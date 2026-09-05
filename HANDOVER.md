# Handover — Chennai SWD extraction

For whoever (or whatever) picks this up next. Read this before touching
anything; several of the non-obvious decisions here were expensive to reach and
easy to undo by accident.

---

## What this project is

107 GCC / SECON-JBA "BASE MAP – STORM WATER DRAIN" ward sheets (2021 survey,
World Bank PDGF flood-forecasting project) turned into machine-readable GIS
data, a runnable SWMM model, and a 3D web viewer.

Everything is **EPSG:32644** (WGS 84 / UTM zone 44N) on disk. The web viewer
converts to EPSG:4326 because MapLibre requires it.

The single most important thing to understand: **these PDFs are not GeoPDFs.**
They carry no georeferencing. Every coordinate in this project comes from
least-squares fitting each sheet's own printed 500 m UTM grid, after OCR'ing the
grid labels. Median residual is 4.7 cm. If vectors ever land near the Maldives,
the affine solve failed — that is the failure mode, and `derived/affines.json`
is where to look.

The second most important thing: **no text on these sheets is selectable.** It
is all AutoCAD SHX font glyph outlines. Every number and street name was OCR'd
(macOS Vision framework, via `pyobjc`). There is no shortcut around this.

---

## Environment

```bash
cd ~/projects/swd
python3 -m venv .venv314          # Python 3.14; the venv is NOT in the upload
.venv314/bin/pip install pymupdf pyobjc-framework-Vision scipy numpy requests
```

GDAL comes from Homebrew, not pip. Every command needs it on the path:

```bash
export PYTHONPATH=/opt/homebrew/Cellar/gdal/3.13.0/lib/python3.14/site-packages:$PWD
.venv314/bin/python run_export.py
```

Adjust that GDAL path for the host — on Linux it is usually already importable
and you can drop it. `swdlib/ocr.py` is **macOS-only** (Vision framework). If you
re-run extraction on Linux you need a different OCR backend; everything
downstream of `derived/wards/` works anywhere.

SWMM is built from source in `bin/runswmm` (EPA `Stormwater-Management-Model`).
The pip wheels `swmm-toolkit` / `pyswmm` **SIGKILL on import** on Apple Silicon —
their bundled dylibs are ad-hoc signed. Don't waste time on them. Build needs
`brew install cmake libomp` and an explicit
`-DOpenMP_C_FLAGS="-Xpreprocessor -fopenmp -I/opt/homebrew/opt/libomp/include"`.

---

## The pipeline, in order

```bash
python run_georef.py              # page -> UTM affine for all 107 sheets
python run_extract.py             # per-ward extraction   (~25 min, 6 workers)
python run_labels.py              # street / place names  (~12 min)
python run_network.py --no-clip   # city-wide network, dedup + stitch
python run_floodhist.py           # tag manholes by historical flooding
python run_connect.py             # <-- adds the inferred links. See below.
python run_rainfall.py            # hybrid gauge + ERA5 hourly series
python run_export.py              # GeoPackage + shapefiles + SWMM .inp
./bin/runswmm derived/swd_model.inp derived/swmm/run2015.rpt derived/swmm/run2015.out
python run_analyse.py             # score against the flood record  (run ~49 min)
python run_as_drawn.py            # the un-joined, PDF-faithful export
python run_web_export.py          # pack network.json + wards.json for the viewer
```

`derived/wards/ward_XXX/` is the per-sheet output and the point of truth for
geometry. `derived/network/*.json` is the city-wide build. **`run_connect.py`
rewrites `derived/network/nodes.json` and `conduits.json` in place** — if you
re-run it twice you will double-apply. It is idempotent on flags but not on
component orientation; re-run `run_network.py` first if in doubt.

---

## The open problem: inferred links cut through buildings

This is the thing to fix next, and the user has flagged it directly.

`run_connect.py` exists because cutting each drawn run between its own manholes
left isolated chains — only 4% of junctions could reach an outfall. It adds:

| what | count | how |
|---|---|---|
| schedule links | 2,157 | a **straight 2-point chord** between manholes consecutive in the schedule |
| junction links | 124 | where two drawn runs physically touch |
| promoted outfalls | 203 | lowest node of a component with no outlet |
| reorientation | 2,202 | conduits reversed to point at the component outlet |

That lifted outfall reachability to 95%. But the schedule links are chords, not
routes. Measured against the drawn drain centrelines:

- median chord length **111 m**, max 399 m, mean **2.00 vertices** (traced
  conduits average 17.85)
- midpoint sits a median 9.7 m from any drawn drain, but **508 of them (24%) are
  more than 20 m off**, 116 more than 50 m, max 138 m

So roughly a quarter of them visibly cut across blocks and through houses
instead of following the road. Open the viewer, switch **Network** to
"Connected", and it is obvious.

Note the cross-ward joining is **not** the culprit — all 168 cross-ward conduits
have real traced geometry. It is the schedule infill.

**How to actually fix it.** Route the chords along the street network instead of
drawing them straight:

1. Take OSM roads for Chennai (`maps-site` has extracts, or pull fresh from
   Overpass). Build a routable graph.
2. For each `schedule_link`, snap both manholes to the graph and shortest-path
   between them.
3. Accept the route only if its length is within, say, 1.4x the straight chord —
   otherwise the two manholes probably are not connected along a road and the
   link should be dropped rather than invented.
4. Keep `geometry_inferred` in `flags` but add `road_routed` so the provenance
   stays visible.

Until that is done, `as_drawn/` is the honest dataset and `derived/` is the
routable-but-approximate one. Both ship, and the viewer toggles between them.

---

## SWMM: it runs clean and has no predictive skill

Dynamic wave, 30 Nov – 8 Dec 2015, 15 s routing step, 49 min wall clock. Mass
balance is excellent — runoff continuity **-0.004%**, flow routing **+0.038%**.
1,493 of 4,749 modelled nodes flood.

Scored against the flooded-street record (independent — never used to build or
tune the model): 807 hits, 686 false alarms, 1,834 misses, 1,422 correct dry.
Base rate 55.6%, precision 54.1%, **lift x0.97**. That is no better than chance.
Tightening the observation to 10 m does not help (x0.94); restricting to nodes
with surveyed flow direction does not help (x1.00 vs x0.95).

The diagnostic: **372 nodes flood for more than 120 hours of a 192-hour run.**
Those are hydraulic dead ends, not rainfall response. Only 27% of flooded nodes
flood for under 6 hours.

**Do not present `swmm_flood_hr` as a flood forecast.** It is a diagnostic for
finding dead ends. Getting to a predictive model needs surveyed flow directions,
calibrated subcatchment parameters (currently defaults: 75% impervious, 0.5%
slope, Horton), and outfall tailwater levels.

`run_analyse.py` had a parser bug that silently reported **zero** flooded nodes:
its section reader stopped at the second rule line, and SWMM puts the table
title between two asterisk banners. Fixed. If you write another report parser,
test it against a section you know has rows.

---

## The viewer (`web/`)

```bash
python3 -m http.server 8779 --bind 127.0.0.1 --directory web
```

MapLibre GL carries the basemap, projection and camera; a Three.js custom layer
draws into the same WebGL context. Roughly 1.3 M vertices, loads in ~6 s.

Things that will bite you if you edit it:

- **Frames.** MapLibre's mercator space is x-east, **y-SOUTH**, z-up. The scene
  is x-east, **y-UP**, z-south. The model matrix is therefore a *permutation*,
  not a scale (`MODEL` in index.html). Get this wrong and the whole city
  mirrors. The determinant is negative by necessity, so materials are
  `DoubleSide`.
- **`map.setStyle()` diffs the style and silently drops layers the new style
  does not declare.** Swapping basemaps that way deleted the ward boundaries
  every time. The style now starts empty and `applyBase()` swaps sources in
  place, which keeps the order `base -> swd -> wards -> labels` stable.
- **A plain `THREE.Camera` with a hand-built projection matrix cannot be
  raycast.** Picking projects all 5,941 chambers to screen space and takes the
  nearest. It is fast enough per click and forgives near-misses, which matters
  on touch.
- **Never grow vertex arrays with `Array.push`.** Building the conduit mesh that
  way took 15 s; preallocating typed arrays took it under 1 s.
- **Vertical exaggeration** applies fully to levels but is capped at 9x for
  cross-sections — a 0.6 m drain drawn 25x tall is a 15 m wall that hides the
  network behind it.
- **Two datums.** "Below street" subtracts each manhole's own `road_edge` so the
  translucent veil is the pavement; "Sea level" subtracts one city-wide mean
  (6.4 m MSL) so regional fall toward the coast survives. Under "Sea level" at
  high exaggeration the higher wards genuinely rise above the veil — that is
  topography, not a bug.
- **Locate-me needs a secure context.** Over plain HTTP the browser refuses
  geolocation outright and the button is dead. The page detects this and
  explains it in a toast, but if you want it to actually work, serve `web/`
  over HTTPS.
- Esri World Imagery stops at **z18** here and serves a "map data not available"
  tile past that, so its source is capped and overzooms instead. Google
  (`lyrs=y` hybrid / `lyrs=s` plain) goes deeper.

---

## Deliverables

```
as_drawn/          the network exactly as the sheets draw it — nothing stitched,
                   nothing inferred, no cross-ward joins.  GPKG / SHP / GeoJSON /
                   KML / CSV.  Start at as_drawn/DATA_DICTIONARY.md
derived/           the connected, routable build + SWMM model + report
web/               the 3D viewer
README.md          method, provenance, limitations
```

## Not in this upload

- **`.venv314/`** — platform-specific, rebuild it (see Environment above).
- **`derived/swmm/run2015.out`** — 271 MB binary time series. Regenerate with
  `./bin/runswmm` in ~49 min; `run2015.rpt` (2.9 MB) has all the summary tables
  and is included.

## Things that cost time, so you do not repeat them

- Claude Code's `preview_start` dev-server sandbox **cannot bind a port** on the
  original Mac — the process starts but connections are refused and
  `preview_logs` stays empty. Serve static files from a plain background
  `http.server` and point the browser at the URL instead.
- `curl` is blocked in that Bash sandbox; `urllib.request` works.
- Ward numbers: TNGIS zero-pads to three digits (`028`), the sheets do not
  (`28`). Compare as integers or you silently match only the 100+ wards.
- OCR of the schedule needs **700 DPI raw** — 600 DPI fails on shaded rows and
  binarising hurts thin glyphs.
- `swdlib/nodes.py:snap_id()` must **never** edit a manhole identifier's letter
  prefix. The prefix denotes a distinct drain run; "correcting" it merges
  unrelated runs.

---

## Addendum — 2026-09-04, Linux session (Fable)

Everything above still holds. What changed, in one place: `ARCHITECTURE.md`.
The short version:

- **Open channels exist.** The OCR never returns a lone `O`; those rows came
  back blank. `run_drain_type_vector.py` classifies the glyph from its vector
  path (open arc = `C`, closed loop = `O`) — 57 `O` rows found, 99.94% agreement
  with OCR on the `C` control. Do not "fix" `_clean()` in `swdlib/tables.py`; it
  was never the problem.
- **Chords are routed along OSM roads** (`run_route_chords.py`): 1,263 routed,
  37 short ones left straight, 857 dropped. Threshold is route/chord ≤ 1.5 — a
  drain going round a block corner is exactly √2, which 1.4 rejects.
- **The viewer is dimensionally honest now** — no floors, no defaults, one
  exaggeration factor, closed box culverts, oriented chambers with shafts and
  covers, ghosted elements where the section was not surveyed.
- **Linux toolchain**: `.venv-linux`, `run_swmm_linux.py` (swmm-toolkit),
  Playwright's Chromium at `~/projects/chennai-gps-camera/app/node_modules/playwright`
  for headless screenshots (`--use-angle=swiftshader`).
- **MapLibre traps found here**: `getFreeCameraOptions()` does not exist (Mapbox
  only); the far plane sits 1% past the ground so buried geometry vanishes at
  low pitch unless you rewrite the matrix's z row; three.js `GLSL3`
  ShaderMaterials must declare their own `out vec4` — `gl_FragColor` is not
  aliased on that path. All three are handled in `web/index.html`.
