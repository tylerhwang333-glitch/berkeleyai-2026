"""Minimal Arize-style observability / trust stub.

This logs structured JSON spans to the console and returns simple eval scores.
It does NOT require external Arize credentials.

TODO(arize): Replace these console logs with real Arize / Phoenix tracing:
  - Use arize-otel / openinference instrumentation to emit spans.
  - Log the LLM prompt + response as a span with token usage.
  - Push groundedness / relevance evals to an Arize project.
  See https://docs.arize.com and https://github.com/Arize-ai/phoenix
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .models import CoachReport, DecisionMoment, SimilarMemoryItem


def _log(span: str, payload: Dict[str, Any]) -> None:
    print(f"[arize_trace] {json.dumps({'span': span, **payload}, default=str)}")


def trace_pipeline_start(payload: Dict[str, Any]) -> None:
    _log("pipeline_start", payload)


def trace_detected_moments(moments: List[DecisionMoment]) -> None:
    _log(
        "detected_moments",
        {
            "count": len(moments),
            "mistake_types": [m.mistake_type for m in moments],
        },
    )


def trace_redis_retrieval(results: List[SimilarMemoryItem]) -> None:
    _log(
        "redis_retrieval",
        {
            "count": len(results),
            "top_similarity": results[0].similarity if results else None,
        },
    )


def trace_coach_output(report: CoachReport) -> None:
    _log(
        "coach_output",
        {
            "report_id": report.report_id,
            "summary_chars": len(report.final_coaching_summary),
            "num_drills": len(report.drills),
        },
    )


def evaluate_groundedness(report: CoachReport) -> Dict[str, Any]:
    """Cheap heuristic 'groundedness' eval: does the coaching summary reference
    mistake types that were actually detected? Returns simple eval scores.

    TODO(arize): swap this heuristic for a real LLM-as-judge groundedness eval.
    """
    summary = report.final_coaching_summary.lower()
    detected_types = {m.mistake_type for m in report.moments}

    referenced = 0
    keyword_map = {
        "passive_response_to_utility": ["utility", "wait", "choke", "control"],
        "isolated_death": ["isolated", "trade", "teammate", "alone"],
        "early_overrotation": ["rotate", "rotation", "site", "confirm"],
        "utility_inefficiency": ["unused", "nade", "grenade", "utility"],
    }
    for t in detected_types:
        if any(kw in summary for kw in keyword_map.get(t, [])):
            referenced += 1

    groundedness = round(referenced / len(detected_types), 3) if detected_types else 1.0
    coverage = round(len(detected_types) / 4.0, 3)  # 4 detector categories

    scores = {
        "groundedness": groundedness,
        "detector_coverage": coverage,
        "has_actionable_drills": bool(report.drills),
        "num_moments": len(report.moments),
    }
    _log("groundedness_eval", scores)
    return scores
