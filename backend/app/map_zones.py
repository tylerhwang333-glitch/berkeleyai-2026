"""Deterministic coordinate/callout -> canonical map-zone resolver.

Why this exists: the parser provides reliable world coordinates and CS2 callouts,
but the LLM coach must NEVER infer a map location itself (that is how it ends up
hallucinating callouts). This module turns a position -- or a demo-provided
callout string -- into exactly ONE label from a small, fixed canonical set per
map, or ``"Unknown"``. The coach prompt is then pinned to those labels.

Resolution order for ``resolve_map_zone(map, x, y, z)`` (most trusted first):

  1. awpy nav mesh (if importable AND a nav file is present):
       coordinate -> nav area -> canonical zone
  2. coordinate regions from the per-map mapping file (``map_data/<map>.py``)
  3. ``"Unknown"``  -- also returned for unsupported maps, missing coordinates,
     or when no mapping data resolves the point.

Everything degrades gracefully: a missing awpy, missing nav files, missing
coordinates, or missing mappings all just yield ``"Unknown"`` rather than raising.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .map_data import mirage as _mirage
from .models import MapRadar, ParsedDemo

UNKNOWN_ZONE = "Unknown"

# Per-map mapping modules, keyed by canonical (de_-prefixed) map name.
_MAP_MODULES = {
    "de_mirage": _mirage,
}

# Cache of derived awpy nav indexes, keyed by canonical map name. A value of
# ``False`` means "we already tried and it is unavailable" (don't retry).
_NAV_CACHE: Dict[str, object] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _canonical_map(map_name: Optional[str]) -> Optional[str]:
    """Normalize a map name to its canonical ``de_*`` form (or None)."""
    if not map_name:
        return None
    m = map_name.strip().lower()
    if not m:
        return None
    if not m.startswith("de_"):
        m = "de_" + m
    return m


def canonical_zones(map_name: str) -> List[str]:
    """The fixed list of zone labels the coach may use for this map (or [])."""
    mod = _MAP_MODULES.get(_canonical_map(map_name))
    return list(mod.CANONICAL_ZONES) if mod else []


def _normalize_place(place: str) -> str:
    return re.sub(r"[^a-z0-9]", "", place.lower())


def zone_from_callout(map_name: str, place: Optional[str]) -> str:
    """Deterministically map a CS2 callout / nav place name to a canonical zone.

    The callout comes from the demo (``last_place_name``) or the parsed fixture,
    NOT from the LLM, so this is still a deterministic map reference rather than
    a guess. Returns ``"Unknown"`` when nothing matches.
    """
    mod = _MAP_MODULES.get(_canonical_map(map_name))
    if mod is None or not place:
        return UNKNOWN_ZONE
    norm = _normalize_place(place)
    if not norm:
        return UNKNOWN_ZONE
    for token, zone in mod.CALLOUT_TO_ZONE:
        if token in norm:
            return zone
    return UNKNOWN_ZONE


# ---------------------------------------------------------------------------
# Coordinate regions (mapping-file source of truth for zone labels)
# ---------------------------------------------------------------------------
def _zone_from_regions(mod, x: float, y: float) -> str:
    """Map a world (x, y) to a canonical zone via the map's COORDINATE_REGIONS."""
    for xmin, xmax, ymin, ymax, rzone in mod.COORDINATE_REGIONS:
        if xmin <= x <= xmax and ymin <= y <= ymax:
            return rzone
    return UNKNOWN_ZONE


# ---------------------------------------------------------------------------
# Awpy 2.0.2 nav mesh (optional dependency, fully guarded)
# ---------------------------------------------------------------------------
# How this works with awpy 2.0.2:
#   * awpy is OPTIONAL (see requirements.txt; it needs Python 3.11-3.13). If it
#     or the nav file is missing, everything degrades to coordinate regions /
#     callouts and ultimately "Unknown" -- nothing raises.
#   * Nav files are NOT bundled with awpy. They are downloaded once via the awpy
#     CLI (`awpy get navs`) into ``awpy.data.NAVS_DIR`` (~/.awpy/navs). Set
#     AWPY_NAV_DIR to point elsewhere, or drop ``<map>.nav`` in
#     ``app/map_data/navs/``.
#   * awpy 2.0.2 nav areas carry GEOMETRY ONLY (corners/centroid/area_id) -- they
#     have NO callout/place name. So a resolved nav area is turned into a
#     canonical zone via the map module's NAV_AREA_ID_TO_ZONE table first, then
#     by passing the area centroid through COORDINATE_REGIONS. We precompute that
#     label per area once at load and cache it.
_NEAREST_CENTROID_MAX = 300.0  # units; beyond this a containment-miss stays Unknown


def _nav_path(canonical: str) -> Optional[Path]:
    """Locate ``<map>.nav`` across the override dir, awpy's dir, and repo fallback."""
    candidates: List[Path] = []
    env = os.environ.get("AWPY_NAV_DIR")
    if env:
        candidates += [Path(env) / f"{canonical}.nav", Path(env)]
    try:
        from awpy.data import NAVS_DIR  # type: ignore

        candidates.append(Path(NAVS_DIR) / f"{canonical}.nav")
    except Exception:  # noqa: BLE001 - awpy not importable
        pass
    candidates.append(Path(__file__).parent / "map_data" / "navs" / f"{canonical}.nav")
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def _build_area_index(canonical: str, nav) -> object:
    """Precompute a fast per-area lookup with a canonical zone label baked in.

    Returns a list of ``{poly, zmin, zmax, cx, cy, cz, zone}`` dicts, or ``False``
    when no usable areas were produced.
    """
    mod = _MAP_MODULES.get(canonical)
    index: List[dict] = []
    for area_id, area in nav.areas.items():
        corners = getattr(area, "corners", None) or []
        if len(corners) < 3:
            continue
        poly = [(c.x, c.y) for c in corners]
        zs = [c.z for c in corners]
        cen = area.centroid  # Vector3 property
        zone = UNKNOWN_ZONE
        if mod is not None:
            zone = mod.NAV_AREA_ID_TO_ZONE.get(area_id) or _zone_from_regions(mod, cen.x, cen.y)
        index.append(
            {
                "poly": poly,
                "zmin": min(zs),
                "zmax": max(zs),
                "cx": cen.x,
                "cy": cen.y,
                "cz": cen.z,
                "zone": zone,
            }
        )
    return index or False


def _load_nav(canonical: str):
    """Return a cached awpy-derived area index for the map, or ``False``."""
    if canonical in _NAV_CACHE:
        return _NAV_CACHE[canonical]
    index: object = False
    try:
        from awpy.nav import Nav  # type: ignore

        path = _nav_path(canonical)
        if path is not None:
            nav = Nav.from_path(str(path))
            index = _build_area_index(canonical, nav)
    except Exception:  # noqa: BLE001 - missing awpy/file/parse error -> no nav
        index = False
    _NAV_CACHE[canonical] = index
    return index


def _point_in_polygon(x: float, y: float, poly: List[tuple]) -> bool:
    """Ray-casting point-in-polygon test over a list of (px, py) vertices."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi if yj != yi else 1e-9) + xi
        ):
            inside = not inside
        j = i
    return inside


def _zone_from_nav(canonical: str, x: float, y: float, z: Optional[float]) -> str:
    """coordinate -> awpy nav area -> canonical zone. Fully guarded."""
    index = _load_nav(canonical)
    if not index:
        return UNKNOWN_ZONE
    try:
        # 1. Areas whose XY polygon contains the point; pick the closest in z.
        best = None
        best_dz = float("inf")
        for area in index:  # type: ignore[union-attr]
            if not _point_in_polygon(x, y, area["poly"]):
                continue
            if z is None:
                dz = 0.0
            elif area["zmin"] - 64 <= z <= area["zmax"] + 64:
                dz = 0.0
            else:
                dz = min(abs(z - area["zmin"]), abs(z - area["zmax"]))
            if dz < best_dz:
                best_dz, best = dz, area
        # 2. Fallback: nearest area centroid within a sane radius.
        if best is None:
            best_d = _NEAREST_CENTROID_MAX
            for area in index:  # type: ignore[union-attr]
                d = ((area["cx"] - x) ** 2 + (area["cy"] - y) ** 2) ** 0.5
                if d < best_d:
                    best_d, best = d, area
        return best["zone"] if best else UNKNOWN_ZONE
    except Exception:  # noqa: BLE001 - any geometry error -> safe fallback
        return UNKNOWN_ZONE


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------
def resolve_map_zone(
    map_name: str, x: Optional[float], y: Optional[float], z: Optional[float] = None
) -> str:
    """Resolve a world coordinate to a canonical zone label for ``map_name``.

    Order: awpy nav mesh (if available) -> coordinate regions -> ``"Unknown"``.
    Never raises; unsupported maps and missing coordinates return ``"Unknown"``.
    """
    canonical = _canonical_map(map_name)
    if canonical is None or canonical not in _MAP_MODULES:
        return UNKNOWN_ZONE
    if x is None or y is None:
        return UNKNOWN_ZONE

    # 1. Preferred: awpy nav mesh.
    zone = _zone_from_nav(canonical, x, y, z)
    if zone != UNKNOWN_ZONE:
        return zone

    # 2. Coordinate regions from the mapping file.
    zone = _zone_from_regions(_MAP_MODULES[canonical], x, y)
    if zone != UNKNOWN_ZONE:
        return zone

    # 3. Safe fallback.
    return UNKNOWN_ZONE


# Alias matching the task's requested camelCase signature.
resolveMapZone = resolve_map_zone


def resolve_zone(
    map_name: str,
    x: Optional[float] = None,
    y: Optional[float] = None,
    z: Optional[float] = None,
    place: Optional[str] = None,
) -> str:
    """Coordinate-first zone resolution with a deterministic callout fallback.

    Prefers real coordinates (awpy nav / coordinate regions). If coordinates are
    missing or resolve to ``"Unknown"``, falls back to the demo-provided callout
    string (also deterministic). Returns ``"Unknown"`` if nothing resolves.
    """
    zone = UNKNOWN_ZONE
    if x is not None and y is not None:
        zone = resolve_map_zone(map_name, x, y, z)
    if zone == UNKNOWN_ZONE:
        zone = zone_from_callout(map_name, place)
    return zone


# ---------------------------------------------------------------------------
# Radar image + world->image coordinate scaling (for map visualization)
# ---------------------------------------------------------------------------
def _radar_cfg(map_name: str) -> Optional[dict]:
    """The RADAR calibration block for a map, or None if it has none."""
    mod = _MAP_MODULES.get(_canonical_map(map_name))
    cfg = getattr(mod, "RADAR", None) if mod else None
    return cfg if isinstance(cfg, dict) else None


def world_to_normalized(
    map_name: str, x: Optional[float], y: Optional[float]
) -> Tuple[Optional[float], Optional[float]]:
    """Scale a world (x, y) to a 0..1 fraction of the map's radar image.

    Returns ``(None, None)`` for unsupported/uncalibrated maps or missing
    coordinates. The fraction is resolution-independent: the frontend multiplies
    it by whatever size it renders the radar image at, so positions stay correct
    at any display size ("scaled to the size of the picture").
    """
    cfg = _radar_cfg(map_name)
    if cfg is None or x is None or y is None:
        return None, None
    span = float(cfg["scale"]) * float(cfg["size"])
    if span == 0:
        return None, None
    nx = (float(x) - float(cfg["pos_x"])) / span
    ny = (float(cfg["pos_y"]) - float(y)) / span
    return nx, ny


def radar_descriptor(map_name: str, asset_base: str = "/assets") -> Optional[MapRadar]:
    """Build the MapRadar descriptor (image URL + calibration) for a map."""
    cfg = _radar_cfg(map_name)
    if cfg is None:
        return None
    canonical = _canonical_map(map_name) or map_name
    return MapRadar(
        map=canonical,
        image_url=f"{asset_base.rstrip('/')}/{cfg['image']}",
        pos_x=float(cfg["pos_x"]),
        pos_y=float(cfg["pos_y"]),
        scale=float(cfg["scale"]),
        size=float(cfg["size"]),
    )


def annotate_zones(demo: ParsedDemo) -> ParsedDemo:
    """Fill canonical zones on events and player summaries in-place.

    Coordinate-driven zones are set by the real parser where positions exist;
    this pass guarantees EVERY parser mode (fixture / json / real) ends up with
    canonical zone labels derived from whatever deterministic info is available
    (coordinates already resolved by the parser, else the demo callout strings).

    The LLM never sees raw coordinates -- only these labels. Runs once, right
    after parsing and before detection, so detectors and the coach see zones.
    """
    for rnd in demo.rounds:
        for ev in rnd.events:
            if not ev.zone or ev.zone == UNKNOWN_ZONE:
                ev.zone = zone_from_callout(demo.map, ev.location)
        s = rnd.player_summary
        if not s.death_zone or s.death_zone == UNKNOWN_ZONE:
            s.death_zone = zone_from_callout(demo.map, s.death_location)
        if not s.primary_zone or s.primary_zone == UNKNOWN_ZONE:
            s.primary_zone = zone_from_callout(demo.map, s.primary_area)
    return demo
