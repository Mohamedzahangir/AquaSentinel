#!/usr/bin/env python3
"""
Ward outlines for the viewer and the maps.

The 2021 sheets follow the PRE-2022 GCC ward delimitation. Checked against the
ward boundaries traced off the sheets themselves (map_context role
`ward_boundary`, 105 wards): the pre-2022 file matches at a median 6 m (p80
38 m); the current 2022 delimitation — TNGIS, and the copies in maps-site and
civic-sentinel — is >150 m off in 33 wards. So this uses
OpenDataChennai/geojson/Chennai_Wards_old.geojson.

Every ward file extends into the Bay of Bengal, so the polygons are clipped to
land with the OSM coastline (derived/osm/coastline.json, Overpass
natural=coastline). Simplified to ~5 m: they are drawn as thin dotted lines.
"""
import json, os
from shapely.geometry import shape, mapping, box, LineString, Point
from shapely.ops import linemerge, unary_union, polygonize

SRC = os.environ.get("WARDS_SRC", "/home/parag/projects/OpenDataChennai/geojson/Chennai_Wards_old.geojson")
COAST = "derived/osm/coastline.json"
OUT = "web/data/wards.json"

wards = json.load(open(SRC))["features"]
coast = json.load(open(COAST))
lines = [LineString([(g["lon"], g["lat"]) for g in w["geometry"]]) for w in coast["elements"] if w.get("type") == "way"]
merged = linemerge(unary_union(lines))
bb = box(80.05, 12.80, 80.45, 13.35)
# the coastline stops short of the bbox; run each free end out to the bbox edge so it splits it
parts = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
ext = []
for ln in parts:
    c = list(ln.coords)
    for end in (0, -1):
        q = bb.boundary.interpolate(bb.boundary.project(Point(c[end])))
        c = ([tuple(q.coords[0])] + c) if end == 0 else (c + [tuple(q.coords[0])])
    ext.append(LineString(c))
pieces = list(polygonize(unary_union([bb.boundary] + ext)))
# land = every piece that holds a ward centroid well inland (the sea piece holds none)
inland = [shape(f["geometry"]).representative_point() for f in wards if f.get("geometry")]
land = unary_union([p for p in pieces if sum(p.contains(q) for q in inland) >= 3])
print(f"coastline: {len(lines)} ways -> {merged.geom_type}; {len(pieces)} pieces, land area {land.area*111*111:.0f} km²")

nodes = json.load(open("web/data/network.json"))["nodes"]
covered = {n["w"] for n in nodes if n.get("w") is not None}
feats = []
for f in wards:
    if not f.get("geometry"): continue
    p = f["properties"]; ward = int(str(p.get("WARD") or p.get("ward") or p.get("name")).strip().lstrip("0") or 0)
    g = shape(f["geometry"]).intersection(land)
    if g.is_empty: continue
    g = g.simplify(0.00005, preserve_topology=True)
    feats.append({"type": "Feature", "properties": {"ward": ward, "zone": p.get("ZONE") or p.get("zone"), "have": 1 if ward in covered else 0},
                  "geometry": mapping(g)})
json.dump({"type": "FeatureCollection", "features": feats,
           "source": "GCC ward boundaries, pre-2022 delimitation (the one the 2021 SWD sheets use), via OpenDataChennai; clipped to land with the OSM coastline"},
          open(OUT, "w"), separators=(",", ":"))
xs = [c[0] for f in feats for ring in (f["geometry"]["coordinates"] if f["geometry"]["type"] == "Polygon" else [r for pg in f["geometry"]["coordinates"] for r in pg]) for c in ring]
print(f"{len(feats)} wards ({sum(f['properties']['have'] for f in feats)} with a sheet) -> {OUT} ({os.path.getsize(OUT)/1e6:.2f} MB); max lon {max(xs):.4f}")
