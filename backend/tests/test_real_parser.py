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

from app.real_parser import (  # noqa: E402
    RealDemParser,
    TEAM_CT,
    TEAM_T,
    _f,
    _i,
    _to_region,
    _winner_team,
)
from app import map_zones  # noqa: E402


# ---------------------------------------------------------------------------
# Deterministic map-zone resolver
# ---------------------------------------------------------------------------
def test_zone_from_callout_maps_to_canonical_labels():
    z = map_zones.zone_from_callout
    assert z("de_mirage", "A ramp") == "A Ramp"
    assert z("de_mirage", "A site") == "A Site"
    assert z("de_mirage", "BombsiteB") == "B Site"
    assert z("de_mirage", "Catwalk") == "Catwalk"
    assert z("de_mirage", "CTSpawn") == "CT Spawn"
    assert z("de_mirage", "TSpawn") == "T Spawn"
    # map name without the de_ prefix still resolves
    assert z("mirage", "Palace") == "Palace"
    # unknown callout / unknown map / no input -> safe fallback
    assert z("de_mirage", "SomePlaceThatDoesNotExist") == "Unknown"
    assert z("de_dust2", "A site") == "Unknown"
    assert z("de_mirage", None) == "Unknown"


def test_resolve_map_zone_falls_back_to_unknown_without_nav_or_regions():
    # No awpy nav + empty coordinate regions (default) -> Unknown, never raises.
    assert map_zones.resolve_map_zone("de_mirage", 0.0, 0.0, 0.0) == "Unknown"
    assert map_zones.resolve_map_zone("de_mirage", None, None) == "Unknown"
    assert map_zones.resolve_map_zone("de_unsupported", 1.0, 2.0) == "Unknown"
    # camelCase alias points at the same function
    assert map_zones.resolveMapZone is map_zones.resolve_map_zone


def test_point_in_polygon():
    square = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    assert map_zones._point_in_polygon(5.0, 5.0, square) is True
    assert map_zones._point_in_polygon(15.0, 5.0, square) is False


def test_coordinate_regions_resolve_a_raw_point():
    from app.map_data import mirage

    saved = mirage.COORDINATE_REGIONS
    mirage.COORDINATE_REGIONS = [(-100.0, 100.0, -100.0, 100.0, "A Site")]
    try:
        assert map_zones.resolve_map_zone("de_mirage", 0.0, 0.0) == "A Site"
        assert map_zones.resolve_map_zone("de_mirage", 9999.0, 0.0) == "Unknown"
    finally:
        mirage.COORDINATE_REGIONS = saved


def test_nav_index_resolves_zone_from_awpy_geometry():
    """Exercise the awpy 2.0.2 path with a faked Nav (awpy isn't installable on 3.14)."""
    from dataclasses import dataclass

    from app.map_data import mirage

    @dataclass
    class V3:
        x: float
        y: float
        z: float

    class FakeArea:
        def __init__(self, area_id, corners):
            self.area_id = area_id
            self.corners = corners

        @property
        def centroid(self):
            n = len(self.corners)
            return V3(
                sum(c.x for c in self.corners) / n,
                sum(c.y for c in self.corners) / n,
                sum(c.z for c in self.corners) / n,
            )

    class FakeNav:
        def __init__(self, areas):
            self.areas = areas

    # One square area centred at (0,0); label it via a temporary coordinate region.
    area = FakeArea(
        42, [V3(-10, -10, 64), V3(10, -10, 64), V3(10, 10, 64), V3(-10, 10, 64)]
    )
    nav = FakeNav({42: area})

    saved_regions = mirage.COORDINATE_REGIONS
    map_zones._NAV_CACHE.pop("de_mirage", None)
    mirage.COORDINATE_REGIONS = [(-50.0, 50.0, -50.0, 50.0, "Connector")]
    try:
        # Inject the derived index directly (bypasses the awpy import).
        map_zones._NAV_CACHE["de_mirage"] = map_zones._build_area_index("de_mirage", nav)
        # Point inside the area polygon -> area's baked-in zone.
        assert map_zones._zone_from_nav("de_mirage", 0.0, 0.0, 64.0) == "Connector"
        # Nav is tried first by the public resolver.
        assert map_zones.resolve_map_zone("de_mirage", 0.0, 0.0, 64.0) == "Connector"
        # Far-away point (outside polygon + beyond centroid radius) -> Unknown.
        assert map_zones._zone_from_nav("de_mirage", 5000.0, 5000.0, 64.0) == "Unknown"
        # Exact-area-id mapping wins over coordinate regions.
        saved_ids = mirage.NAV_AREA_ID_TO_ZONE
        mirage.NAV_AREA_ID_TO_ZONE = {42: "Catwalk"}
        try:
            map_zones._NAV_CACHE["de_mirage"] = map_zones._build_area_index("de_mirage", nav)
            assert map_zones._zone_from_nav("de_mirage", 0.0, 0.0, 64.0) == "Catwalk"
        finally:
            mirage.NAV_AREA_ID_TO_ZONE = saved_ids
    finally:
        mirage.COORDINATE_REGIONS = saved_regions
        map_zones._NAV_CACHE.pop("de_mirage", None)


def test_resolve_zone_prefers_coords_then_callout():
    # No coords resolvable -> uses the deterministic callout fallback.
    assert map_zones.resolve_zone("de_mirage", None, None, None, place="Connector") == "Connector"
    # Nothing at all -> Unknown.
    assert map_zones.resolve_zone("de_mirage") == "Unknown"


def test_annotate_zones_fills_fixture_from_callouts():
    from app.sample_data import load_sample_parsed_demo

    demo = load_sample_parsed_demo("local_user")
    map_zones.annotate_zones(demo)
    # The bundled Mirage fixture talks about "A ramp" / "A site".
    zones = {ev.zone for rnd in demo.rounds for ev in rnd.events}
    assert "A Ramp" in zones or "A Site" in zones
    for rnd in demo.rounds:
        # Summary zones are always populated (canonical label or "Unknown").
        assert rnd.player_summary.death_zone is not None
        assert rnd.player_summary.primary_zone is not None


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


def test_winner_team_accepts_side_strings_and_numbers():
    # The real bug: demoparser2 reports the winner as a side STRING, which the
    # old numeric-only parse turned into -1 ("unknown") for every round.
    assert _winner_team("CT") == TEAM_CT
    assert _winner_team("t") == TEAM_T
    assert _winner_team("TERRORIST") == TEAM_T
    assert _winner_team("Counter-Terrorist") == TEAM_CT
    assert _winner_team(3) == TEAM_CT and _winner_team(2) == TEAM_T
    assert _winner_team("garbage") == -1
    assert _winner_team(None) == -1
    assert _winner_team(float("nan")) == -1


def test_region_mapping():
    assert _to_region("BombsiteA") == "A"
    assert _to_region("BombsiteB") == "B"
    assert _to_region("Middle") == "MID"
    assert _to_region("Connector") == "MID"
    assert _to_region("Apartments") == "B"
    assert _to_region("Palace") == "A"
    assert _to_region(None) is None
    assert _to_region("CTSpawn") is None


# ---------------------------------------------------------------------------
# Radar coordinate scaling + per-event position snapshots
# ---------------------------------------------------------------------------
def test_world_to_normalized_scales_into_unit_square():
    # de_mirage calibration: pos_x=-3230, pos_y=1713, scale=5, size=1024.
    nx, ny = map_zones.world_to_normalized("de_mirage", -3230.0, 1713.0)
    assert abs(nx - 0.0) < 1e-9 and abs(ny - 0.0) < 1e-9  # top-left corner
    nx, ny = map_zones.world_to_normalized("mirage", -1000.0, 0.0)  # de_ optional
    assert 0.0 <= nx <= 1.0 and 0.0 <= ny <= 1.0
    # Unsupported / missing -> (None, None), never raises.
    assert map_zones.world_to_normalized("de_dust2", 0.0, 0.0) == (None, None)
    assert map_zones.world_to_normalized("de_mirage", None, 0.0) == (None, None)


def test_radar_descriptor_is_none_for_unknown_maps():
    r = map_zones.radar_descriptor("de_mirage")
    assert r is not None and r.image_url.endswith("De_mirage_radar.jpeg")
    assert r.image_url.startswith("/assets/")
    assert map_zones.radar_descriptor("de_nuke") is None


def test_snapshot_at_places_players_and_active_utils():
    from app.real_parser import TEAM_CT, TEAM_T, TICK_RATE

    p = RealDemParser("x.dem")
    p.players = {"1": "Alice", "2": "Bob", "3": "Enemy"}
    exact = {
        1000: [
            {"steamid": "1", "x": -1000, "y": 0, "z": 0, "team": TEAM_T, "place": "Mid", "alive": True},
            {"steamid": "2", "x": -500, "y": 500, "z": 0, "team": TEAM_T, "place": "A", "alive": False},
            {"steamid": "3", "x": 1000, "y": -500, "z": 0, "team": TEAM_CT, "place": "B", "alive": True},
        ]
    }
    detonations = [
        {"tick": 990, "steamid": "3", "x": 800, "y": -400, "z": 0, "util": "smoke"},   # active
        {"tick": 100, "steamid": "3", "x": 0, "y": 0, "z": 0, "util": "flash"},        # expired
        {"tick": 1000, "steamid": "1", "x": -900, "y": 50, "z": 0, "util": "he"},      # active now
    ]
    snap = p._snapshot_at(1000, "de_mirage", "1", detonations, exact, {})

    assert len(snap.players) == 3
    me = next(e for e in snap.players if e.is_analyzed_player)
    assert me.label == "Alice" and me.team == "T" and me.alive is True
    assert me.nx is not None and 0.0 <= me.nx <= 1.0
    dead = next(e for e in snap.players if e.label == "Bob")
    assert dead.alive is False
    # Smoke (within 18s) and HE (this tick) stay; the long-expired flash drops.
    util_types = sorted(u.util_type for u in snap.utils)
    assert util_types == ["he", "smoke"]
    smoke = next(u for u in snap.utils if u.util_type == "smoke")
    assert smoke.team == "CT"  # resolved from the detonator's team
    # Lifetime boundary: smoke detonated at 990 is gone well after 18s.
    later = p._snapshot_at(990 + int(19 * TICK_RATE), "de_mirage", "1", detonations, exact, {})
    assert all(u.util_type != "smoke" for u in later.utils)


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
