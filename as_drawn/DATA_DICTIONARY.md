# Chennai SWD — as drawn on the sheets

The storm water drain network exactly as the 107 GCC / SECON-JBA ward base map
PDFs draw it. **Nothing stitched, nothing inferred, nothing joined across ward
boundaries.** Every line here was traced from linework on a sheet; every number
was read off that sheet's own schedule.

CRS **EPSG:32644** (WGS 84 / UTM zone 44N) for GeoPackage, Shapefile and CSV.
GeoJSON and KML are reprojected to **EPSG:4326** as those formats require.
All levels are metres above mean sea level (MSL), all dimensions metres.

## What this is *not*

The main pipeline (`../derived/`) adds connections so the network can be routed
hydraulically. Those additions are **removed here**:

| removed | count | what it was |
|---|---|---|
| straight-line schedule links | 2,157 | a 2-point chord between manholes consecutive in the schedule, where the drawing could not be traced. Median 111 m long, up to 399 m. **508 of them sit more than 20 m from any drawn drain** — they cut across blocks instead of following the road. |
| junction links | 124 | a link added where two drawn runs happened to touch |
| cross-ward conduits | 130 | conduits spanning a ward boundary after the city-wide merge |
| promoted outfalls | 203 | the lowest node of a component with no surveyed outlet, promoted so it could discharge |

So `swd_conduits_as_drawn` has **3,959** conduits against 6,370 in the connected
network, and `is_outlet` is true for the **247** manholes actually marked `(OL)`
on a sheet, not 450.

Use this export when you want ground truth. Use `../derived/` when you need
something that drains.

## Formats

```
gpkg/     chennai_swd_as_drawn.gpkg     all five layers in one file  <- easiest
shp/      one .shp per layer, plus chennai_swd_as_drawn_shapefiles.zip
geojson/  the four SWD layers, EPSG:4326
kml/      the four SWD layers, for Google Earth
csv/      attribute tables, plus the raw schedule transcription
```

`map_context_as_drawn` is GeoPackage and Shapefile only — it is bulky and rarely
wanted outside a GIS.

## Layers

### `swd_nodes_as_drawn` — 5,941 manholes (Point Z, Z = invert)

| field | meaning | source |
|---|---|---|
| `uid` | globally unique, `W160_A1` — **join on this**, never `node_id` | derived |
| `node_id` | identifier as printed on the sheet | PDF |
| `ward` | GCC ward number | PDF |
| `si` | serial number in the schedule | PDF |
| `drain_top` | MSL level of the drain top, m | PDF schedule |
| `invert` | **MSL invert level, m** — the bed of the drain | PDF schedule |
| `road_edge` | MSL at the road edge, m (ground surface) | PDF schedule |
| `width` | drain width, m | PDF schedule |
| `depth` | drain depth, m | PDF schedule |
| `drain_type` | `C` closed conduit, `O` open channel | PDF schedule |
| `is_outlet` | 1 where the sheet marks the manhole `(OL)` | PDF schedule |
| `locator` | how the manhole was placed: `id` (matched by identifier) or `invert` (matched by invert value) | derived |
| `conf` | OCR confidence of the identifier, 0–1 | derived |
| `snap_m` | distance from the label to the drain centreline, m | derived |
| `raw_label` | the identifier exactly as OCR read it, before snapping | derived |
| `flags` | QA notes from `swdlib/validate.py` | derived |
| `microws` | published microwatershed it falls in | TNGIS |
| `subbasin` | river sub-basin | TNGIS |
| `flood_m` | distance to the nearest historically flooded street, m | maps-site |
| `flood_road` | class of that street | maps-site |

4,817 of 5,941 carry surveyed `width`/`depth`; 4,790 carry an `invert`.

### `swd_conduits_as_drawn` — 3,959 links (LineString Z, Z interpolated invert)

`from_uid` → `to_uid` is directed downstream. Geometry follows the drawn
centreline — mean 17.9 vertices per conduit, so it bends with the road.

`cond_id`, `from_uid`, `to_uid`, `from_ward`, `length_m`, `us_invert`,
`ds_invert`, `width_m`, `depth_m`, `type`, `slope`, `dir_src`, `flags`.

`dir_src` records how direction was decided — `invert` (surveyed fall) or
`arrow` (the sheet's flow arrow). Direction imposed by network orientation is
not present here, because that pass belongs to the connected build.

### `swd_drains_as_drawn` — 10,223 drawn centreline fragments (LineString)

Every drain path on every sheet, unmerged. `ward`, `role`, `cad_layer` (the
originating AutoCAD layer name). This is the most faithful record of geometry in
the whole project — use it for extent and alignment, conduits for hydraulics.

### `map_text_as_drawn` — 13,039 labels (Point)

Street and place names read off the sheets. `ward`, `role`, `text`, `conf`,
`size_pt`.

### `map_context_as_drawn` — 754 features (MultiLineString)

Roads, ward and zone boundaries, railway, flyover, divider, canal, bridge, pump —
grouped into one multi-geometry per ward/role/CAD layer, `n_paths` recording how
many original strokes went in. River and sea are excluded: on these sheets they
are hatch fill, several hundred thousand short strokes shading a water body
rather than a centreline. They remain in `../derived/chennai_swd.gpkg`.

### `csv/schedule_list_of_points.csv` — 9,324 rows

The "LIST OF POINTS" tables transcribed. Each column appears twice: `*_raw` is
exactly what OCR returned, the bare name is the parsed and validated value. Keep
both — the raw column is how you audit a suspicious number.

## How the geometry got here

Text on these sheets is AutoCAD SHX glyph outlines, not selectable text, so
every value was OCR'd (macOS Vision) out of a vector cell grid. Georeferencing
comes from each sheet's own printed 500 m UTM grid, least-squares fitted; median
residual **4.7 cm**. Hand-checked against a sheet held out of tuning, **100% of
numeric schedule values were correct**.

## Known limits

- **107 of ~200 GCC wards.** Roughly half the city has no sheet here.
- `drain_type` is ~90% complete — a lone letter in a wide cell is the one thing
  the recogniser regularly drops.
- Because nothing is joined, the network is **not routable**. Runs stop at ward
  boundaries and wherever the drawing was too broken to trace.
