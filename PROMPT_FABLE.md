# Prompt for Fable — make the SWD viewer dimensionally honest and physically real

Paste everything below the line into a fresh Claude Code session running Fable,
with the working directory set to the project root.

---

You are picking up the Chennai storm water drain (SWD) project. Read
`HANDOVER.md` first — it covers the environment, the pipeline order, and a set
of traps that are expensive to rediscover. Then read `README.md` and
`as_drawn/DATA_DICTIONARY.md`. Do not start editing until you have read all
three.

**Working directory:** `~/projects/swd` (also on the origin Mac at
`/Users/admin/Desktop/projects/swd`).

**Serve the viewer with:**
```bash
python3 -m http.server 8779 --bind 127.0.0.1 --directory web
```
Do not try to launch it through `preview_start` with a `launch.json` entry — on
the origin machine that sandbox starts the process but never binds the port.
Point the browser at the URL instead.

Your job has three parts, in priority order. **Part 1 is the one that matters
most**: the viewer currently draws the network at dimensions that are not the
surveyed ones, and the user has asked for exact fidelity to the sheets.

---

## Part 1 — Make the geometry exactly what the sheets say

`web/index.html` renders the network with several deliberate simplifications I
introduced for legibility. Each is a small lie about the data, and all of them
need to go or become honest and explicit.

### 1.1 Every closed drain is being drawn as an open channel — fix this first

The schedule's `drain_type` column returns **`C` (closed conduit) 7,952 times
across 9,324 rows and `O` (open channel) exactly zero times.** So every drain
whose type was read is a **closed rectangular box culvert**.

The current `buildConduits()` emits three quads per segment — a bed and two
walls — with **no soffit**. That is an open channel, and it is wrong for every
typed conduit in the dataset.

Draw a proper box culvert instead:
- floor at `us_invert`→`ds_invert` (linearly interpolated along the run)
- clear internal height = `depth_m`, so the soffit sits at invert + `depth_m`
- internal width = `width_m`
- add wall thickness if you want, but do not invent it — the sheets do not give
  it, so either omit it or make it a clearly-labelled constant

The soffit must not hide the inside. Use a cutaway: render the roof
back-face-only, or clip the near half, or make it fade with camera angle. The
point of the viewer is seeing the buried network, so a solid lid defeats it.
Pick one approach and make it look deliberate.

**Verify the type reading before you trust it.** The OCR never once returned
`O`, and there are ~14 garbage rows (`'UUUUUUU'`, `'UU'`, `'-C'`) that look like
a misread symbol. Open five or six of the source PDFs in `GeoPDF/`, find the
"LIST OF POINTS" table, and check by eye whether open channels genuinely do not
appear in these 107 wards or whether `swdlib/tables.py` is systematically
dropping `O`. Report which it is. If open channels do exist, they must render
without a soffit.

### 1.2 Remove every dimension floor and default

These all currently substitute a plausible number for a missing or small one:

```js
const CH_MIN_W = 0.9, CH_MIN_H = 0.5;                    // chamber floors
const w = Math.max(n.wd ?? 1.0, CH_MIN_W);               // chamber width
const h = Math.max(chH(n), CH_MIN_H);                    // chamber height
const hw  = Math.max(cd.wd ?? 0.7, 0.5) / 2;             // conduit half-width
const dep = Math.max(cd.dp ?? 0.6, 0.35);                // conduit depth
```

Real surveyed values run from **0.121 m to 10.159 m** wide and **0.050 m to
3.023 m** deep. Flooring at 0.5 m and 0.9 m visibly inflates the small ones, and
`?? 0.7` invents a dimension where none was surveyed.

Replace with: render the exact surveyed value, and where a dimension is
**missing**, render that element in a visually distinct "not surveyed" state —
wireframe, or desaturated and semi-transparent — rather than guessing. This
matters because coverage is not universal:

- **4,817 of 5,941** manholes have a surveyed `width`; **4,749** have `depth`
- **4,637 (78%)** manholes are fully dimensioned (invert + top + road_edge +
  width + depth)
- **4,685 of 6,370 (74%)** conduits are fully dimensioned

So roughly a quarter of what you draw has no surveyed cross-section. Make that
visible instead of papering over it, and put a count in the legend.

### 1.3 Cross-sections must use the same exaggeration as levels

```js
const SEC_CAP = 9;
const secEx = () => Math.min(zEx, SEC_CAP);
```

I capped cross-section exaggeration at 9x while levels take the full factor,
because a 0.6 m drain drawn 25x tall becomes a 15 m wall that hides the network
behind it. That is a legibility hack that makes the rendering dimensionally
inconsistent — the drain is not drawn at its true proportions.

Fix it properly: use **one** exaggeration factor for both, so proportions are
always true. Solve the wall problem with rendering rather than distortion —
transparency, cutaway, depth fade, or a lower default factor. If you keep any
separate section control, label it explicitly as breaking true proportion and
default it to 1:1 with the level factor.

Add a clearly-marked **"true scale (1:1)"** button that sets exaggeration to 1
so an engineer can check real proportions at any time.

### 1.4 Chambers: right shape, right orientation

Chambers are currently axis-aligned cubes scaled `w × h × w`. Two problems:

- **Orientation.** A box drain runs along the street. Rotate each chamber to the
  bearing of its connected conduit(s) — average the bearings where several meet.
  An axis-aligned box on a diagonal street is obviously wrong.
- **Plan dimensions are not surveyed.** The schedule gives the *drain* width and
  depth at that point, not the chamber's plan size. So do not present a square
  `w × w` footprint as if it were measured. Draw the chamber as the drain
  cross-section (`width_m` across flow, `depth_m` tall) extruded a short,
  explicitly-constant distance along the drain, plus the access shaft.

**The shaft is real and currently missing.** `road_edge - drain_top` is the
cover depth — surveyed, and currently drawn as a 1-pixel `LineSegments`. Model it
as an actual vertical shaft with a manhole cover disc at road level. Median
cover is small and some are negative (the drain stands proud of the kerb) — keep
handling that case; it is real, not an error.

### 1.5 Invert profile through the run

Conduit z interpolates linearly between `us_invert` and `ds_invert` only. Where
a drawn run passes intermediate manholes with their own surveyed inverts, use
them as breakpoints so the longitudinal profile steps at each chamber the way
the real drain does.

---

## Part 2 — Make it physically real

Only after Part 1 is correct. Realism applied to wrong dimensions is worse than
useless.

### 2.1 A real ground surface

The "ground veil" is a single flat translucent plane. **5,355 manholes carry a
surveyed `road_edge` level.** Build a proper ground surface from those — a
Delaunay TIN, or a coarse grid interpolated from them — so the drains sit
beneath actual terrain that rises and falls. Drape the satellite imagery on it,
or keep it translucent so the network reads through. This single change does
more for realism than any material work.

Keep the existing two-datum behaviour working (see HANDOVER.md); with a real
surface, "Below street" becomes literally true.

### 2.2 Materials and light

- PBR materials: damp concrete for culvert walls and floor, cast iron for
  covers, with roughness and normal variation. Currently everything is
  `MeshLambertMaterial`.
- Shadow mapping plus ambient occlusion in the corners where chambers meet
  conduits — those junctions currently interpenetrate with no shading cue.
- Fix the interpenetration itself: chambers and conduits should join, not
  overlap.
- Depth cue: darken and desaturate with distance below ground so depth reads
  without needing the colour ramp.

### 2.3 Performance budget

The current mesh is ~1.3 M vertices and loads in about 6 s. Closed box culverts
plus shafts and covers will push it well past that. Before you add geometry,
put in place: frustum-culled spatial chunking, an LOD that drops shafts and
covers at low zoom, and instancing for covers and chambers. Target 60 fps on a
mid-range phone and a first load under 5 s on a 4G connection. Measure both and
report the numbers; do not assume.

Never grow vertex arrays with `Array.push` — preallocate typed arrays. Doing it
the naive way cost 15 s of load time here before it was fixed.

---

## Part 3 — Animate the real simulated event, not a decorative pulse

The current flow animation is a shader band travelling from `from_uid` to
`to_uid`. It shows direction correctly and nothing else — the speed is
arbitrary and there is no water.

There is a real SWMM run available: dynamic wave, **30 Nov – 8 Dec 2015**, 15 s
routing step, mass balance clean (runoff continuity −0.004%, routing +0.038%).
Its binary output carries **per-timestep depth at every node and flow in every
link**. Use it:

- water surface inside each culvert at the simulated depth, clamped to the
  soffit, surcharging up the shaft when the node floods
- flow speed driven by the simulated discharge, not a constant
- a timeline scrubber across the 8-day event, with the rainfall hyetograph from
  `web/data/rainfall.json` underneath it
- play / pause / speed control

**`derived/swmm/run2015.out` is NOT in this upload** — it is 271 MB and was
excluded. Regenerate it:
```bash
./bin/runswmm derived/swd_model.inp derived/swmm/run2015.rpt derived/swmm/run2015.out
```
That takes about 49 minutes. `run2015.rpt` (2.9 MB) *is* included and has all
the summary tables. To read the binary, `swmm-toolkit` works fine on Linux; on
Apple Silicon the pip wheels SIGKILL on import because their dylibs are ad-hoc
signed — use the source-built `bin/runswmm` and parse the `.out` yourself, or
work on Linux.

Do not ship the raw 271 MB file to the browser. Downsample to a sensible
reporting interval, keep only depth per node and flow per link, and pack it as
typed arrays the way `run_web_export.py` packs the network.

### The one thing you must not get wrong here

**This model has no predictive skill.** Scored against the independent
flooded-street record: 807 hits, 686 false alarms, 1,834 misses, 1,422 correct
dry. Base rate 55.6%, precision 54.1% — **lift x0.97**, no better than chance.
372 nodes flood for more than 120 hours of a 192-hour run, which means they are
hydraulic dead ends, not rainfall response.

So label the animation **"simulated"** everywhere in the UI, never "observed" or
"predicted". Do not add anything that implies forecast accuracy. A beautiful,
convincing animation of an unskilled model is actively misleading, and this data
is going to public-sector users. If you cannot make the caveat legible in the
UI, do not ship the animation.

---

## Also fix

- **Straight chords through buildings.** 2,157 conduits are 2-point straight
  lines between manholes consecutive in the schedule; **508 of them sit more
  than 20 m from any drawn drain**, cutting across blocks and through houses.
  HANDOVER.md has the approach: snap both ends to an OSM road graph,
  shortest-path between them, and *reject* the link if the route exceeds about
  1.4x the straight chord rather than inventing a connection. Flag routed ones
  `road_routed` so provenance stays visible. This is the user's top-priority
  data complaint.
- **1,589 conduits run uphill** (33.7% of those with a slope). They are coloured
  red in Gradient mode but nothing else marks them. Surface them in 3D — they
  are either silted sumps or survey noise and an engineer needs to find them.
- **Esri imagery stops at z18** here and serves a "map data not available" tile
  past it; the source is capped and overzooms. Google (`lyrs=y`) goes deeper.
- **Locate-me needs HTTPS.** Over plain HTTP the browser refuses geolocation and
  the button is dead. The page detects this and explains it in a toast. If the
  deployment should have working geolocation, it needs TLS.

## Do not undo these — each cost real time to get right

- **The `MODEL` matrix is a permutation, not a scale.** MapLibre mercator space
  is x-east, **y-south**, z-up; the scene is x-east, **y-up**, z-south. Get this
  wrong and the whole city mirrors. Its determinant is negative by necessity, so
  materials are `DoubleSide`.
- **Never swap basemaps with `map.setStyle()`.** It diffs the style and silently
  drops layers the new style does not declare — it deleted the ward boundaries
  on every switch. `applyBase()` swaps sources in place and keeps the order
  `base → swd → wards → labels`.
- **A plain `THREE.Camera` with a hand-built projection matrix cannot be
  raycast.** Picking projects all chambers to screen space and takes the
  nearest. If you add geometry, keep picking working — including on touch, where
  the tolerance is deliberately larger.
- **Keep the As drawn / Connected toggle honest.** "As drawn" must show only
  conduits traced off a sheet, with no cross-ward joins and no promoted
  outfalls. `as_drawn/` is the ground-truth export and the two must agree.
- **`swdlib/nodes.py:snap_id()` must never edit a manhole identifier's letter
  prefix** — it denotes a distinct drain run, and "correcting" it merges
  unrelated runs.

## When you are done

Show me, with screenshots:
1. A closed box culvert in section, with its surveyed `width_m × depth_m`
   annotated, next to the corresponding row of the PDF schedule — so I can check
   the rendered dimensions against the sheet directly.
2. The same view at true scale (1:1) and at the default exaggeration.
3. A chamber with its shaft and cover, against the surveyed `road_edge`,
   `drain_top` and `invert`.
4. Before/after of a run that currently cuts through buildings.
5. Measured frame rate and first-load time on a mobile viewport.

And tell me plainly which of the numbers you drew are surveyed and which are
assumptions you had to make.
