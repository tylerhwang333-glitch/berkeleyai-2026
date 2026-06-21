"""Rule-based decision-mistake detectors.

Each detector inspects RoundFacts and, when its rule fires, emits a
DecisionMoment with human-readable evidence, a recommended response, a
confidence score and a one-line summary.

These rules are intentionally simple and hackathon-practical. They operate on
the structured PlayerRoundSummary + events, never on raw demo bytes.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from .map_zones import UNKNOWN_ZONE
from .models import DecisionMoment, GameEvent, ParsedDemo, RoundFacts

# Thresholds (tweakable hackathon constants)
PASSIVE_WAIT_SECONDS = 15.0
ISOLATED_DISTANCE = 1000.0
BLOCKING_UTILITY = {"smoke", "molotov", "incendiary"}
USABLE_UTILITY = {"flash", "smoke", "molotov", "incendiary", "he", "grenade"}


def _moment_id(round_id: str, mistake_type: str, demo_id: str) -> str:
    raw = f"{demo_id}:{round_id}:{mistake_type}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _pick_zone(*candidates: Optional[str]) -> str:
    """First meaningful canonical zone among the candidates, else 'Unknown'."""
    for c in candidates:
        if c and c != UNKNOWN_ZONE:
            return c
    return UNKNOWN_ZONE


def _find_enemy_utility_event(rnd: RoundFacts) -> Optional[GameEvent]:
    for ev in rnd.events:
        if ev.actor == "enemy" and ev.event_type in BLOCKING_UTILITY:
            return ev
    return None


def detect_passive_response_to_utility(rnd: RoundFacts, demo_id: str, player_id: str) -> Optional[DecisionMoment]:
    """Enemy utility blocks the player's choke and the player freezes there."""
    summary = rnd.player_summary
    util_event = _find_enemy_utility_event(rnd)
    waited = summary.waited_after_enemy_utility_seconds

    if util_event is None or waited is None:
        return None
    if waited <= PASSIVE_WAIT_SECONDS:
        return None
    if summary.alternate_map_control_taken:
        return None

    location = util_event.location or summary.primary_area or "the choke"
    enemy_action = f"Enemy used {util_event.event_type} on {location} at {util_event.timestamp_seconds:.0f}s"
    user_response = f"Player held outside {location} for {waited:.0f}s without taking alternate control"
    outcome = "Lost map control; round stalled into a late/failed action" if rnd.round_winner != rnd.player_team else "Stalled but survived"

    zone = _pick_zone(util_event.zone, summary.primary_zone)
    evidence = [
        f"Canonical zone: {zone}",
        f"{util_event.event_type.title()} thrown on {location} at {util_event.timestamp_seconds:.0f}s",
        f"Waited {waited:.0f}s near the blocked choke (threshold {PASSIVE_WAIT_SECONDS:.0f}s)",
        "No alternate map-control event recorded this round",
    ]
    confidence = min(0.95, 0.6 + (waited - PASSIVE_WAIT_SECONDS) / 60.0)
    recommended = (
        f"When early utility stalls {location}, don't freeze in the choke. "
        "Take mid control, reset, or set up a late split instead of waiting it out."
    )
    summary_text = f"Passive response to enemy {util_event.event_type} on {location}: waited {waited:.0f}s instead of taking alternate control."

    return DecisionMoment(
        moment_id=_moment_id(rnd.round_id, "passive_response_to_utility", demo_id),
        player_id=player_id,
        demo_id=demo_id,
        round_id=rnd.round_id,
        map=rnd.map,
        side=rnd.side,
        zone=zone,
        timestamp_seconds=util_event.timestamp_seconds,
        enemy_action=enemy_action,
        user_response=user_response,
        outcome=outcome,
        mistake_type="passive_response_to_utility",
        evidence=evidence,
        recommended_response=recommended,
        confidence=round(confidence, 2),
        summary_text=summary_text,
    )


def detect_isolated_death(rnd: RoundFacts, demo_id: str, player_id: str) -> Optional[DecisionMoment]:
    """Player dies far from teammates with no flash support to trade."""
    summary = rnd.player_summary
    if summary.survived:
        return None
    dist = summary.nearest_teammate_distance_on_death
    if dist is None or dist <= ISOLATED_DISTANCE:
        return None
    if summary.had_flash_support_before_death:
        return None

    location = summary.death_location or summary.primary_area or "an isolated area"
    enemy_action = f"Enemy held an angle near {location}"
    user_response = f"Player engaged {location} alone (~{dist:.0f} units from nearest teammate)"
    outcome = "Died without trade potential; team plays a man down"

    zone = _pick_zone(summary.death_zone, summary.primary_zone)
    evidence = [
        f"Canonical zone: {zone}",
        f"Death at {summary.death_time_seconds:.0f}s in {location}" if summary.death_time_seconds is not None else f"Died in {location}",
        f"Nearest teammate ~{dist:.0f} units away (threshold {ISOLATED_DISTANCE:.0f})",
        "No flash support before death (untradeable)",
    ]
    confidence = min(0.95, 0.6 + (dist - ISOLATED_DISTANCE) / 2000.0)
    recommended = (
        "Don't take isolated duels. Stay within trade range of a teammate and "
        "ask for a flash before peeking contested angles."
    )
    summary_text = f"Isolated death in {location}: ~{dist:.0f} units from team and no flash support."

    return DecisionMoment(
        moment_id=_moment_id(rnd.round_id, "isolated_death", demo_id),
        player_id=player_id,
        demo_id=demo_id,
        round_id=rnd.round_id,
        map=rnd.map,
        side=rnd.side,
        zone=zone,
        timestamp_seconds=summary.death_time_seconds or 0.0,
        enemy_action=enemy_action,
        user_response=user_response,
        outcome=outcome,
        mistake_type="isolated_death",
        evidence=evidence,
        recommended_response=recommended,
        confidence=round(confidence, 2),
        summary_text=summary_text,
    )


def detect_early_overrotation(rnd: RoundFacts, demo_id: str, player_id: str) -> Optional[DecisionMoment]:
    """Player rotates off their area before the bomb is confirmed, and the
    original area gets pressured / the round is lost."""
    summary = rnd.player_summary
    if not (summary.rotated_from and summary.rotated_to and summary.rotation_time_seconds is not None):
        return None

    # Was the bomb actually confirmed before the rotation time?
    bomb_confirmed_before_rotation = any(
        ev.event_type == "bomb_plant" and ev.timestamp_seconds <= summary.rotation_time_seconds
        for ev in rnd.events
    )
    if bomb_confirmed_before_rotation:
        return None

    # Did the original area later receive pressure, or did we lose the round?
    pressure_after = any(
        ev.event_type in {"enemy_pressure", "bomb_plant"}
        and ev.timestamp_seconds > summary.rotation_time_seconds
        and (summary.rotated_from in (ev.location or "") or (ev.location or "").startswith(summary.rotated_from))
        for ev in rnd.events
    )
    round_lost = rnd.round_winner != rnd.player_team
    if not (pressure_after or round_lost):
        return None

    enemy_action = f"Enemy applied pressure to {summary.rotated_from} after the player left"
    user_response = (
        f"Rotated {summary.rotated_from} -> {summary.rotated_to} at "
        f"{summary.rotation_time_seconds:.0f}s before bomb confirmation"
    )
    outcome = "Original site fell / round lost due to premature rotation"

    zone = _pick_zone(summary.primary_zone, summary.death_zone)
    evidence = [
        f"Canonical zone: {zone}",
        f"Rotation {summary.rotated_from} -> {summary.rotated_to} at {summary.rotation_time_seconds:.0f}s",
        "No bomb plant confirmed before the rotation",
        "Enemy pressure on the abandoned site" if pressure_after else "Round was lost after the rotation",
    ]
    recommended = (
        "Hold your site until the bomb is confirmed or you have a clear read. "
        "Rotating on one piece of utility leaves your area open to a fake."
    )
    summary_text = (
        f"Early over-rotation {summary.rotated_from}->{summary.rotated_to} before bomb confirmation; "
        f"{summary.rotated_from} got punished."
    )

    return DecisionMoment(
        moment_id=_moment_id(rnd.round_id, "early_overrotation", demo_id),
        player_id=player_id,
        demo_id=demo_id,
        round_id=rnd.round_id,
        map=rnd.map,
        side=rnd.side,
        zone=zone,
        timestamp_seconds=summary.rotation_time_seconds,
        enemy_action=enemy_action,
        user_response=user_response,
        outcome=outcome,
        mistake_type="early_overrotation",
        evidence=evidence,
        recommended_response=recommended,
        confidence=0.7,
        summary_text=summary_text,
    )


def detect_utility_inefficiency(rnd: RoundFacts, demo_id: str, player_id: str) -> Optional[DecisionMoment]:
    """Player dies with unused utility still in inventory."""
    summary = rnd.player_summary
    if summary.survived:
        return None
    unused = [u for u in summary.utility_unused_on_death if u in USABLE_UTILITY]
    if not unused:
        return None

    location = summary.death_location or summary.primary_area or "site"
    enemy_action = "Enemy took the duel on even terms"
    user_response = f"Died in {location} with unused utility: {', '.join(unused)}"
    outcome = "Wasted utility value; weaker duel than necessary"

    zone = _pick_zone(summary.death_zone, summary.primary_zone)
    evidence = [
        f"Canonical zone: {zone}",
        f"Unused on death: {', '.join(unused)}",
        f"Death in {location}" + (f" at {summary.death_time_seconds:.0f}s" if summary.death_time_seconds is not None else ""),
    ]
    confidence = min(0.9, 0.55 + 0.12 * len(unused))
    recommended = (
        "Use your utility before contact. Pre-fire a flash or commit a smoke/molly "
        "to win the duel rather than dying with nades in the bank."
    )
    summary_text = f"Utility inefficiency: died in {location} holding {', '.join(unused)}."

    return DecisionMoment(
        moment_id=_moment_id(rnd.round_id, "utility_inefficiency", demo_id),
        player_id=player_id,
        demo_id=demo_id,
        round_id=rnd.round_id,
        map=rnd.map,
        side=rnd.side,
        zone=zone,
        timestamp_seconds=summary.death_time_seconds or 0.0,
        enemy_action=enemy_action,
        user_response=user_response,
        outcome=outcome,
        mistake_type="utility_inefficiency",
        evidence=evidence,
        recommended_response=recommended,
        confidence=round(confidence, 2),
        summary_text=summary_text,
    )


ALL_DETECTORS = [
    detect_passive_response_to_utility,
    detect_isolated_death,
    detect_early_overrotation,
    detect_utility_inefficiency,
]


def run_detectors(demo: ParsedDemo) -> List[DecisionMoment]:
    """Run every detector over every round and collect the firing moments."""
    moments: List[DecisionMoment] = []
    for rnd in demo.rounds:
        for detector in ALL_DETECTORS:
            moment = detector(rnd, demo.demo_id, demo.player_id)
            if moment is not None:
                moments.append(moment)
    return moments
