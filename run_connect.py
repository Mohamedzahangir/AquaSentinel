#!/usr/bin/env python3
"""
Make the network drain somewhere.

Cutting each drawn run between its own manholes produces isolated chains: at a
T-junction the stitcher can only carry one branch through, so the third arrives
as a separate run and no conduit ever links it. The result routes water into
dead ends — 4% of junctions could reach an outfall.

Two passes fix it:

1. **Junction links.** Where two manholes on *different* runs sit close together
   and their runs actually touch, the drains meet there in reality, so a conduit
   is added.
2. **Terminal outfalls.** A component with no outfall still has to discharge
   somewhere. Its lowest-invert node is promoted to an outfall and marked
   `synthetic_outfall`, so the assumption is visible rather than buried.
"""
import json
import math
import re
import os
import sys
from collections import defaultdict, deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from swdlib.network import project_onto_line  # noqa: E402

ROOT = "/Users/admin/Desktop/projects/swd"
NET = f"{ROOT}/derived/network"

NODE_LINK_TOL = 22.0     # m between two manholes that share a junction
RUN_TOUCH_TOL = 8.0      # m the two runs must approach for the link to be real


def components(nodes_by_uid, conduits):
    adj = defaultdict(set)
    for c in conduits:
        adj[c["from_uid"]].add(c["to_uid"])
        adj[c["to_uid"]].add(c["from_uid"])
    seen, comps = set(), []
    for u in nodes_by_uid:
        if u in seen:
            continue
        comp, q = [], deque([u])
        seen.add(u)
        while q:
            v = q.popleft()
            comp.append(v)
            for w in adj[v]:
                if w not in seen:
                    seen.add(w)
                    q.append(w)
        comps.append(comp)
    return comps


def reachable_from_outfalls(nodes_by_uid, conduits):
    rev = defaultdict(list)
    for c in conduits:
        rev[c["to_uid"]].append(c["from_uid"])
    outs = [u for u, n in nodes_by_uid.items() if n.get("is_outlet")]
    seen, q = set(outs), deque(outs)
    while q:
        u = q.popleft()
        for v in rev.get(u, ()):
            if v not in seen:
                seen.add(v)
                q.append(v)
    return seen


def main():
    nodes = json.load(open(f"{NET}/nodes.json"))
    conduits = json.load(open(f"{NET}/conduits.json"))
    runs = json.load(open(f"{NET}/runs.json"))
    by_uid = {f"W{n['ward']}_{n['node_id']}": n for n in nodes}
    for u, n in by_uid.items():
        n["uid"] = u
    placed = {u: n for u, n in by_uid.items() if n.get("snap_m") is not None}

    have = {(c["from_uid"], c["to_uid"]) for c in conduits}
    have |= {(b, a) for a, b in have}

    # which run each node sits on
    cell = 60.0
    grid = defaultdict(list)
    for u, n in placed.items():
        grid[(int(n["east"] // cell), int(n["north"] // cell))].append(u)

    run_of = {}
    ridx = defaultdict(list)
    for ri, r in enumerate(runs):
        for (x, y) in r:
            ridx[(int(x // cell), int(y // cell))].append(ri)
    for u, n in placed.items():
        best, bd = None, 1e9
        gx, gy = int(n["east"] // cell), int(n["north"] // cell)
        cands = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                cands.update(ridx.get((gx + dx, gy + dy), ()))
        for ri in cands:
            d, _ = project_onto_line((n["east"], n["north"]), runs[ri])
            if d < bd:
                best, bd = ri, d
        run_of[u] = best

    def runs_touch(ra, rb, near):
        if ra is None or rb is None:
            return False
        for (x, y) in runs[ra]:
            if abs(x - near[0]) > 60 or abs(y - near[1]) > 60:
                continue
            d, _ = project_onto_line((x, y), runs[rb])
            if d <= RUN_TOUCH_TOL:
                return True
        return False

    print("adding junction links ...", flush=True)
    added = 0
    uids = list(placed)
    for u in uids:
        n = by_uid[u]
        gx, gy = int(n["east"] // cell), int(n["north"] // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for v in grid.get((gx + dx, gy + dy), ()):
                    if v <= u:
                        continue
                    m = by_uid[v]
                    d = math.dist((n["east"], n["north"]), (m["east"], m["north"]))
                    if d > NODE_LINK_TOL or d < 0.5:
                        continue
                    if (u, v) in have:
                        continue
                    if run_of.get(u) == run_of.get(v):
                        continue
                    if not runs_touch(run_of.get(u), run_of.get(v), (n["east"], n["north"])):
                        continue
                    ia, ib = n.get("invert"), m.get("invert")
                    us, ds, uu, ud = n, m, u, v
                    if ia is not None and ib is not None and ib > ia:
                        us, ds, uu, ud = m, n, v, u
                    ui, di = us.get("invert"), ds.get("invert")
                    slope = round((ui - di) / d, 6) if (ui is not None and di is not None) else None
                    conduits.append({
                        "cond_id": f"{uu}->{ud}", "from_uid": uu, "to_uid": ud,
                        "from_ward": us["ward"], "to_ward": ds["ward"],
                        "length_m": round(d, 3), "us_invert": ui, "ds_invert": di,
                        "width_m": us.get("width") if us.get("width") is not None else ds.get("width"),
                        "depth_m": us.get("depth") if us.get("depth") is not None else ds.get("depth"),
                        "type": us.get("drain_type") or ds.get("drain_type"),
                        "slope": slope,
                        "dir_src": "invert" if slope is not None else "assumed",
                        "cross_ward": int(us["ward"] != ds["ward"]),
                        "flags": "junction_link",
                        "geometry": [(n["east"], n["north"]), (m["east"], m["north"])]
                        if (uu == u) else [(m["east"], m["north"]), (n["east"], n["north"])],
                    })
                    have.add((u, v)); have.add((v, u))
                    added += 1
    print(f"  {added} junction links added -> {len(conduits)} conduits")

    reach = reachable_from_outfalls(by_uid, conduits)
    print(f"  reaching an outfall: {len(reach)}/{len(by_uid)} "
          f"({100*len(reach)/len(by_uid):.0f}%)")

    # The schedule numbers manholes sequentially along a drain run, so A2 and A3
    # are consecutive structures on one pipe whether or not the drawing could be
    # traced between them. Those links are real hydraulics; only their shape is
    # unknown, so they are added last, after every drawn connection has been
    # used, and flagged so the straight geometry is never mistaken for surveyed.
    print("adding schedule-sequence links ...", flush=True)
    series = defaultdict(list)
    for u, n in by_uid.items():
        m = re.match(r"^([A-Z]+)(\d+)$", n["node_id"] or "")
        if m:
            series[(n["ward"], m.group(1))].append((int(m.group(2)), u))
    seq_added = 0
    for key, members in series.items():
        members.sort()
        for (na, ua), (nb, ub) in zip(members, members[1:]):
            if nb != na + 1 or (ua, ub) in have:
                continue
            a, b = by_uid[ua], by_uid[ub]
            d = math.dist((a["east"], a["north"]), (b["east"], b["north"]))
            if not (1.0 <= d <= 400.0):
                continue
            ia, ib = a.get("invert"), b.get("invert")
            us, ds, uu, ud = a, b, ua, ub
            if ia is not None and ib is not None and ib > ia:
                us, ds, uu, ud = b, a, ub, ua
            ui, di = us.get("invert"), ds.get("invert")
            slope = round((ui - di) / d, 6) if (ui is not None and di is not None) else None
            conduits.append({
                "cond_id": f"{uu}->{ud}", "from_uid": uu, "to_uid": ud,
                "from_ward": us["ward"], "to_ward": ds["ward"],
                "length_m": round(d, 3), "us_invert": ui, "ds_invert": di,
                "width_m": us.get("width") if us.get("width") is not None else ds.get("width"),
                "depth_m": us.get("depth") if us.get("depth") is not None else ds.get("depth"),
                "type": us.get("drain_type") or ds.get("drain_type"),
                "slope": slope,
                "dir_src": "invert" if slope is not None else "assumed",
                "cross_ward": int(us["ward"] != ds["ward"]),
                "flags": "schedule_link;geometry_inferred",
                "geometry": [(us["east"], us["north"]), (ds["east"], ds["north"])],
            })
            have.add((ua, ub)); have.add((ub, ua))
            seq_added += 1
    print(f"  {seq_added} schedule links added -> {len(conduits)} conduits")
    reach = reachable_from_outfalls(by_uid, conduits)
    print(f"  reaching an outfall: {len(reach)}/{len(by_uid)} "
          f"({100*len(reach)/len(by_uid):.0f}%)")

    print("promoting terminal nodes to outfalls ...", flush=True)
    comps = components(by_uid, conduits)
    promoted = 0
    for comp in comps:
        if len(comp) < 2:
            continue
        if any(by_uid[u].get("is_outlet") for u in comp):
            continue
        withinv = [u for u in comp if by_uid[u].get("invert") is not None]
        if not withinv:
            continue
        low = min(withinv, key=lambda u: by_uid[u]["invert"])
        by_uid[low]["is_outlet"] = True
        f = by_uid[low].get("flags") or ""
        by_uid[low]["flags"] = (f + ";" if f else "") + "synthetic_outfall"
        promoted += 1
    print(f"  {promoted} components given a terminal outfall")

    # Direction so far is per-conduit, from whichever end has the higher invert.
    # Surveyed levels carry enough noise (and genuine local sumps) that this
    # leaves sinks part-way down a run, and everything upstream of one drains
    # into a dead end. Water in fact runs to the component's outlet, so each
    # component is oriented along its tree rooted at that outlet; conduits whose
    # direction that reverses are flagged rather than quietly overwritten.
    print("orienting each component toward its outlet ...", flush=True)
    adj = defaultdict(list)
    for i, c in enumerate(conduits):
        adj[c["from_uid"]].append((c["to_uid"], i))
        adj[c["to_uid"]].append((c["from_uid"], i))
    flipped = 0
    for comp in components(by_uid, conduits):
        outs = [u for u in comp if by_uid[u].get("is_outlet")]
        if not outs:
            continue
        root = min(outs, key=lambda u: (by_uid[u].get("invert") is None,
                                        by_uid[u].get("invert") or 1e9))
        seen = {root}
        q = deque([root])
        while q:                       # breadth-first out from the outlet
            v = q.popleft()
            for w, ci in adj[v]:
                if w in seen:
                    continue
                seen.add(w)
                q.append(w)
                c = conduits[ci]
                # every edge must point from w (upstream) towards v (downstream)
                if c["from_uid"] != w:
                    c["from_uid"], c["to_uid"] = c["to_uid"], c["from_uid"]
                    c["from_ward"], c["to_ward"] = c["to_ward"], c["from_ward"]
                    c["us_invert"], c["ds_invert"] = c["ds_invert"], c["us_invert"]
                    c["geometry"] = c["geometry"][::-1]
                    c["cond_id"] = f"{c['from_uid']}->{c['to_uid']}"
                    if c.get("dir_src") == "invert":
                        c["flags"] = ((c.get("flags") + ";") if c.get("flags") else "") \
                            + "reversed_against_invert"
                    c["dir_src"] = "network"
                    ui, di = c.get("us_invert"), c.get("ds_invert")
                    c["slope"] = round((ui - di) / c["length_m"], 6) \
                        if (ui is not None and di is not None and c["length_m"]) else None
                    flipped += 1
    print(f"  {flipped} conduits reversed to drain toward their outlet")

    reach = reachable_from_outfalls(by_uid, conduits)
    print(f"  reaching an outfall now: {len(reach)}/{len(by_uid)} "
          f"({100*len(reach)/len(by_uid):.0f}%)")
    sizes = sorted((len(c) for c in components(by_uid, conduits)), reverse=True)
    print(f"  components: {len(sizes)}, largest {sizes[0]}, "
          f"singletons {sum(1 for s in sizes if s == 1)}")
    adv = sum(1 for c in conduits if c.get("slope") is not None and c["slope"] < 0)
    print(f"  conduits now running uphill (real local sumps): {adv}")

    with open(f"{NET}/nodes.json", "w") as f:
        json.dump(nodes, f)
    with open(f"{NET}/conduits.json", "w") as f:
        json.dump(conduits, f)
    print("updated nodes.json / conduits.json")


if __name__ == "__main__":
    main()
