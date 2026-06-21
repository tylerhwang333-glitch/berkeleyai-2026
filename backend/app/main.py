"""FastAPI entrypoint for the CS2 Decision Coach.

Pipeline:
  parsed demo -> detectors -> store in Redis -> retrieve similar -> coach report
"""
from __future__ import annotations

import os
import tempfile
import uuid
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from . import arize_tracing, coach, redis_store
from .detectors import run_detectors
from .models import AnalyzeSampleRequest, CoachReport, DecisionMoment, ParsedDemo, SimilarMemoryItem
from .parser import JsonUploadParser, MockDemParser, SampleFixtureParser
from .real_parser import DemoParseError, RealDemParser

# Set USE_MOCK_DEM_PARSER=1 to bypass the real demoparser2-backed parser (e.g.
# in environments where the native wheel is unavailable). Defaults to real.
_USE_MOCK_DEM = os.environ.get("USE_MOCK_DEM_PARSER", "").lower() in ("1", "true", "yes")

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
        analyzed_player=demo.analyzed_player,
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
    player_name: str = Form(""),
) -> CoachReport:
    """Analyze an uploaded demo.

    * ``.json`` -> treated as already-parsed fixture data.
    * ``.dem``  -> decoded for real via demoparser2 (RealDemParser). Pass
      ``player_name`` to choose which player in the demo to coach; otherwise the
      first player is analyzed.
    """
    player_id = player_id or "local_user"
    player_name = (player_name or "").strip()
    filename = file.filename or "upload"
    contents = await file.read()
    lower = filename.lower()

    if lower.endswith(".json"):
        try:
            demo = JsonUploadParser(contents, player_id=player_id).parse()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Could not parse uploaded JSON as a parsed demo: {exc}")
    elif lower.endswith(".dem"):
        demo = _parse_dem_upload(contents, filename, player_id, player_name)
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload a CS2 .dem file or an already-parsed .json.",
        )

    return _run_pipeline(demo)


def _parse_dem_upload(contents: bytes, filename: str, player_id: str, player_name: str) -> ParsedDemo:
    """Persist the uploaded bytes to a temp .dem and decode it."""
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded .dem file is empty.")

    # CS2 (Source 2) demos start with the magic "PBDEMS2"; CS:GO demos with
    # "HL2DEMO". Reject obvious non-demos before invoking the native parser.
    if not contents[:8].startswith((b"PBDEMS2", b"HL2DEMO")):
        raise HTTPException(
            status_code=400,
            detail="That file is not a CS2 demo (missing PBDEMS2 header). Upload a real .dem.",
        )

    if _USE_MOCK_DEM:
        return MockDemParser(filename, player_id=player_id).parse()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dem", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name
        safe_name = filename.rsplit("/", 1)[-1].replace(".dem", "") or "uploaded_demo"
        parser = RealDemParser(
            tmp_path,
            player_id=player_id,
            player_name=player_name or None,
            demo_id=f"dem_{safe_name}",
        )
        return parser.parse()
    except DemoParseError as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse .dem file: {exc}")
    except HTTPException:
        raise
    except BaseException as exc:  # noqa: BLE001
        # Guard against pyo3 PanicException (subclasses BaseException) from the
        # native parser on truncated/corrupt demos.
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise HTTPException(status_code=400, detail=f"Unexpected error parsing .dem file: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


@app.get("/api/player/{player_id}/memory")
def player_memory(player_id: str) -> dict:
    moments = redis_store.get_player_memory(_redis, player_id)
    return {
        "player_id": player_id,
        "count": len(moments),
        "moments": [m.model_dump() for m in moments],
    }
