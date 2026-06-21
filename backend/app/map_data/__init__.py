"""Per-map canonical zone mapping data (nav-area / coordinate / callout -> zone).

Each map module here is pure data consumed by ``app/map_zones.py``. Keeping the
mapping in dedicated files makes it obvious what the coach is allowed to talk
about and easy to tune per map without touching resolver logic.
"""
