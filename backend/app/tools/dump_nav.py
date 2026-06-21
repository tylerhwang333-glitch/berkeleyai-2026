"""Dump an awpy nav mesh as ``area_id -> centroid -> inferred zone``.

Use this to populate ``app/map_data/<map>.py`` (NAV_AREA_ID_TO_ZONE and/or
COORDINATE_REGIONS) from REAL nav data instead of guessing coordinates.

Prereqs (see requirements.txt): awpy 2.0.2 on Python 3.11-3.13, and the nav file
downloaded once with ``awpy get navs`` (or AWPY_NAV_DIR / app/map_data/navs/).

    python -m app.tools.dump_nav de_mirage
    python -m app.tools.dump_nav de_mirage --zone "A Site"   # only that zone

Run from the ``backend`` directory.
"""
from __future__ import annotations

import sys

from .. import map_zones


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    map_name = argv[0]
    only_zone = None
    if "--zone" in argv:
        i = argv.index("--zone")
        only_zone = argv[i + 1] if i + 1 < len(argv) else None

    canonical = map_zones._canonical_map(map_name)
    if canonical is None or canonical not in map_zones._MAP_MODULES:
        print(f"No mapping module for map '{map_name}'.")
        return 1

    path = map_zones._nav_path(canonical)
    if path is None:
        print(
            f"No nav file found for {canonical}. Run `awpy get navs` or set "
            f"AWPY_NAV_DIR / drop {canonical}.nav in app/map_data/navs/."
        )
        return 1

    try:
        from awpy.nav import Nav
    except Exception as exc:  # noqa: BLE001
        print(f"awpy is not importable ({exc}). Install awpy==2.0.2 on Python 3.11-3.13.")
        return 1

    nav = Nav.from_path(str(path))
    mod = map_zones._MAP_MODULES[canonical]
    print(f"# {canonical}: {len(nav.areas)} nav areas from {path}")
    print("# area_id, centroid_x, centroid_y, centroid_z, inferred_zone")
    rows = []
    for area_id, area in nav.areas.items():
        c = area.centroid
        zone = mod.NAV_AREA_ID_TO_ZONE.get(area_id) or map_zones._zone_from_regions(mod, c.x, c.y)
        if only_zone and zone != only_zone:
            continue
        rows.append((area_id, c.x, c.y, c.z, zone))
    for area_id, cx, cy, cz, zone in sorted(rows):
        print(f"{area_id}\t{cx:.1f}\t{cy:.1f}\t{cz:.1f}\t{zone}")
    print(f"# {len(rows)} areas listed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
