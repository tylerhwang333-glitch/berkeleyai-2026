"""FastAPI entrypoint for the CS2 Decision Coach.

Pipeline:
  parsed demo -> detectors -> store in Redis -> retrieve similar -> coach report
"""
from __future__ import annotations

import hashlib
import uuid
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import arize_tracing, coach, redis_store
from .detectors import run_detectors
from .models import AnalyzeSampleRequest, CoachReport, DecisionMoment, ParsedDemo, SimilarMemoryItem
from .parser import JsonUploadParser, MockDemParser, SampleFixtureParser

app = FastAPI(title="CS2 Decision Coach", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # hackathon: open CORS
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single shared Redis client; may be None if Redis is unreachable.
_redis = redis_store.connect_redis()


@app.on_event("startup")
def _startup() -> None:
    global _redis
    if _redis is None:
        _redis = redis_store.connect_redis()
    redis_store.ensure_indexes(_redis)


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------
def _run_pipeline(demo: ParsedDemo) -> CoachReport:
    arize_tracing.trace_pipeline_start(
        {"demo_id": demo.demo_id, "parser_mode": demo.parser_mode, "player_id": demo.player_id, "rounds": len(demo.rounds)}
    )

    # 1. Detect decision moments.
    moments: List[DecisionMoment] = run_detectors(demo)
    arize_tracing.trace_detected_moments(moments)

    # 2. Retrieve similar PRIOR memories (before storing the new ones) so we
    #    surface genuine recurring patterns from past analyses.
    new_ids = [m.moment_id for m in moments]
    combined_summary = " ".join(m.summary_text for m in moments) or "cs2 round"
    similar: List[SimilarMemoryItem] = redis_store.search_similar_moments(
        _redis, demo.player_id, combined_summary, top_k=5, exclude_ids=new_ids
    )
    arize_tracing.trace_redis_retrieval(similar)

    # 3. Store the new moments in Redis memory.
    for m in moments:
        redis_store.store_moment(_redis, m)

    # 4. Generate the coach report.
    report_id = uuid.uuid4().hex[:12]
    report = coach.build_report(
        report_id=report_id,
        player_id=demo.player_id,
        demo_id=demo.demo_id,
        parser_mode=demo.parser_mode,
        map_name=demo.map,
        moments=moments,
        similar_memory=similar,
    )

    # 5. Persist report + trace + eval.
    redis_store.store_report(_redis, report)
    arize_tracing.trace_coach_output(report)
    arize_tracing.evaluate_groundedness(report)

    return report


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    redis_ok = False
    if _redis is not None:
        try:
            redis_ok = bool(_redis.ping())
        except Exception:  # noqa: BLE001
            redis_ok = False
    return {
        "status": "ok",
        "redis_connected": redis_ok,
        "vector_index": redis_store._vector_index_ready,
    }


@app.post("/api/analyze/sample", response_model=CoachReport)
def analyze_sample(req: AnalyzeSampleRequest) -> CoachReport:
    demo = SampleFixtureParser(player_id=req.player_id or "local_user").parse()
    return _run_pipeline(demo)


@app.post("/api/analyze/upload", response_model=CoachReport)
async def analyze_upload(
    file: UploadFile = File(...),
    player_id: str = Form("local_user"),
) -> CoachReport:
    player_id = player_id or "local_user"
    filename = file.filename or "upload"
    contents = await file.read()
    lower = filename.lower()

    if lower.endswith(".json"):
        try:
            demo = JsonUploadParser(contents, player_id=player_id).parse()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Could not parse uploaded JSON as a parsed demo: {exc}")
    elif lower.endswith(".dem"):
        # TODO(real-parser): swap MockDemParser for a demoparser2/awpy-backed
        # RealDemParser that decodes the actual .dem bytes.
        demo = MockDemParser(filename, player_id=player_id).parse()
    else:
        # Unknown extension: treat as a .dem-style upload via the mock parser so
        # we never crash. Mark it clearly.
        demo = MockDemParser(filename, player_id=player_id).parse()

    return _run_pipeline(demo)


@app.get("/api/player/{player_id}/memory")
def player_memory(player_id: str) -> dict:
    moments = redis_store.get_player_memory(_redis, player_id)
    return {
        "player_id": player_id,
        "count": len(moments),
        "moments": [m.model_dump() for m in moments],
    }
