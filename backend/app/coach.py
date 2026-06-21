"""Coach report generation.

If ANTHROPIC_API_KEY is set we ask Claude to write the final coaching summary
from STRUCTURED data only (detected moments, evidence, similar memories,
pattern counts) -- never raw demo files. Otherwise we use a deterministic
fallback generator so the app always works offline.
"""
from __future__ import annotations

import os
from collections import Counter
from typing import List, Optional, Tuple

from .models import CoachReport, DecisionMoment, SimilarMemoryItem

MISTAKE_LABELS = {
    "passive_response_to_utility": "passive response to enemy utility",
    "isolated_death": "isolated / untradeable deaths",
    "early_overrotation": "early over-rotation before bomb confirmation",
    "utility_inefficiency": "dying with unused utility",
}

DRILL_LIBRARY = {
    "passive_response_to_utility": [
        "Play 10 Mirage T rounds where early A utility automatically triggers a mid-control call.",
        "Set a personal rule: if a choke is blocked for >8s, call mid control, reset, or pressure the opposite side.",
    ],
    "isolated_death": [
        "Review every first death and ask whether a teammate could have traded you.",
        "Run trade-fragging drills: never peek a contested angle outside ~1000 units of a teammate.",
    ],
    "early_overrotation": [
        "Practice holding your site until you hear a bomb plant or get a clear teammate call.",
        "Watch 5 rounds of your demos focusing only on rotation timing vs. bomb info.",
    ],
    "utility_inefficiency": [
        "Before each execute, choose one piece of utility you MUST use before taking contact.",
        "End-of-round check: review any round where you died with full nades.",
    ],
}


def _pattern_counts(moments: List[DecisionMoment]) -> Counter:
    return Counter(m.mistake_type for m in moments)


def build_drills(moments: List[DecisionMoment]) -> List[str]:
    counts = _pattern_counts(moments)
    drills: List[str] = []
    for mistake_type, _ in counts.most_common():
        drills.extend(DRILL_LIBRARY.get(mistake_type, []))
    # de-dup while preserving order
    seen = set()
    out = []
    for d in drills:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out[:6]


def _fallback_summary(
    moments: List[DecisionMoment],
    similar_memory: List[SimilarMemoryItem],
) -> str:
    if not moments:
        return "No clear decision mistakes detected in this demo. Keep playing and re-analyze to build a memory of patterns."

    counts = _pattern_counts(moments)
    top_type, top_n = counts.most_common(1)[0]
    label = MISTAKE_LABELS.get(top_type, top_type)

    lines: List[str] = []
    lines.append(
        f"Your biggest recurring issue is {label} ({top_n} occurrence(s) this demo)."
    )

    if top_type == "passive_response_to_utility":
        lines.append(
            "When the enemy blocks your first path with early utility, you tend to wait instead of "
            "taking alternate map control. Practice rule: if a choke is blocked for more than 8 seconds, "
            "call for mid control, reset, or pressure the opposite side."
        )
    elif top_type == "isolated_death":
        lines.append(
            "You repeatedly take duels with no teammate in trade range and no flash support. "
            "Stay connected to your team and demand a flash before peeking contested angles."
        )
    elif top_type == "early_overrotation":
        lines.append(
            "You rotate off your site before the bomb is confirmed, leaving it open to fakes. "
            "Hold until you have real information, then rotate with your team."
        )
    elif top_type == "utility_inefficiency":
        lines.append(
            "You keep dying with grenades unused. Spend your utility to win duels instead of saving it for a moment that never comes."
        )

    other = [MISTAKE_LABELS.get(t, t) for t, _ in counts.most_common()[1:]]
    if other:
        lines.append("Secondary patterns to watch: " + ", ".join(other) + ".")

    if similar_memory:
        recurring = similar_memory[0]
        lines.append(
            f"Redis memory shows this echoes a past mistake ({recurring.mistake_type}) — "
            "this is a pattern, not a one-off. Prioritize fixing it."
        )

    return " ".join(lines)


def _build_structured_prompt(
    moments: List[DecisionMoment],
    similar_memory: List[SimilarMemoryItem],
) -> Tuple[str, str]:
    counts = _pattern_counts(moments)
    pattern_lines = "\n".join(f"- {MISTAKE_LABELS.get(t, t)}: {n}" for t, n in counts.most_common())
    moment_lines = "\n".join(
        f"- [{m.mistake_type}] {m.summary_text} | enemy: {m.enemy_action} | you: {m.user_response} "
        f"| outcome: {m.outcome} | evidence: {'; '.join(m.evidence)}"
        for m in moments
    )
    mem_lines = "\n".join(
        f"- [{s.mistake_type}] {s.summary_text} (similarity {s.similarity})" for s in similar_memory
    ) or "- (no prior memory yet)"

    system = (
        "You are a concise, practical Counter-Strike 2 decision coach. You analyze a player's OWN "
        "demos and tell them what they should have done differently given what the enemy did. "
        "Be direct and actionable. 4-6 sentences. No fluff, no grades, no scores."
    )
    user = (
        "Structured data only (no raw demo). Write a final_coaching_summary.\n\n"
        f"Pattern counts:\n{pattern_lines}\n\n"
        f"Detected decision moments:\n{moment_lines}\n\n"
        f"Similar past mistakes from memory:\n{mem_lines}\n"
    )
    return system, user


def generate_coach_summary(
    moments: List[DecisionMoment],
    similar_memory: List[SimilarMemoryItem],
) -> Tuple[str, bool]:
    """Return (summary_text, used_llm)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _fallback_summary(moments, similar_memory), False

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        system, user = _build_structured_prompt(moments, similar_memory)
        # Default to the latest capable Claude model for hackathon quality.
        model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-8")
        resp = client.messages.create(
            model=model,
            max_tokens=500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
        return text.strip() or _fallback_summary(moments, similar_memory), True
    except Exception as exc:  # noqa: BLE001 - never block MVP on the LLM
        print(f"[coach] Anthropic call failed, using fallback: {exc}")
        return _fallback_summary(moments, similar_memory), False


def build_report(
    report_id: str,
    player_id: str,
    demo_id: str,
    parser_mode: str,
    map_name: str,
    moments: List[DecisionMoment],
    similar_memory: List[SimilarMemoryItem],
    analyzed_player: Optional[str] = None,
) -> CoachReport:
    summary, _used_llm = generate_coach_summary(moments, similar_memory)
    drills = build_drills(moments)
    return CoachReport(
        report_id=report_id,
        player_id=player_id,
        demo_id=demo_id,
        parser_mode=parser_mode,
        map=map_name,
        analyzed_player=analyzed_player,
        moments=moments,
        similar_memory=similar_memory,
        final_coaching_summary=summary,
        drills=drills,
    )
