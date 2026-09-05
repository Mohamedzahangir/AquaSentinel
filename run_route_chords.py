#!/usr/bin/env python3
"""
Route the schedule-link chords along the OSM street network.

`run_connect.py` joins manholes consecutive in the schedule with a straight
2-point chord. A quarter of those cut across blocks. This pass snaps both ends
of every chord to the OSM road graph, shortest-paths between them, and

  - replaces the chord with the road route when the route is no longer than
    ROUTE_MAX_RATIO x the chord  -> flag `road_routed`
  - keeps short chords as they are (two manholes a few metres apart on one
    street do not need routing)            -> unchanged
  - DROPS the link when the road route is much longer than the chord, or an
    end is nowhere near a road. Those two manholes are probably not connected
    along a street, and a made-up connection is worse than a gap.

Rewrites derived/network/conduits.json in place (backup kept alongside).
Input roads: derived/osm/roads.json, an Overpass `out geom` dump of
way[highway] over the network bbox.
"""
import json, math, os, shutil, sys, time
import networkx as nx
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

NET = "derived/network"
OSM = "derived/osm/roads.json"
ROUTE_MAX_RATIO = float(os.environ.get("ROUTE_MAX_RATIO", "1.5"))    # route / chord above this -> reject
SNAP_MAX_M = 40.0        # manhole further than this from any road node -> reject
SHORT_CHORD_M = 25.0     # chords this short are left straight

t0 = time.time()
tf = Transformer.from_crs("EPSG:4326", "EPSG:32644", always_xy=True)
osm = json.load(open(OSM))
G = nx.Graph()
xy = {}
for w in osm["elements"]:
    if w.get("type") != "way" or "geometry" not in w:
        continue
    ids = w["nodes"]
    for nid, g in zip(ids, w["geometry"]):
        if nid not in xy:
            xy[nid] = tf.transform(g["lon"], g["lat"])
    for a, b in zip(ids, ids[1:]):
        (xa, ya), (xb, yb) = xy[a], xy[b]
        G.add_edge(a, b, w=math.hypot(xb - xa, yb - ya))
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree
edges = list(G.edges())
edge_geoms = [LineString([xy[a], xy[b]]) for a, b in edges]
etree = STRtree(edge_geoms)
print(f"road graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges "
      f"({time.time()-t0:.0f}s)")


def snap(P, tag):
    """Project a manhole onto the nearest road edge and splice a temporary
    node there, so the route starts where the manhole actually meets the
    street instead of at whichever OSM vertex happens to be closest."""
    i = int(etree.nearest(Point(P)))
    ln = edge_geoms[i]
    d = ln.distance(Point(P))
    if d > SNAP_MAX_M:
        return None
    a, b = edges[i]
    q = ln.interpolate(ln.project(Point(P)))
    qxy = (q.x, q.y)
    G.add_edge(tag, a, w=math.hypot(qxy[0]-xy[a][0], qxy[1]-xy[a][1]))
    G.add_edge(tag, b, w=math.hypot(qxy[0]-xy[b][0], qxy[1]-xy[b][1]))
    xy[tag] = qxy
    return tag


nodes = {n["uid"]: n for n in json.load(open(f"{NET}/nodes.json"))}
path = f"{NET}/conduits.json"
bak = f"{NET}/conduits.pre_route.json"
if not os.path.exists(bak):
    shutil.copy(path, bak)
conduits = json.load(open(bak))      # always start from the un-routed build

stats = dict(chords=0, short=0, routed=0, too_long=0, no_road=0, unreachable=0)
kept = []
ratios = []
for c in conduits:
    flags = c.get("flags") or ""
    if "schedule_link" not in flags:
        kept.append(c); continue
    stats["chords"] += 1
    a, b = nodes[c["from_uid"]], nodes[c["to_uid"]]
    A = (a["east"], a["north"]); B = (b["east"], b["north"])
    chord = math.hypot(B[0] - A[0], B[1] - A[1])
    if chord < SHORT_CHORD_M:
        stats["short"] += 1; kept.append(c); continue
    sA = snap(A, "_A"); sB = snap(B, "_B")
    if sA is None or sB is None:
        stats["no_road"] += 1
        G.remove_nodes_from([n for n in ("_A", "_B") if n in G]); continue
    try:
        route = nx.shortest_path(G, sA, sB, weight="w")
    except nx.NetworkXNoPath:
        stats["unreachable"] += 1
        G.remove_nodes_from(["_A", "_B"]); continue
    pts = [list(A)] + [list(xy[n]) for n in route] + [list(B)]
    G.remove_nodes_from(["_A", "_B"])
    # drop consecutive near-duplicates (manhole sitting on the snapped node)
    geom = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0]-geom[-1][0], p[1]-geom[-1][1]) > 0.5:
            geom.append(p)
    L = sum(math.hypot(geom[i+1][0]-geom[i][0], geom[i+1][1]-geom[i][1]) for i in range(len(geom)-1))
    ratio = L / chord
    ratios.append(ratio)
    if ratio > ROUTE_MAX_RATIO:
        stats["too_long"] += 1; continue
    c["geometry"] = geom
    c["length_m"] = round(L, 3)
    if c.get("us_invert") is not None and c.get("ds_invert") is not None and L > 0:
        c["slope"] = round((c["us_invert"] - c["ds_invert"]) / L, 6)
    c["flags"] = flags + ";road_routed"
    stats["routed"] += 1
    kept.append(c)

json.dump(kept, open(path, "w"))
dropped = stats["too_long"] + stats["no_road"] + stats["unreachable"]
print(f"{stats['chords']} schedule chords: {stats['routed']} routed along roads, "
      f"{stats['short']} left straight (<{SHORT_CHORD_M:.0f} m), {dropped} dropped "
      f"({stats['too_long']} route >{ROUTE_MAX_RATIO}x chord, {stats['no_road']} no road within "
      f"{SNAP_MAX_M:.0f} m, {stats['unreachable']} no path)")
if ratios:
    r = np.array(ratios)
    print(f"route/chord ratio: median {np.median(r):.2f}, p90 {np.percentile(r,90):.2f}")
print(f"conduits {len(conduits)} -> {len(kept)}  ({time.time()-t0:.0f}s)")
