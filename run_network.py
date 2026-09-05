#!/usr/bin/env python3
"""
Build ONE city-wide drain network from the per-ward extractions.

Per-sheet processing stops at the ward boundary, so a drain continuing into the
neighbouring ward arrives as two disconnected halves. Here the sheets are
combined first and the network derived afterwards:

  1. each ward's drain linework is clipped to its own ward polygon, so the
     overlap where adjacent sheets both draw the same pipe is removed and each
     length of drain has exactly one owner;
  2. the clipped pieces are stitched into continuous runs — halves that met at
     a boundary now join, because they share an endpoint on that boundary;
  3. every manhole in the city is snapped onto that unified network and the runs
     are cut between consecutive manholes, which yields conduits that cross ward
     boundaries wherever the drain does.

Node attributes are repaired/validated on the way through (see swdlib.validate).
"""
import json
import math
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from osgeo import ogr, osr  # noqa: E402

from swdlib.network import merge_lines, project_onto_line, slice_line, MIN_LEN  # noqa: E402
from swdlib.validate import check_node, check_conduit  # noqa: E402
from swdlib.catchments import voronoi_cells, swmm_subcatchment_params  # noqa: E402

ogr.UseExceptions()

ROOT = "/Users/admin/Desktop/projects/swd"
WARDS = f"{ROOT}/derived/wards"
OUT = f"{ROOT}/derived"
WARD_GEOJSON = "/Users/admin/Desktop/projects/cumta/repos/GCC_Flood_Inter_dept/data/GCCWardBoundary_2.js"
MICROWS = "/Users/admin/Desktop/projects/maps-site/data/layers/microwatersheds.geojson"
SUBBASINS = "/Users/admin/Desktop/projects/maps-site/data/layers/sub_basins.geojson"

SNAP_TOL = 35.0
CLIP_BUFFER = 20.0       # m; keep a little of the neighbour so boundary drains meet
MAX_SPAN = 600.0


def utm_srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(32644)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


def wgs_srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(4326)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


def load_ward_polygons():
    txt = open(WARD_GEOJSON).read()
    data = json.loads(txt[txt.index("{"):].rstrip(";\n "))
    ct = osr.CoordinateTransformation(wgs_srs(), utm_srs())
    out = {}
    for feat in data["features"]:
        m = re.match(r"Ward\s+(\d+)$", feat["properties"].get("Name", ""), re.I)
        if not m:
            continue
        mp = ogr.Geometry(ogr.wkbMultiPolygon)
        for pg in feat["geometry"]["coordinates"]:
            poly = ogr.Geometry(ogr.wkbPolygon)
            for ring in pg:
                r = ogr.Geometry(ogr.wkbLinearRing)
                for c in ring:
                    r.AddPoint(c[0], c[1])
                poly.AddGeometry(r)
            mp.AddGeometry(poly)
        mp.Transform(ct)
        out[int(m.group(1))] = mp.Buffer(CLIP_BUFFER)
    return out


def load_polygon_layer(path, name_fields):
    """Polygons from a WGS84 GeoJSON, in UTM, as [(label, geometry)]."""
    if not os.path.exists(path):
        return []
    data = json.load(open(path))
    ct = osr.CoordinateTransformation(wgs_srs(), utm_srs())
    out = []
    for feat in data["features"]:
        g = ogr.CreateGeometryFromJson(json.dumps(feat["geometry"]))
        if g is None:
            continue
        g.Transform(ct)
        props = feat.get("properties", {})
        label = next((str(props[f]) for f in name_fields if props.get(f) not in (None, "")), "")
        out.append((label, props, g))
    return out


def load_ward_drains(ward_polys, clip=True):
    """Per-ward drain linework, clipped to the owning ward."""
    lines = []
    kept = clipped = 0
    for d in sorted(os.listdir(WARDS)):
        m = re.match(r"ward_(\d+)$", d)
        if not m:
            continue
        ward = int(m.group(1))
        shp = os.path.join(WARDS, d, "swd_drains.shp")
        if not os.path.exists(shp):
            continue
        poly = ward_polys.get(ward)
        ds = ogr.Open(shp)
        lyr = ds.GetLayer()
        for f in lyr:
            g = f.GetGeometryRef()
            if g is None:
                continue
            if clip and poly is not None:
                try:
                    g = g.Intersection(poly)
                except Exception:
                    continue
                if g is None or g.IsEmpty():
                    clipped += 1
                    continue
            for part in (g if g.GetGeometryName().startswith("MULTI") else [g]):
                if part.GetPointCount() < 2:
                    continue
                pts = [(part.GetX(i), part.GetY(i)) for i in range(part.GetPointCount())]
                if sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)) < 1.0:
                    continue
                lines.append(pts)
                kept += 1
        ds = None
    return lines, kept, clipped


def load_nodes():
    nodes = []
    for d in sorted(os.listdir(WARDS)):
        p = os.path.join(WARDS, d, "nodes.json")
        if os.path.exists(p):
            nodes.extend(json.load(open(p)))
    return nodes


class SegIndex:
    """Grid index over line segments, for nearest-line queries."""

    def __init__(self, lines=(), cell=60.0):
        self.lines = []
        self.cell = cell
        self.grid = defaultdict(list)
        for ln in lines:
            self.add(ln)

    def add(self, ln):
        li = len(self.lines)
        self.lines.append(ln)
        c = self.cell
        for si in range(len(ln) - 1):
            (ax, ay), (bx, by) = ln[si], ln[si + 1]
            x0, x1 = sorted((ax, bx))
            y0, y1 = sorted((ay, by))
            for gx in range(int(x0 // c), int(x1 // c) + 1):
                for gy in range(int(y0 // c), int(y1 // c) + 1):
                    self.grid[(gx, gy)].append(li)
        return li

    def candidates(self, pt, radius):
        c = self.cell
        gx0, gx1 = int((pt[0] - radius) // c), int((pt[0] + radius) // c)
        gy0, gy1 = int((pt[1] - radius) // c), int((pt[1] + radius) // c)
        seen = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                for li in self.grid.get((gx, gy), ()):
                    if li not in seen:
                        seen.add(li)
                        yield li


def dedupe_lines(lines, tol: float = 4.0, cover: float = 0.7, step: float = 8.0):
    """Drop drain pieces that another sheet already draws.

    Each sheet shows a margin of its neighbours, so a pipe on a ward boundary
    arrives twice. Keeping both leaves two parallel copies that never join, and
    the manholes either side snap to different ones — which is exactly what
    stops a conduit forming across the boundary. Longest first, a piece is
    dropped when most of it already lies on a piece we have kept.
    """
    order = sorted(range(len(lines)),
                   key=lambda i: -sum(math.dist(lines[i][k], lines[i][k + 1])
                                      for k in range(len(lines[i]) - 1)))
    kept: list[list] = []
    idx = SegIndex([], cell=max(tol * 4, 20.0))
    dropped = 0
    for i in order:
        ln = lines[i]
        total = sum(math.dist(ln[k], ln[k + 1]) for k in range(len(ln) - 1))
        if total <= 0:
            continue
        # walk the piece and ask how much of it is already covered
        on, off, s = 0, 0, 0.0
        while s <= total:
            pt = slice_line(ln, s, s)[0]
            near = False
            for li in idx.candidates(pt, tol):
                d, _ = project_onto_line(pt, idx.lines[li])
                if d <= tol:
                    near = True
                    break
            on, off = (on + 1, off) if near else (on, off + 1)
            s += step
        if on + off and on / (on + off) >= cover:
            dropped += 1
            continue
        kept.append(ln)
        idx.add(ln)
    return kept, dropped


def point_in(polys, pt):
    p = ogr.Geometry(ogr.wkbPoint)
    p.AddPoint(pt[0], pt[1])
    for label, props, g in polys:
        if g.Contains(p):
            return label, props
    return None, None


def main():
    print("loading ward polygons ...", flush=True)
    ward_polys = load_ward_polygons()

    print("loading + clipping ward drains ...", flush=True)
    CLIP = "--no-clip" not in sys.argv
    raw, kept, dropped = load_ward_drains(ward_polys, clip=CLIP)
    print(f"  clipping to ward polygons: {CLIP}")
    total_raw = sum(sum(math.dist(l[i], l[i + 1]) for i in range(len(l) - 1)) for l in raw)
    print(f"  {kept} pieces kept ({dropped} fell outside their ward), {total_raw/1000:.1f} km")

    print("removing drains drawn twice by adjacent sheets ...", flush=True)
    raw, dup = dedupe_lines(raw)
    dl = sum(sum(math.dist(l[i], l[i + 1]) for i in range(len(l) - 1)) for l in raw)
    print(f"  dropped {dup} duplicate pieces -> {len(raw)} pieces, {dl/1000:.1f} km")

    print("stitching into city-wide runs ...", flush=True)
    runs = merge_lines(raw, tol=8.0, min_cos=-0.5)
    runs = [r for r in runs
            if sum(math.dist(r[i], r[i + 1]) for i in range(len(r) - 1)) >= 2.0]
    total = sum(sum(math.dist(r[i], r[i + 1]) for i in range(len(r) - 1)) for r in runs)
    print(f"  {len(runs)} runs, {total/1000:.1f} km")

    print("validating node attributes ...", flush=True)
    nodes = load_nodes()
    # schedule rows carry the raw OCR strings needed to repair lost decimals
    raws = {}
    for d in sorted(os.listdir(WARDS)):
        m = re.match(r"ward_(\d+)$", d)
        p = os.path.join(WARDS, d, "schedule.json")
        if not m or not os.path.exists(p):
            continue
        for r in json.load(open(p)):
            mid = (r.get("manhole_id") or "").upper().replace(" ", "")
            if mid:
                raws.setdefault((int(m.group(1)), mid), r)
    flag_counts = defaultdict(int)
    for n in nodes:
        key = (n["ward"], (n["node_id"] or "").upper())
        src = raws.get(key, {})
        merged = dict(n)
        for c in ("width", "depth"):
            merged[f"{c}_raw"] = src.get(f"{c}_raw")
        fixed, flags = check_node(merged)
        n["width"], n["depth"] = fixed.get("width"), fixed.get("depth")
        n["invert"], n["drain_top"] = fixed.get("invert"), fixed.get("drain_top")
        n["road_edge"] = fixed.get("road_edge")
        n["flags"] = ";".join(flags)
        for f in flags:
            flag_counts[f] += 1
    print("  " + (", ".join(f"{k}={v}" for k, v in sorted(flag_counts.items())) or "no flags"))

    print("snapping nodes to the unified network ...", flush=True)
    idx = SegIndex(runs)
    placed = {}
    unplaced = 0
    for n in nodes:
        pt = (n["east"], n["north"])
        best = (None, SNAP_TOL, 0.0)
        for li in idx.candidates(pt, SNAP_TOL):
            d, s = project_onto_line(pt, runs[li])
            if d < best[1]:
                best = (li, d, s)
        if best[0] is None:
            unplaced += 1
            n["snap_m"] = None
            continue
        n["east"], n["north"] = slice_line(runs[best[0]], best[2], best[2])[0]
        n["snap_m"] = round(best[1], 2)
        placed[f"W{n['ward']}_{n['node_id']}"] = (best[0], best[2], n)
    print(f"  {len(placed)} snapped, {unplaced} unplaced")

    print("cutting conduits (crossing wards where the drain does) ...", flush=True)
    on_run = defaultdict(list)
    for uid, (li, s, n) in placed.items():
        on_run[li].append((s, uid, n))
    conduits = []
    cross = 0
    cflags = defaultdict(int)
    for li, members in on_run.items():
        members.sort(key=lambda t: t[0])
        run = runs[li]
        for (s0, ua, a), (s1, ub, b) in zip(members, members[1:]):
            length = s1 - s0
            if length < MIN_LEN or length > MAX_SPAN:
                continue
            geom = slice_line(run, s0, s1)
            ia, ib = a.get("invert"), b.get("invert")
            us, ds, uid_us, uid_ds, src = a, b, ua, ub, "assumed"
            if ia is not None and ib is not None and abs(ia - ib) > 1e-6:
                if ib > ia:
                    us, ds, uid_us, uid_ds = b, a, ub, ua
                    geom = geom[::-1]
                src = "invert"
            ui, di = us.get("invert"), ds.get("invert")
            slope = round((ui - di) / length, 6) if (ui is not None and di is not None) else None
            flags = check_conduit(us, ds, length, slope)
            for f in flags:
                cflags[f] += 1
            if us["ward"] != ds["ward"]:
                cross += 1
            conduits.append({
                "cond_id": f"{uid_us}->{uid_ds}", "from_uid": uid_us, "to_uid": uid_ds,
                "from_ward": us["ward"], "to_ward": ds["ward"],
                "length_m": round(length, 3), "us_invert": ui, "ds_invert": di,
                "width_m": us.get("width") if us.get("width") is not None else ds.get("width"),
                "depth_m": us.get("depth") if us.get("depth") is not None else ds.get("depth"),
                "type": us.get("drain_type") or ds.get("drain_type"),
                "slope": slope, "dir_src": src, "cross_ward": int(us["ward"] != ds["ward"]),
                "flags": ";".join(flags), "geometry": geom,
            })
    print(f"  {len(conduits)} conduits, {cross} of them crossing a ward boundary")
    print("  " + (", ".join(f"{k}={v}" for k, v in sorted(cflags.items())) or "no flags"))

    print("tagging catchments ...", flush=True)
    micro = load_polygon_layer(MICROWS, ["microwater", "subwatersh", "watershed"])
    subb = load_polygon_layer(SUBBASINS, ["subbasin", "basin"])
    print(f"  {len(micro)} microwatersheds, {len(subb)} sub-basins")
    for n in nodes:
        mlabel, _ = point_in(micro, (n["east"], n["north"]))
        slabel, _ = point_in(subb, (n["east"], n["north"]))
        n["microws"] = mlabel or ""
        n["subbasin"] = slabel or ""
    tagged = sum(1 for n in nodes if n["microws"])
    print(f"  {tagged}/{len(nodes)} nodes inside a microwatershed")

    print("building subcatchments ...", flush=True)
    city = None
    for g in ward_polys.values():
        gg = g.Buffer(0)
        city = gg if city is None else city.Union(gg)
    snapped = [n for n in nodes if n.get("snap_m") is not None]
    cells = voronoi_cells([(n["east"], n["north"]) for n in snapped], city)
    subs = []
    for n, cell in zip(snapped, cells):
        if cell is None:
            continue
        area = cell.GetArea()
        if area < 1.0:
            continue
        rec = {"uid": f"W{n['ward']}_{n['node_id']}", "ward": n["ward"],
               "microws": n.get("microws", ""), "subbasin": n.get("subbasin", "")}
        rec.update(swmm_subcatchment_params(area))
        rec["wkt"] = cell.ExportToWkt()
        subs.append(rec)
    tot = sum(s["area_ha"] for s in subs)
    print(f"  {len(subs)} subcatchments, {tot:.0f} ha total "
          f"(median {sorted(s['area_ha'] for s in subs)[len(subs)//2]:.2f} ha)")
    with open(f"{OUT}/network/subcatchments.json", "w") as f:
        json.dump(subs, f)

    os.makedirs(f"{OUT}/network", exist_ok=True)
    with open(f"{OUT}/network/nodes.json", "w") as f:
        json.dump(nodes, f)
    with open(f"{OUT}/network/conduits.json", "w") as f:
        json.dump(conduits, f)
    with open(f"{OUT}/network/runs.json", "w") as f:
        json.dump(runs, f)
    print(f"\nwritten -> {OUT}/network/")


if __name__ == "__main__":
    main()
