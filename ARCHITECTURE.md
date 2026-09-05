# swd — Chennai storm water drain network, 3D viewer + data

Live at **https://swd.reclaimchennai.city** (static, served by the shared Caddy
from `~/projects/swd/web`, mounted read-only as `/var/www/swd`). No backend, no
container of its own. HTTPS means the locate-me button works.

Origin of the project and the full method are in `README.md`; the traps are in
`HANDOVER.md`. This file is the server-side map: what runs here, how it is
deployed, and what changed in the 2026-09-04 Linux session.

## Layout

```
web/                    the site — index.html + data/, mount this and nothing else
  data/network.json     nodes + conduits, WGS84, 4.0 MB (≈1 MB gzipped)
  data/network_pre_route.json   the same before chord routing (for ?net_file= before/after)
  data/ground.json      Delaunay TIN of the 5,355 surveyed road-edge levels
  data/wards.json       GCC ward outlines (from the Mac; TNGIS source is not on this server)
  data/rainfall.json    daily totals + hourly hyetograph for the Dec 2015 run
  data/swmm.json/.bin   the SWMM run, hourly, uint16 — loaded only on demand
derived/                the connected build, SWMM model, OSM roads, per-ward extraction
as_drawn/               ground-truth export, untouched by this session
GeoPDF/                 the 107 ward sheets
.venv-linux/            Python 3.11 venv with numpy/scipy/shapely/pyproj/networkx/swmm-toolkit/pymupdf/pillow
```

The original `.venv` (Python 3.14, macOS) and `bin/runswmm` (arm64 Mach-O) do not
work here. Use `.venv-linux/bin/python` and `run_swmm_linux.py`.

## Deploy

Static files only — editing anything under `web/` is the deploy. Caddy block in
`~/projects/caddy/Caddyfile` (`swd.reclaimchennai.city`): zstd/gzip, `no-cache`
on HTML, 1 h `must-revalidate` on `/data/*`. `encode` needs an explicit `match` block
listing `application/octet-stream` (one `header` line per type) or the `.bin` ships raw. The volume is in
`~/projects/caddy/docker-compose.yml`; adding it needed `docker compose up -d`
(a Caddyfile reload does not pick up new mounts — see caddy/ARCHITECTURE.md).

Local preview: `python3 -m http.server 8779 --bind 127.0.0.1 --directory web`.

## Pipeline additions (run in this order, after `run_connect.py`)

| script | what | notes |
|---|---|---|
| `run_drain_type_vector.py` | re-reads the drain-type column from vector glyph geometry | Vision OCR never returns a lone `O`; `C` is an open 17-segment path, `O` a closed 20-segment loop. Control: 7,099 OCR-`C` rows agree, 4 disagree. Filled 1,131 blank cells: **57 `O`**, 1,074 `C`. Needs no OCR — runs on Linux, ~5 min on 6 cores. |
| `run_route_chords.py` | routes the 2,157 schedule chords along OSM roads | `derived/osm/roads.json` is an Overpass `way[highway]` dump over the network bbox (28 MB, 2026-05 OSM). Ends are projected onto the nearest road edge and spliced in as temporary graph nodes. `ROUTE_MAX_RATIO` env var, default **1.5** (1.4 → 989 routed / 1,131 dropped; 1.5 → 1,263 / 857; 1.7 → 1,535 / 585). Always starts from `conduits.pre_route.json`, so it is idempotent. |
| `run_web_export.py` | packs `web/data/` | Rewritten for Linux (pyproj instead of GDAL). Adds per-conduit invert breakpoints (`bp`) where a run passes an intermediate surveyed manhole within 3 m, and the ground TIN. `CONDUITS=` / `NET_OUT=` env vars for alternative inputs/outputs. |
| `run_swmm_linux.py` | runs the model via `swmm-toolkit` | same EPA SWMM 5.2 engine as `bin/runswmm`. 123 min wall clock here (wet days are slow); mass balance identical to the Mac run (−0.004 % / +0.039 %). |
| `run_swmm_pack.py` | `.out` (271 MB) → `web/data/swmm.bin` | hourly bins: max of the four 15-min samples for depths and flooding, mean for velocity; uint16 mm / L/s / mm/s. |

The SWMM model `.inp` was **not** regenerated after chord routing, so the
simulated run still uses the 6,370-conduit straight-chord network; links that
were dropped simply have no water in the viewer. Regenerating needs
`run_export.py` (GDAL, currently Mac-only paths) — see Open items.

## The viewer (`web/index.html`)

MapLibre GL 4.7 owns the basemap and camera; a Three.js r160 custom layer draws
into the same WebGL2 context. Read the "Do not undo" list in `HANDOVER.md`
first. What this session changed:

- **Every typed conduit is a closed box culvert**: floor at the invert, walls
  `depth_m` high, soffit at invert + `depth_m`, inside width `width_m`. Type `O`
  (30 nodes / 30 conduits after the vector pass) gets no soffit. The lid is
  drawn in a second transparent pass whose alpha follows the view angle —
  looking down into a culvert it is 10 % so the inside reads; from the side it
  solidifies to 72 %.
- **No dimension floors or defaults.** Elements without a surveyed section are
  drawn at a labelled 0.6 × 0.6 m placeholder as a stippled, desaturated ghost,
  with counts in the legend (971 conduits, 1,218 chambers). Toggle to hide.
- **One exaggeration factor** for levels and sections (default 6×, slider 1–40,
  a `1:1` button). Vertical placement happens in the vertex shader from the
  surveyed levels, so changing exaggeration or datum touches no buffers.
- **Chambers** are the drain section (`width_m` across flow × `depth_m` tall)
  extruded a constant 1.2 m along the drain, rotated to the doubled-angle mean
  bearing of the incident conduits; open at both ends; the conduit ends are
  trimmed 0.6 m so they butt against the chamber instead of running through it.
  A 0.30 m-radius shaft rises from the soffit to `road_edge` with a 0.64 m cast
  iron cover disc at road level; a negative cover (drain proud of the kerb) draws
  the cover and no shaft.
- **Invert profile** steps at intermediate surveyed manholes (253 breakpoints).
- **Ground**: under "Below street" the surface is flat by construction (every
  chamber hangs from its own road level) — the veil plane. Under "Sea level" a
  Delaunay TIN of the 5,355 road-edge levels carries real relief, hillshaded
  with a contour every metre.
- **Materials**: one custom shader for culverts and chambers — Lambert + a
  damp-concrete sheen, world-position grain, contact darkening where chamber
  meets conduit, and a depth cue that darkens and desaturates with real metres
  below ground. Shafts and covers are `MeshStandardMaterial` (concrete /
  cast iron). No shadow maps or SSAO: a hand-built `THREE.Camera` cannot drive
  three's shadow cameras; the contact darkening is the stand-in.
- **Performance**: geometry is split into 800 m chunks (290 of them) with real
  bounding spheres so three's frustum culling works against MapLibre's matrix;
  chambers, shafts and covers are instanced per chunk; shafts + covers drop out
  below zoom 14.5, chambers below 13.2. Conduit colour is a 1-texel-per-conduit
  texture, so a recolour uploads 22 KB. 1.28 M vertices / 642 k triangles built
  in ~430 ms after the data arrives.
- **MapLibre far-plane fix**: MapLibre puts its far plane 1 % beyond the visible
  ground, which clips everything buried at low pitch. `render()` rebuilds the
  clip-z row of the matrix for a far plane 40× further (needs `map.transform.nearZ/farZ`).
- **Camera position** for the specular/lid terms is the inverse image of the
  clip-space direction (0,0,−1,0) — `map.getFreeCameraOptions()` is Mapbox-only.
- **Uphill runs** (1,362) carry a thin red band every 3 m in every colour mode.
- **Simulated event** (Part 3): loaded on demand (7.5 MB bin, 2.7 MB gzipped, hourly; 4,079 of the 5,513 viewer conduits match a model link by `from__to` name — the rest carry no water). Link depth
  and velocity, node depth and flooding rate live in R32F data textures sampled
  in the vertex shader — scrubbing uploads nothing. Water surface per culvert
  clamped to the soffit; water column up the shaft when the node surcharges;
  a ponding disc at the street scaled by log flooding rate. Timeline with the
  hyetograph, play/pause, speed. Every label says *simulated*; the panel and
  the toast carry the lift ×0.97 caveat.
- **URL state**: MapLibre `hash:true` for the camera; `?node=W70_A2` opens a
  readout and centres, `?ex=`, `?datum=msl`, `?net=conn`, `?mode=`, `?sim=1&t=`,
  `?net_file=network_pre_route.json`.

### Numbers drawn vs assumed

Surveyed and drawn exactly: invert, drain top, road-edge levels; conduit and
chamber `width_m × depth_m`; drain type; centrelines traced from the sheets.
Assumed constants (all in `ASSUME` at the top of the script and listed in the
panel): chamber length 1.2 m, shaft radius 0.30 m, cover Ø 0.64 × 0.05 m,
placeholder section 0.6 × 0.6 m, a chamber with no invert hangs 0.6 m under its
drain top, wall thickness 0 (not surveyed, not drawn).

## Measured (2026-09-04, headless Chromium on this server)

- First load, 390×844 mobile viewport, against the live HTTPS site with Chrome's
  "Fast 4G" throttle (4 Mbit/s, 20 ms): **4.2 s** to network built, 2.47 MB
  transferred. At 1.6 Mbit/s / 150 ms: 9.5 s.
- Frame rate: this server has no GPU. SwiftShader software GL gives 1–2 fps at
  city scale and 7–16 fps zoomed in, which says nothing about a phone. Not
  measured on real mobile hardware — see Open items.

## Open items

- Frame rate on a real mid-range phone is unmeasured.
- Regenerate the SWMM `.inp` from the routed network (needs GDAL or a pyproj port
  of `run_export.py`) and re-run, then `run_swmm_pack.py`.
- `wards.json` cannot be regenerated here (TNGIS source only on the Mac).
- The 664 nodes with no drain type have no schedule row at all, not an unread cell.
- Basemap imagery is not draped on the TIN (MapLibre draws it flat, without depth).

## 2026-09-05 follow-up

- **Manhole symbols in the centrelines.** The tracer followed each sheet's ~1 mm
  manhole circle (≈5 m at 1:5000) into the drain path: 850 of 8,426 as-drawn
  conduit ends carried ~42 vertices looping round the manhole, which the box
  extrusion rendered as a "gear". `run_web_export.py:collapse_symbols()` snaps
  every vertex within 6 m of a manhole onto it (850 → 10 loop tails; 1.28 M →
  0.66 M vertices; network.json 4.0 → 3.1 MB). `derived/` geometry is untouched.
- **Six sheets are mis-georeferenced.** Median distance of as-drawn vertices to
  the nearest OSM road centreline is 5.2 m overall, but wards 108 (19 m), 103
  (14), 133 (15), 112 (15), 95 (13), 140 (13) are off by a near-rigid shift: a
  single translation brings 140 to 0.8 m, 112 to 5.3, 108 to 6.2, 133 to 6.6,
  95 to 7.2 (103 only to 10.3 — likely rotation/scale too). Those affine solves
  in `derived/affines.json` should be redone. **Not corrected** — it is the
  user's call whether to shift "as drawn" data.
- **No external KML** of the drain network exists on this server; the only KML
  is our own `as_drawn/kml` export.
- UI: `2D`/`3D` toggle control (compass kept, +/- removed on touch); tap a
  conduit to highlight it end to end with its own readout (`pickConduit`,
  `uHi` uniform); the level legend is a gradient bar.

## 2026-09-05 — alignment audit, maps, repository

- **GCC drain GIS** (data.opencity.in, 10,278 lines, 2,219 km, all zones) downloaded to
  `derived/reference/opencity_swd.kml` (git-ignored, third-party). Our as-drawn vertices: median
  5.1 m from it, 66 % within 10 m, 22 % over 25 m. It flags the same block-offset wards as the OSM
  test (95, 103, 108, 112, 133, 140) plus 33, 96, 170, 177, 60, 52, 104 — but it carries offsets of
  its own (a rigid shift fixes ward 140 to 0.8 m against OSM, only 9.5 m against the KML).
- **Verdict on the offset sheets**: `affines.json` shows clean grid fits for all of them
  (rms 0.05–1.2 m; 108/103/112/140 have only 3–4 northing lines, hence ~1 m rms). A 20 m
  error cannot come from a grid fit that good, so the drawing on those sheets is offset from its
  own printed grid. Left as drawn, documented in README "Accuracy".
- `run_maps.py` → `maps/` (six 4000 px variants). Viewer: Earth/Streets (Google) only, compass
  removed. Manhole-symbol loops are collapsed, so there is nothing left to label.
- Repository: github.com/reclaimchennai/chennai-swd — everything except venvs, the 281 MB
  SWMM binary, the reference KML, and `derived/shp/map_context.*` (131 MB > GitHub's 100 MB cap;
  the same layer is in `derived/chennai_swd.gpkg`).

## 2026-09-05 — ward boundaries, release assets

- `run_wards.py` replaces the TNGIS `wards.json`: the sheets use the **pre-2022** ward delimitation
  (`~/projects/OpenDataChennai/geojson/Chennai_Wards_old.geojson` matches the sheet-traced
  `ward_boundary` lines at median 6 m / p80 38 m; the 2022 file — TNGIS, maps-site, civic-sentinel —
  is >150 m off in 33 wards). Polygons are clipped to land with the OSM coastline
  (`derived/osm/coastline.json`, ends extended to the bbox so `polygonize` splits it).
  Wards 133 and 171 still disagree with the sheet trace by >150 m — unresolved.
- Repository trimmed: `GeoPDF/`, `as_drawn/shp/`, `derived/shp/` and the per-ward shapefiles in `derived/wards/` are git-ignored and live in release
  **v1.0** (218 MB PDFs, 27 MB + 47 MB shapefile zips, built by `zipfile` — `zip` is not installed
  here). History was rewritten (single commit, force-pushed) so the PDFs are not in the clone.
