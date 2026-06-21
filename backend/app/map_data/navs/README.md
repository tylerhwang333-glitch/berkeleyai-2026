# Nav meshes

The map-zone resolver (`app/map_zones.py`) can use **awpy 2.0.2** nav meshes to
turn a world coordinate into a canonical zone. Nav files are **not** committed
here (they are large binaries downloaded per game build).

## Getting the nav files

awpy downloads nav meshes into `~/.awpy/navs/` (one `<map>.nav` per map):

```bash
pip install "awpy==2.0.2"   # Python 3.11-3.13 only
awpy get navs               # downloads <map>.nav for the current build
```

The resolver looks for `<map>.nav` in this order:

1. `$AWPY_NAV_DIR/<map>.nav` (or `$AWPY_NAV_DIR` itself)
2. `awpy.data.NAVS_DIR/<map>.nav` (i.e. `~/.awpy/navs/de_mirage.nav`)
3. `app/map_data/navs/<map>.nav` (drop a file here to bundle one)

If none is found — or awpy isn't installed — zone resolution degrades to the
deterministic callout mapping in `app/map_data/<map>.py`, then `"Unknown"`.

## Labelling areas

awpy 2.0.2 nav areas have geometry only (no callout names). To attach canonical
zone labels by area id, dump a real nav and fill in
`app/map_data/<map>.py:NAV_AREA_ID_TO_ZONE`:

```bash
python -m app.tools.dump_nav de_mirage          # area_id, centroid, inferred zone
python -m app.tools.dump_nav de_mirage --zone "A Site"
```
