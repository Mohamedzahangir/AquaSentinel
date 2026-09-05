# Chennai storm water drain (SWD) network — extracted from GCC ward base maps

**Live 3D viewer: https://swd.reclaimchennai.city** · maps in [`maps/`](maps) · GIS data in [`as_drawn/`](as_drawn) (ground truth) and [`derived/`](derived) (connected build) as GeoPackage / GeoJSON / KML / CSV · **shapefiles and the 107 source PDFs are release assets: [v1.0](https://github.com/reclaimchennai/chennai-swd/releases/tag/v1.0)** (GitHub's file-size cap).

## Sources and credits

- **Drain sheets**: Greater Chennai Corporation / SECON-JBA, "BASE MAP – STORM WATER DRAIN", one sheet per ward, 2021 survey (World Bank PDGF flood-forecasting project). 107 of ~200 wards. The PDFs are not georeferenced and contain no selectable text; every coordinate here comes from fitting each sheet's printed 500 m UTM grid (median residual 4.7 cm) and every number from OCR of the schedule, checked 100 % on a held-out sheet.
- **Flooded-street record (Dec 2015)**: [osm-in/flood-map](http://github.com/osm-in/flood-map).
- **Roads** used to route the inferred links and as map context: © OpenStreetMap contributors, ODbL.
- **Ward boundaries**: GCC pre-2022 delimitation (the one the 2021 sheets use — checked against the ward lines traced off the sheets: median 6 m; the 2022 re-delimitation is >150 m off in 33 wards), via OpenDataChennai, clipped to land with the OSM coastline (`run_wards.py`). **Rainfall**: NOAA GHCN-Daily (IMD Meenambakkam) daily totals, ERA5 hourly shape.
- **Reference GIS**: GCC drain layer published on [data.opencity.in](https://data.opencity.in) — used only to cross-check alignment (see "Accuracy" below), not merged.
- Extraction, model, viewer and maps: **@reclaimchennai**. Code: MIT. Derived data: CC BY 4.0, credit "GCC SWD sheets, extracted by @reclaimchennai". The PDFs are GCC's; they are mirrored in the release for verification.

## Accuracy — what to trust

Every level, dimension and drain type is exactly what the sheet's schedule says; `*_raw` columns keep the OCR text so any value can be audited. Geometry is what the sheet draws. Two independent references — OSM road centrelines and GCC's own drain GIS — put the median as-drawn vertex **5 m** from the nearest road/drain, but the following sheets sit **12–20 m off** as a block: wards **95, 103, 108, 112, 133, 140** (also 177, 60 vs the GCC layer). Their grid fits are sound (0.05–1.2 m rms), so the offset is in the sheet content itself, not in the georeferencing; the data is left as drawn and those wards are flagged here rather than silently shifted. Drain type: the OCR never returns a lone `O`; open channels were recovered from vector glyph geometry (57 rows, 99.94 % agreement with the OCR control).

Everything is **EPSG:32644** (WGS 84 / UTM zone 44N), the CRS the sheets declare.

---

## Outputs

```
derived/
  chennai_swd.gpkg        all layers in one GeoPackage      <- easiest to use
  chennai_swd.qgz         QGIS project, styled
  shp/                    the same layers as flat shapefiles
    swd_drains.shp          drain network, city-wide continuous runs
    swd_nodes.shp           manholes: position, levels, dimensions, flood history
    swd_conduits.shp        directed links, incl. cross-ward
    swd_subcatchments.shp   Thiessen cell per manhole
    map_text.shp            street / place names read off the sheets
    map_context.shp         roads, ward + zone boundaries, water, rail
  swd_model.inp           SWMM 5 model
  swmm/run2015.rpt        SWMM report for the Dec 2015 event
  network/*.json          the same data as JSON (pipeline intermediate)
  affines.json            page->UTM transform solved for every sheet
  qa_report.json          per-ward and city-wide quality figures

web/
  index.html              3D viewer — MapLibre + Three.js over satellite imagery
  data/network.json       the network packed for the browser (4.0 MB)
  data/wards.json         GCC ward outlines, tagged with whether we have a sheet

as_drawn/                 the network exactly as the sheets draw it  <- ground truth
  DATA_DICTIONARY.md      every field, its units and its provenance
  gpkg/ shp/ geojson/ kml/ csv/
```

## Two datasets, and which one you want

| | `as_drawn/` | `derived/` |
|---|---|---|
| conduits | **3,959** — every one traced off a sheet | 6,370 |
| outfalls | **247**, each marked `(OL)` on a sheet | 450 |
| cross-ward links | none | 168 |
| geometry | follows the drawn centreline, ~17.9 vertices per conduit | 2,157 conduits are straight 2-point chords |
| routable? | no — runs stop at ward boundaries | yes, 95% of nodes reach an outfall |

Use `as_drawn/` when you need ground truth or are checking our work against the
PDFs. Use `derived/` when you need something that drains. `run_as_drawn.py`
derives the first from the second by stripping the inferred connections back
out; nothing is re-extracted.

**A caveat on `derived/`.** The 2,157 straight chords are assumptions, and about
a quarter of them (508) sit more than 20 m from any drawn drain — they cut
across blocks rather than following the road. Routing them along the street
network is the main outstanding job; see HANDOVER.md.

### The 3D viewer

Live at <https://swd.reclaimchennai.city>. Locally:

```bash
python3 -m http.server 8779 --bind 127.0.0.1 --directory web
```

Every typed conduit is drawn as a closed box culvert at its surveyed
`width_m × depth_m`, floor at the invert, soffit at invert + depth, with the lid
fading as you look down into it. Chambers are the drain section extruded a
labelled 1.2 m along the run and rotated to it, with the access shaft
(`road_edge − soffit`) and a cover disc at road level. Nothing is floored or
defaulted: elements with no surveyed section are ghosted at a labelled
placeholder and counted in the legend. One vertical exaggeration factor applies
to levels and sections alike (`1:1` button for true scale). Under "Sea level" the
network sits beneath a TIN of the 5,355 surveyed road-edge levels. The Dec 2015
SWMM run can be loaded as water inside the culverts — it is labelled
*simulated* everywhere because it has no predictive skill. Full detail in
`ARCHITECTURE.md`.

### `swd_nodes` — the manholes

| field | meaning |
|---|---|
| `uid` | **globally unique** name, `W160_A1` — join on this, never `node_id` |
| `node_id`, `ward`, `si` | identifier as printed, ward, serial in the schedule |
| `drain_top` | MSL level of the drain top, m |
| `invert` | **MSL invert level, m** — the bed of the drain |
| `road_edge` | MSL at the road edge, m (ground surface) |
| `width_m`, `depth_m` | drain cross-section |
| `type` | `C` closed conduit, `O` open channel |
| `is_outlet` | manhole marked `(OL)` — a discharge point |
| `microws`, `subbasin` | published microwatershed / river sub-basin it falls in |
| `flood_m`, `flood_road` | distance to the nearest historically flooded street |
| `locator` | `id` (matched by identifier) or `invert` (matched by invert value) |
| `conf`, `snap_m`, `flags` | OCR confidence, label-to-centreline offset, QA flags |

### `swd_conduits` — the links

`from_uid` → `to_uid` is **directed downstream**. Carries `us_invert`,
`ds_invert`, `slope`, `width_m`, `depth_m`, `type`, `length_m`, and
`cross_ward = 1` where the link spans a ward boundary. `dir_src` records how
direction was decided: `invert` (surveyed fall), `arrow` (the sheet's flow
arrow), `assumed`.

---

## How it was done

1. **Exact georeferencing.** Each sheet prints a 500 m UTM grid with eastings
   and northings labelled around the frame. Grid lines are found in the vector
   content, the labels read by OCR, each label tied to the line it annotates,
   and `E = a·u + b`, `N = c·v + d` least-squares fitted. Median residual
   **4.7 cm**; the scale comes out 1:5000 on every sheet. Sheets plotted in
   landscape carry a `/Rotate 270` flag, which is irrelevant here because E and
   N are fitted against whichever page axis carries them.

2. **The schedule.** The "LIST OF POINTS" tables are vector cell grids with the
   text as glyph outlines (AutoCAD SHX fonts — nothing is selectable text). Row
   and column boundaries come from the line work; OCR only fills cells.
   Hand-checked against a sheet not used for tuning: **100% of numeric values
   correct**.

3. **Positions.** Manhole identifiers are glyph outlines on a `Drain Nos` layer.
   Glyphs are clustered back into words (exact position), then read. Identifiers
   are snapped onto the set the schedule contains, never editing the letter
   prefix — that denotes a distinct drain run. Where an identifier is unreadable
   or a sheet carries no identifier layer, the manhole is located instead by
   matching the map's MSL invert annotation against the schedule's invert column.

4. **One city-wide network** (`run_network.py`). Per-sheet processing stops at
   the ward boundary, so a drain continuing into the next ward arrives as two
   halves. Each sheet also draws a margin of its neighbours, so boundary pipes
   arrive twice — and those duplicates are what stop the halves joining. So the
   pieces are de-duplicated first, then stitched into continuous runs, then every
   manhole in the city is snapped onto that single network and the runs cut
   between consecutive manholes. Conduits therefore cross ward boundaries
   wherever the drain does.

5. **Validation** (`swdlib/validate.py`). Recoverable OCR damage is repaired —
   the schedule always prints three decimals, so a bare `180` in a dimension
   column is a lost decimal point and means 0.180 m. Impossible values (negative
   depth, invert above the drain top) are nulled or flagged rather than guessed,
   and the reason lands in the `flags` column.

6. **Subcatchments.** The published microwatershed layer covers only 58% of the
   GCC area, so runoff is instead assigned by Thiessen cell — the area closer to
   one manhole than any other, clipped to the city. Where a published
   microwatershed does exist the cell is tagged with it.

7. **Making it drain** (`run_connect.py`). Cutting each drawn run between its own
   manholes leaves isolated chains — only 4% of junctions could reach an outfall.
   Three passes fix it: conduits where two runs physically touch; conduits
   between manholes consecutive in the schedule (`A2`→`A3` is one pipe even where
   the drawing could not be traced — flagged `geometry_inferred`); and, for any
   component still without one, the lowest node promoted to an outfall
   (`synthetic_outfall`). Each component is then oriented along its tree toward
   that outlet, because per-conduit invert comparison leaves local sinks that
   strand everything upstream. **95% of nodes now reach an outfall.**

8. **Rainfall** (`run_rainfall.py`). Reanalysis and satellite products miss the
   2 Dec 2015 peak by about six times (ERA5 49 mm, CHIRPS 23 mm, against 345 mm
   observed) — driving a run off them would under-predict the flood silently.
   So daily volume comes from the Meenambakkam gauge (NOAA GHCN, cross-checked
   against the IMD 0.25 deg grid: 345/350 mm) and the hourly *shape* from ERA5,
   rescaled per day. The Indian rain-day runs 08:30–08:30 IST while ERA5 runs
   midnight–midnight, so the window is shifted before rescaling. Where ERA5 saw
   almost none of a day's volume its profile is blended toward uniform, since
   rescaling a sliver by 157x invents a cloudburst that never happened.

9. **Running it** (`run_analyse.py`). Dynamic wave routing, 30 Nov – 8 Dec 2015,
   15 s routing step; 49 minutes of wall clock. Mass balance is clean — runoff
   continuity **-0.004%**, flow routing continuity **+0.038%**. 1,493 of the
   4,749 modelled nodes flood.

---

## Does the model reproduce the 2015 flood? No.

Scored against the flooded-street record — an independent observation, never
used to build or tune the model — the run has **no skill at all**:

|  | observed flooded | not observed |
|---|---|---|
| **model floods** | 807 | 686 |
| **model dry** | 1,834 | 1,422 |

Base rate 55.6%, precision 54.1% — **lift x0.97**. The model's flooded nodes are
no more likely to be on a flooded street than a node picked at random. Detection
30.6%, false alarm ratio 45.9%, CSI 24.3%. Tightening the observation to 10 m of
a flooded street does not help (lift x0.94), and neither does restricting to
nodes whose flow direction was actually surveyed rather than imposed (x1.00
against x0.95) — so the imposed directions are not the sole cause.

The diagnostic that matters: **372 nodes flood for more than 120 hours of a
192-hour run**, a quarter of all flooded nodes. Those are not responding to
rainfall, they are hydraulic dead ends with nowhere to discharge. Only 27% of
flooded nodes flood for under 6 hours, which is what an event response looks
like.

So `swmm_flood_hr` is **not** a flood forecast and must not be presented as one.
What the run is genuinely good for is finding those dead ends — places where the
extracted network has no viable outlet, each of which is either a real bottleneck
or a defect in the extraction worth checking against the sheet. Before the model
can predict anything it needs surveyed flow directions, calibrated subcatchment
parameters, and outfall tailwater levels.

---

## Provenance of the supporting data

| what | from |
|---|---|
| ward boundaries | `cumta/repos/GCC_Flood_Inter_dept` |
| microwatersheds, sub-basins | `maps-site/data/layers` (TNGIS) |
| historically flooded streets | `maps-site/old/data/chennai_flood.geojson` |

---

## Known limitations

- **Flow direction is partly imposed, not observed.** Surveyed inverts alone
  leave local sinks, so each component is oriented toward its outlet; 2,202
  conduits were reversed against their invert gradient and carry
  `reversed_against_invert`. 1,589 conduits consequently run uphill — some are
  genuine silted sumps, some are survey noise. `dir_src` distinguishes `invert`
  (surveyed) from `network` (imposed). Review these before trusting routing.
- **403 of the outfalls are synthetic** — the lowest node of a component with no
  surveyed outlet, flagged `synthetic_outfall`. Only 247 are marked `(OL)` on
  the sheets.
- **2,157 conduits have inferred geometry** (`schedule_link;geometry_inferred`):
  the connection is real, the straight line between the manholes is not.

- **`swd_conduits` is the attributed subset.** A conduit exists only where two
  identified manholes sit on the same drawn run. `swd_drains` carries the
  complete centreline geometry — use that for extent, conduits for hydraulics.
- **Only 107 of ~200 GCC wards** have sheets in `GeoPDF/`. Roughly half of the
  city has no coverage here.
- **`drain_type` (C/O)** is ~90% complete; a lone letter in a wide cell is the
  one thing the recogniser regularly drops.
- **Subcatchment parameters are defaults**, not surveyed: 75% impervious, 0.5%
  slope, Horton infiltration. Calibrate before trusting absolute volumes.
- **Flooded-street data is an observation, not a depth surface** — it says where
  water stood, which makes it a check on the model, not an input to it.
- **`swmm_flood_hr` / `swmm_flood_Ml` / `agree` are diagnostics, not predictions.**
  The run has no skill against the flood record (see above). Use them to find
  nodes that never drain, not to say where the city will flood.

## Running it

```bash
python run_georef.py     # page->UTM transform for every sheet
python run_extract.py    # per-ward extraction        (~25 min, 6 workers)
python run_labels.py     # street / place names       (~12 min)
python run_network.py --no-clip   # city-wide network, cross-ward links
python run_floodhist.py  # tag manholes by historical flooding
python run_connect.py    # make it drain: junction links, outfalls, orientation
python run_rainfall.py   # hybrid gauge + ERA5 hourly series
python run_export.py     # GeoPackage + shapefiles + SWMM
./bin/runswmm derived/swd_model.inp derived/swmm/run2015.rpt derived/swmm/run2015.out
python run_analyse.py    # score the run against the flood record  (~49 min above)
python run_web_export.py # pack the network for the 3D viewer
```
