"""Tests for real .dem parsing, the upload endpoint, and Redis memory.

Runnable two ways:
    pytest backend/tests/test_real_parser.py        # if pytest is installed
    python backend/tests/test_real_parser.py        # standalone runner

The full real-demo test only runs when CS2_TEST_DEMO points at a .dem file
(it needs a real binary demo, which we don't commit). Everything else runs
unconditionally — the upload error paths and the Redis layer (via fakeredis).
"""
from __future__ import annotations

import io
import os
import sys

# Allow `python backend/tests/test_real_parser.py` from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.real_parser import RealDemParser, _f, _i, _to_region  # noqa: E402


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_numeric_coercion_handles_nan_and_none():
    nan = float("nan")
    assert _f(nan) == 0.0
    assert _f(None) == 0.0
    assert _f("3.5") == 3.5
    assert _i(nan) == 0
    assert _i(2.9) == 2
    assert _i(None, default=7) == 7


def test_region_mapping():
    assert _to_region("BombsiteA") == "A"
    assert _to_region("BombsiteB") == "B"
    assert _to_region("Middle") == "MID"
    assert _to_region("Connector") == "MID"
    assert _to_region("Apartments") == "B"
    assert _to_region("Palace") == "A"
    assert _to_region(None) is None
    assert _to_region("CTSpawn") is None


def test_target_resolution_prefers_name_match():
    p = RealDemParser("x.dem", player_id="local_user", player_name="dog")
    players = {"111": "SomeOne", "222": "Dog", "333": "Doggo"}
    assert p._resolve_target(players) == "222"  # exact (case-insensitive)
    p2 = RealDemParser("x.dem", player_name="ggo")
    assert p2._resolve_target(players) == "333"  # loose substring
    p3 = RealDemParser("x.dem")  # no name -> first player
    assert p3._resolve_target(players) == "111"


# ---------------------------------------------------------------------------
# Upload endpoint error handling (no real demo needed)
# ---------------------------------------------------------------------------
def _client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


def _post(c, name, data: bytes, player_id="test"):
    return c.post(
        "/api/analyze/upload",
        files={"file": (name, io.BytesIO(data), "application/octet-stream")},
        data={"player_id": player_id},
    )


def test_upload_rejects_non_demo_bytes():
    c = _client()
    r = _post(c, "x.dem", b"this is not a demo at all")
    assert r.status_code == 400
    assert "not a CS2 demo" in r.json()["detail"]


def test_upload_rejects_empty_and_unsupported():
    c = _client()
    assert _post(c, "x.dem", b"").status_code == 400
    bad_ext = c.post(
        "/api/analyze/upload",
        files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")},
        data={"player_id": "t"},
    )
    assert bad_ext.status_code == 400
    assert "Unsupported file type" in bad_ext.json()["detail"]


def test_upload_rejects_truncated_demo():
    # Correct magic, but far too short to parse -> clean 400, no server crash.
    c = _client()
    r = _post(c, "x.dem", b"PBDEMS2\x00short-garbage")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Redis memory layer (fakeredis -> brute-force similarity path)
# ---------------------------------------------------------------------------
def test_redis_store_and_retrieve_with_fakeredis():
    import fakeredis

    from app import redis_store
    from app.models import DecisionMoment

    client = fakeredis.FakeStrictRedis()
    redis_store._vector_index_ready = False  # fakeredis has no RediSearch

    def moment(mid, text, mtype="isolated_death"):
        return DecisionMoment(
            moment_id=mid, player_id="p1", demo_id="d", round_id="r1", map="de_mirage",
            side="T", timestamp_seconds=10.0, enemy_action="e", user_response="u",
            outcome="o", mistake_type=mtype, evidence=["x"], recommended_response="r",
            confidence=0.8, summary_text=text,
        )

    redis_store.store_moment(client, moment("m1", "isolated death in mid no flash support"))
    redis_store.store_moment(client, moment("m2", "passive response to enemy smoke on A ramp", "passive_response_to_utility"))

    mem = redis_store.get_player_memory(client, "p1")
    assert len(mem) == 2

    # A query close to m1 should rank m1 above m2.
    sims = redis_store.search_similar_moments(client, "p1", "isolated death mid no flash", top_k=5)
    assert sims, "expected at least one similar moment"
    assert sims[0].moment_id == "m1"

    # exclude_ids removes a moment from results.
    sims_excl = redis_store.search_similar_moments(
        client, "p1", "isolated death mid", top_k=5, exclude_ids=["m1"]
    )
    assert all(s.moment_id != "m1" for s in sims_excl)


# ---------------------------------------------------------------------------
# Full real-demo parse (only when a demo path is provided)
# ---------------------------------------------------------------------------
def test_real_demo_parse_if_available():
    demo_path = os.environ.get("CS2_TEST_DEMO")
    if not demo_path or not os.path.exists(demo_path):
        print("  (skipped: set CS2_TEST_DEMO to a .dem path to run)")
        return

    from app.detectors import run_detectors

    demo = RealDemParser(demo_path, player_id="local_user").parse()
    assert demo.parser_mode == "real_dem_parser"
    assert demo.map and demo.map != "unknown"
    assert demo.analyzed_player
    assert len(demo.rounds) > 0
    for rnd in demo.rounds:
        assert rnd.side in ("T", "CT")
        assert rnd.round_winner in ("T", "CT", "unknown")
        assert rnd.player_summary is not None
    # Detectors must run cleanly on the real output.
    moments = run_detectors(demo)
    for m in moments:
        assert m.summary_text and m.mistake_type


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------
def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
