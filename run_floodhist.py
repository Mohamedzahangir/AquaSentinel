#!/usr/bin/env python3
"""
Tag each manhole with whether its street flooded historically.

maps-site carries an OSM-derived record of Chennai roads flagged as flooded.
That is not a water-depth surface, so it cannot calibrate the model directly,
but it is an independent observation of where water actually stood — which
makes it the natural check on whichever nodes the model later predicts as
surcharging.
"""
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from osgeo import ogr, osr  # noqa: E402

ogr.UseExceptions()

ROOT = "/Users/admin/Desktop/projects/swd"
NET = f"{ROOT}/derived/network"
FLOOD = "/Users/admin/Desktop/projects/maps-site/old/data/chennai_flood.geojson"
RADIUS = 40.0     # m: a manhole this close to a flooded street shares its fate


def main():
    if not os.path.exists(FLOOD):
        print("no flood layer found:", FLOOD)
        return
    w = osr.SpatialReference(); w.ImportFromEPSG(4326)
    w.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    u = osr.SpatialReference(); u.ImportFromEPSG(32644)
    u.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    ct = osr.CoordinateTransformation(w, u)

    print("loading flooded roads ...", flush=True)
    data = json.load(open(FLOOD))
    cell = 100.0
    grid = defaultdict(list)
    segs = []
    for feat in data["features"]:
        if str(feat["properties"].get("is_flooded")) not in ("1", "True", "true"):
            continue
        g = ogr.CreateGeometryFromJson(json.dumps(feat["geometry"]))
        if g is None:
            continue
        g.Transform(ct)
        parts = [g.GetGeometryRef(i) for i in range(g.GetGeometryCount())] \
            if g.GetGeometryName().startswith("MULTI") else [g]
        for part in parts:
            pts = [(part.GetX(i), part.GetY(i)) for i in range(part.GetPointCount())]
            for i in range(len(pts) - 1):
                si = len(segs)
                segs.append((pts[i], pts[i + 1], feat["properties"].get("type", "")))
                (ax, ay), (bx, by) = pts[i], pts[i + 1]
                for gx in range(int(min(ax, bx) // cell), int(max(ax, bx) // cell) + 1):
                    for gy in range(int(min(ay, by) // cell), int(max(ay, by) // cell) + 1):
                        grid[(gx, gy)].append(si)
    print(f"  {len(segs)} flooded road segments")

    def near(pt):
        best = RADIUS
        kind = ""
        gx0, gx1 = int((pt[0] - RADIUS) // cell), int((pt[0] + RADIUS) // cell)
        gy0, gy1 = int((pt[1] - RADIUS) // cell), int((pt[1] + RADIUS) // cell)
        seen = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                for si in grid.get((gx, gy), ()):
                    if si in seen:
                        continue
                    seen.add(si)
                    (ax, ay), (bx, by), t = segs[si]
                    dx, dy = bx - ax, by - ay
                    L2 = dx * dx + dy * dy
                    s = 0.0 if L2 <= 1e-12 else max(0.0, min(1.0, ((pt[0]-ax)*dx + (pt[1]-ay)*dy) / L2))
                    d = math.hypot(pt[0] - (ax + s*dx), pt[1] - (ay + s*dy))
                    if d < best:
                        best, kind = d, t
        return (round(best, 1), kind) if best < RADIUS else (None, "")

    nodes = json.load(open(f"{NET}/nodes.json"))
    hit = 0
    for n in nodes:
        d, kind = near((n["east"], n["north"]))
        n["flood_m"] = d
        n["flood_road"] = kind
        if d is not None:
            hit += 1
    print(f"  {hit}/{len(nodes)} manholes within {RADIUS:.0f} m of a historically flooded street "
          f"({100*hit/len(nodes):.1f}%)")

    # a low invert on a flooded street is the most exposed combination
    withinv = [n for n in nodes if n.get("invert") is not None]
    if withinv:
        inv = sorted(n["invert"] for n in withinv)
        low = inv[int(0.25 * len(inv))]
        risky = [n for n in withinv if n.get("flood_m") is not None and n["invert"] <= low]
        print(f"  {len(risky)} of those also sit in the lowest quartile of invert "
              f"(<= {low:.2f} m MSL)")
    with open(f"{NET}/nodes.json", "w") as f:
        json.dump(nodes, f)
    print("updated nodes.json")


if __name__ == "__main__":
    main()
