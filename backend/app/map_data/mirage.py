"""Mirage (de_mirage) canonical zone mapping data.

Deterministic reference data that turns either a CS2 nav-area / callout name OR
a world coordinate into ONE of a small, fixed set of canonical Mirage zones.
This is the ground truth the coach is allowed to talk about: the LLM may only
use these labels and must never invent a callout from raw coordinates.

The resolver in ``app/map_zones.py`` consumes three layers, most-trusted first:

  1. awpy 2.0.2 nav mesh -> nav area -> zone.  awpy 2.0.2 nav areas carry
     GEOMETRY ONLY (no callout/place name), so a resolved area is labelled via
     ``NAV_AREA_ID_TO_ZONE`` (exact area id) and, failing that, by passing the
     area's centroid through ``COORDINATE_REGIONS``.
  2. ``COORDINATE_REGIONS``   -- axis-aligned world boxes -> zone (used directly
     for a raw coordinate when no nav mesh is loaded).
  3. ``CALLOUT_TO_ZONE``      -- CS2 ``last_place_name`` / demo callout -> zone
     (the deterministic fallback; this is what carries the bundled fixture).

Anything that matches none of these resolves to the safe ``"Unknown"`` label.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

MAP_NAME = "de_mirage"

# The ONLY zone labels the resolver may emit for Mirage (besides "Unknown").
# Keep this list broad and stable -- it is what the coach prompt is pinned to.
CANONICAL_ZONES: List[str] = [
    "A Site",
    "A Ramp",
    "Palace",
    "Connector",
    "Window",
    "Mid",
    "Catwalk",
    "B Apps",
    "B Site",
    "Market",
    "CT Spawn",
    "T Spawn",
]

# 1) awpy 2.0.2 nav-mesh area id -> canonical zone.
# awpy 2.0.2 nav areas have no place name, so the only way to attach a label by
# id is this table. Populate it from a real Mirage nav mesh using the helper:
#     python -m app.tools.dump_nav de_mirage
# which prints each area_id with its centroid (and the zone currently inferred
# from COORDINATE_REGIONS) so you can pin specific areas. Left empty by default:
# an unmapped id falls through to centroid->COORDINATE_REGIONS, then "Unknown".
NAV_AREA_ID_TO_ZONE: Dict[int, str] = {}

# 2) Approximate world-coordinate boxes, checked in order (first match wins).
# TODO(coords): these are APPROXIMATE and must be verified against real Mirage
# nav bounds (e.g. from an awpy nav mesh). They are intentionally left empty so
# the resolver never emits a confidently-wrong label from a guessed box -- a
# coordinate that matches no box (and no nav mesh) resolves to "Unknown".
# Schema: (x_min, x_max, y_min, y_max, zone). z is currently ignored (the broad
# Mirage zones below are effectively single-level).
COORDINATE_REGIONS: List[Tuple[float, float, float, float, str]] = [
    # e.g. (-1200.0, 200.0, -700.0, 800.0, "A Site"),  # TODO: verify bounds
]

# 3) CS2 callout / nav place-name substrings -> canonical zone.
# Matched against a NORMALIZED place string (lowercased, non-alphanumeric
# stripped). ORDER MATTERS: more specific tokens come first so e.g. "A ramp"
# ("aramp") resolves to "A Ramp" before the generic "asite" -> "A Site" rule.
# These tokens come from the demo's last_place_name / awpy nav place names (and
# the human-readable callouts used in the bundled fixture), never from the LLM.
CALLOUT_TO_ZONE: List[Tuple[str, str]] = [
    ("bombsitea", "A Site"),
    ("bombsiteb", "B Site"),
    ("ctspawn", "CT Spawn"),
    ("tspawn", "T Spawn"),
    ("palace", "Palace"),
    ("balcony", "Palace"),
    ("aramp", "A Ramp"),
    ("ramp", "A Ramp"),
    ("stairs", "A Ramp"),
    ("ticket", "A Ramp"),
    ("catwalk", "Catwalk"),
    ("connector", "Connector"),
    ("jungle", "Connector"),
    ("window", "Window"),
    ("market", "Market"),
    ("sidealley", "Market"),
    ("apartment", "B Apps"),
    ("apps", "B Apps"),
    ("kitchen", "B Apps"),
    ("underpass", "B Apps"),
    ("topofmid", "Mid"),
    ("middle", "Mid"),
    ("mid", "Mid"),
    ("asite", "A Site"),
    ("tetris", "A Site"),
    ("firingrange", "A Site"),
    ("bsite", "B Site"),
]
