"""FastAPI entrypoint for the CS2 Decision Coach.

Pipeline:
  parsed demo -> detectors -> store in Redis -> retrieve similar -> coach report
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import BinaryIO, List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import arize_tracing, coach, map_zones, redis_store
from .detectors import run_detectors
from .models import (
    AnalyzeSampleRequest,
    CoachReport,
    DecisionMoment,
    ParsedDemo,
    RoundView,
    SimilarMemoryItem,
)
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

# Serve map radar images (and any other static assets) from the repo's
# public/assets directory so the frontend can render them at /assets/<file>.
# Path: backend/app/main.py -> parents[2] == repo root.
_ASSETS_DIR = Path(__file__).resolve().parents[2] / "public" / "assets"
if _ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

# Single shared Redis client; may be None if Redis is unreachable. Connected in
# a startup background thread (NOT at import) so a slow/dead Redis can never delay
# uvicorn binding the port — otherwise the connect retries run before the socket
# is listening and nginx returns 502 for the whole startup window.
_redis = None


def _connect_redis_async() -> None:
    global _redis
    client = redis_store.connect_redis()
    if client is not None:
        redis_store.ensure_indexes(client)
        _redis = client


@app.on_event("startup")
def _startup() -> None:
    # Daemon thread: the API serves immediately (memory features switch on once
    # Redis connects). If Redis is down the app keeps working without memory.
    threading.Thread(target=_connect_redis_async, daemon=True).start()


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------
def _run_pipeline(demo: ParsedDemo) -> CoachReport:
    arize_tracing.trace_pipeline_start(
        {"demo_id": demo.demo_id, "parser_mode": demo.parser_mode, "player_id": demo.player_id, "rounds": len(demo.rounds)}
    )

    # 0. Resolve deterministic canonical map zones BEFORE anything reads
    #    locations. This pins the coach to a fixed vocabulary so it can never
    #    hallucinate a callout from raw coordinates. Degrades to "Unknown".
    map_zones.annotate_zones(demo)

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

    # 5. Persist report + trace + eval. (Stored BEFORE attaching the heavy
    #    visualization payload below, so Redis only keeps the analysis.)
    redis_store.store_report(_redis, report)
    arize_tracing.trace_coach_output(report)
    arize_tracing.evaluate_groundedness(report)

    # 6. Attach the map-visualization payload for the client: the radar image
    #    descriptor + every round's events, each carrying a position snapshot.
    report.map_radar = map_zones.radar_descriptor(demo.map)
    report.rounds = [
        RoundView(
            round_id=rnd.round_id,
            round_number=rnd.round_number,
            side=rnd.side,
            round_winner=rnd.round_winner,
            bombsite=rnd.bombsite,
            bomb=rnd.bomb,
            events=rnd.events,
        )
        for rnd in demo.rounds
    ]

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
def analyze_upload(
    file: UploadFile = File(...),
    player_id: str = Form("local_user"),
    player_name: str = Form(""),
) -> CoachReport:
    """Analyze an uploaded demo.

    * ``.json`` -> treated as already-parsed fixture data.
    * ``.dem``  -> decoded for real via demoparser2 (RealDemParser). Pass
      ``player_name`` to choose which player in the demo to coach; otherwise the
      first player is analyzed.

    Defined as a SYNC ``def`` on purpose: decoding a real .dem is heavy, blocking
    native (Rust/pyo3) work, and ``_run_pipeline`` makes blocking Redis/Anthropic
    calls. A sync endpoint runs in FastAPI's threadpool, so it never freezes the
    event loop — health checks keep responding and one big upload can't wedge the
    whole worker (which otherwise surfaces to the client as a 502).
    """
    player_id = player_id or "local_user"
    player_name = (player_name or "").strip()
    filename = file.filename or "upload"
    lower = filename.lower()

    if lower.endswith(".json"):
        contents = file.file.read()
        try:
            demo = JsonUploadParser(contents, player_id=player_id).parse()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail=f"Could not parse uploaded JSON as a parsed demo: {exc}")
    elif lower.endswith(".dem"):
        demo = _parse_dem_upload(file.file, filename, player_id, player_name)
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. Upload a CS2 .dem file or an already-parsed .json.",
        )

    return _run_pipeline(demo)


def _parse_dem_upload(src: BinaryIO, filename: str, player_id: str, player_name: str) -> ParsedDemo:
    """Stream the uploaded demo to a temp .dem and decode it.

    ``src`` is the SpooledTemporaryFile behind the UploadFile. We copy it to a
    temp file in chunks instead of reading the whole (often 100-300 MB) demo into
    a single bytes object, which keeps peak memory low and avoids OOM-killing the
    worker on a memory-constrained instance.
    """
    # CS2 (Source 2) demos start with the magic "PBDEMS2"; CS:GO demos with
    # "HL2DEMO". Reject obvious non-demos before touching disk / the native parser.
    head = src.read(8)
    if not head:
        raise HTTPException(status_code=400, detail="Uploaded .dem file is empty.")
    if not head.startswith((b"PBDEMS2", b"HL2DEMO")):
        raise HTTPException(
            status_code=400,
            detail="That file is not a CS2 demo (missing PBDEMS2 header). Upload a real .dem.",
        )

    if _USE_MOCK_DEM:
        return MockDemParser(filename, player_id=player_id).parse()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".dem", delete=False) as tmp:
            tmp.write(head)
            shutil.copyfileobj(src, tmp, length=1024 * 1024)  # stream rest in 1 MB chunks
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
