#!/usr/bin/env python3
"""
Pack the network for the 3D viewer.

Everything is converted to WGS84 (what MapLibre wants) and written as flat
JSON / typed arrays rather than GeoJSON — a browser can upload those straight
to the GPU, and it keeps the payload small enough to load without tiling.

Outputs (web/data/):
  network.json   nodes + conduits, as before, plus per-conduit invert
                 breakpoints (`bp`) at intermediate manholes the run passes
  ground.json    a Delaunay TIN over the surveyed road_edge levels
  wards.json     untouched — regenerated only when the TNGIS source is present
  rainfall.json  daily totals + the hourly SWMM hyetograph
"""
import json
import math
import os
import sys

import numpy as np
from pyproj import Transformer
from scipy.spatial import Delaunay, cKDTree

ROOT = os.path.dirname(os.path.abspath(__file__))
NET = f"{ROOT}/derived/network"
WEB = f"{ROOT}/web/data"
WARDS_SRC = os.environ.get("WARDS_SRC", "/Users/admin/Desktop/projects/tngis-data/data/generic_viewer/gcc_ward_boundary.geojson")

TF = Transformer.from_crs("EPSG:32644", "EPSG:4326", always_xy=True)
BP_SNAP_M = 3.0          # a manhole this close to a run's centreline is on it
TIN_MAX_EDGE_M = 450.0   # drop TIN triangles longer than this: they bridge gaps, not ground


def write_wards(nodes):
    if not os.path.exists(WARDS_SRC):
        print(f"ward boundaries not found at {WARDS_SRC}; keeping existing wards.json")
        return
    from osgeo import ogr
    def wardno(v):
        try: return int(str(v).strip())
        except (TypeError, ValueError): return None
    covered = {wardno(n["w"]) for n in nodes if n.get("w") is not None}
    src = ogr.Open(WARDS_SRC); lyr = src.GetLayer(0); feats = []
    for f in lyr:
        g = f.GetGeometryRef()
        if g is None: continue
        g = g.SimplifyPreserveTopology(0.00005)
        ward = wardno(f.GetField("ward"))
        feats.append({"type": "Feature", "properties": {"ward": ward, "zone": f.GetField("zone"),
                      "have": 1 if ward in covered else 0}, "geometry": json.loads(g.ExportToJson())})
    with open(f"{WEB}/wards.json", "w") as fh:
        json.dump({"type": "FeatureCollection", "features": feats}, fh, separators=(",", ":"))


SYMBOL_R = 6.0   # m: the sheets draw each manhole as a ~1 mm circle (5 m at 1:5000)


def collapse_symbols(geom, tree, node_xy):
    """The tracer followed the manhole circle symbol into the centreline, so
    ~10% of runs end (or pass through a junction) with ~40 vertices looping
    round the manhole. Snap every vertex within SYMBOL_R of a manhole onto
    that manhole and drop the duplicates that result."""
    out = []
    for x, y in geom:
        d, j = tree.query((x, y))
        p = list(node_xy[j]) if d <= SYMBOL_R else [x, y]
        if out and math.hypot(p[0]-out[-1][0], p[1]-out[-1][1]) < 0.3:
            continue
        out.append(p)
    return out if len(out) >= 2 else geom


def breakpoints(geom, from_uid, to_uid, tree, node_xy, node_uid, node_inv):
    """Surveyed inverts of manholes the run passes through, as (t, invert)
    with t the fraction of run length — so the viewer can step the profile
    at each chamber instead of one straight interpolation end to end."""
    if len(geom) < 3:
        return []
    P = np.asarray(geom)
    seg = np.diff(P, axis=0)
    L = np.hypot(seg[:, 0], seg[:, 1])
    total = L.sum()
    if total <= 0:
        return []
    cum = np.concatenate([[0], np.cumsum(L)])
    # candidate manholes near any vertex (runs are densely vertexed: ~18 pts)
    cand = set()
    for i in range(1, len(P) - 1):
        for j in tree.query_ball_point(P[i], BP_SNAP_M * 4):
            cand.add(j)
    out = []
    for j in cand:
        uid = node_uid[j]
        if uid in (from_uid, to_uid) or node_inv[j] is None:
            continue
        q = node_xy[j]
        best_d, best_t = 1e9, None
        for i in range(len(seg)):
            if L[i] == 0: continue
            u = np.dot(q - P[i], seg[i]) / (L[i] * L[i])
            u = min(1.0, max(0.0, u))
            d = np.hypot(*(P[i] + u * seg[i] - q))
            if d < best_d:
                best_d, best_t = d, (cum[i] + u * L[i]) / total
        if best_d <= BP_SNAP_M and 0.02 < best_t < 0.98:
            out.append((round(float(best_t), 4), node_inv[j]))
    out.sort()
    return [[t, z] for t, z in out]


def write_ground(nodes_xy_z):
    """Delaunay TIN over every manhole with a surveyed road_edge."""
    pts = np.array([[x, y] for x, y, _ in nodes_xy_z])
    z = np.array([v for _, _, v in nodes_xy_z])
    tri = Delaunay(pts)
    keep = []
    for a, b, c in tri.simplices:
        e = max(np.hypot(*(pts[a]-pts[b])), np.hypot(*(pts[b]-pts[c])), np.hypot(*(pts[c]-pts[a])))
        if e <= TIN_MAX_EDGE_M:
            keep.append([int(a), int(b), int(c)])
    verts = []
    for (x, y), zz in zip(pts, z):
        lon, lat = TF.transform(x, y)
        verts.append([round(lon, 7), round(lat, 7), round(float(zz), 3)])
    with open(f"{WEB}/ground.json", "w") as f:
        json.dump({"n": len(verts), "v": verts, "t": keep,
                   "source": "road_edge level at every surveyed manhole, Delaunay, "
                             f"triangles with an edge over {TIN_MAX_EDGE_M:.0f} m removed"},
                  f, separators=(",", ":"))
    print(f"ground TIN: {len(verts)} vertices, {len(keep)} of {len(tri.simplices)} triangles "
          f"-> ground.json ({os.path.getsize(f'{WEB}/ground.json')/1e6:.2f} MB)")


def main():
    nodes = json.load(open(f"{NET}/nodes.json"))
    conduits = json.load(open(os.environ.get("CONDUITS", f"{NET}/conduits.json")))

    by_uid, out_nodes = {}, []
    node_xy, node_uid, node_inv, ground_pts = [], [], [], []
    for n in nodes:
        uid = f"W{n['ward']}_{n['node_id']}"
        lon, lat = TF.transform(n["east"], n["north"])
        rec = {
            "id": uid, "w": n.get("ward"),
            "lon": round(lon, 7), "lat": round(lat, 7),
            "inv": n.get("invert"), "top": n.get("drain_top"), "road": n.get("road_edge"),
            "wd": n.get("width"), "dp": n.get("depth"),
            "t": n.get("drain_type"), "out": 1 if n.get("is_outlet") else 0,
            "sy": 1 if "synthetic_outfall" in (n.get("flags") or "") else 0,
            "fl": n.get("flood_m"),
            "fh": n.get("swmm_flood_hr"), "fv": n.get("swmm_flood_Ml"),
            "ag": n.get("agree") or "",
        }
        by_uid[uid] = rec; out_nodes.append(rec)
        node_xy.append((n["east"], n["north"])); node_uid.append(uid); node_inv.append(n.get("invert"))
        if n.get("road_edge") is not None:
            ground_pts.append((n["east"], n["north"], n["road_edge"]))
    node_xy = np.array(node_xy)
    tree = cKDTree(node_xy)

    out_cond, n_bp = [], 0
    for c in conduits:
        a, b = by_uid.get(c["from_uid"]), by_uid.get(c["to_uid"])
        if not a or not b:
            continue
        geom = collapse_symbols(c.get("geometry", []), tree, node_xy)
        pts = [[round(v, 7) for v in TF.transform(x, y)] for x, y in geom]
        if len(pts) < 2:
            continue
        bp = breakpoints(geom, c["from_uid"], c["to_uid"], tree, node_xy, node_uid, node_inv)
        n_bp += len(bp)
        rec = {
            "f": c["from_uid"], "t": c["to_uid"],
            "ui": c.get("us_invert"), "di": c.get("ds_invert"),
            "wd": c.get("width_m"), "dp": c.get("depth_m"),
            "ty": c.get("type"), "s": c.get("slope"),
            "L": c.get("length_m"), "x": c.get("cross_ward", 0),
            "src": c.get("dir_src"), "fg": c.get("flags") or "",
            "g": pts,
        }
        if bp: rec["bp"] = bp
        out_cond.append(rec)

    lons = [n["lon"] for n in out_nodes]; lats = [n["lat"] for n in out_nodes]
    meta = {
        "n_nodes": len(out_nodes), "n_conduits": len(out_cond),
        "centre": [sum(lons)/len(lons), sum(lats)/len(lats)],
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
        "crs": "EPSG:4326, elevations m MSL",
        "n_breakpoints": n_bp,
    }
    os.makedirs(WEB, exist_ok=True)
    with open(f"{WEB}/{os.environ.get('NET_OUT', 'network.json')}", "w") as f:
        json.dump({"meta": meta, "nodes": out_nodes, "conduits": out_cond}, f, separators=(",", ":"))
    print(f"{len(out_nodes)} nodes, {len(out_cond)} conduits, {n_bp} intermediate invert breakpoints "
          f"-> network.json ({os.path.getsize(f'{WEB}/network.json')/1e6:.1f} MB)")

    write_ground(ground_pts)
    write_wards(out_nodes)

    rp = f"{ROOT}/derived/rainfall/swmm_series.json"
    if os.path.exists(rp):
        r = json.load(open(rp))
        hourly = []
        for row in r.get("rows", []):
            _, d, t, v = row.split()
            hourly.append([f"{d} {t}", float(v)])
        with open(f"{WEB}/rainfall.json", "w") as f:
            json.dump({"event": r.get("event"), "daily_mm": r.get("daily_mm", {}),
                       "hourly_mm": hourly, "start": r.get("start"), "end": r.get("end"),
                       "source": r.get("source")}, f)
        print(f"rainfall: {len(hourly)} hourly values written")


if __name__ == "__main__":
    main()
