#!/usr/bin/env python3
"""
Compare SWMM's predicted node flooding against the streets that actually flooded.

The flooded-street record (maps-site, OSM-derived) is an independent observation:
it says where water stood, and was never used to build or tune the model. So it
is a fair — if coarse — test of whether the network reproduces reality.

Reported as a 2x2 contingency table plus the usual skill scores, then written
back onto the nodes so the agreement can be mapped.
"""
import json
import os
import re
import sys

ROOT = "/Users/admin/Desktop/projects/swd"
NET = f"{ROOT}/derived/network"
RPT = f"{ROOT}/derived/swmm/run2015.rpt"


def parse_section(path: str, title: str) -> list[list[str]]:
    """Rows of a named summary table in a SWMM report.

    A SWMM table has two rule lines — one under the explanatory sentence and one
    under the column headings — so the data cannot be found by counting rules.
    Everything between the title and the next section banner is returned instead,
    and the caller decides what is a data row.
    """
    rows, inside = [], False
    with open(path, errors="replace") as f:
        for line in f:
            s = line.strip()
            if not inside:
                if s == title:
                    inside = True
                continue
            if set(s) == {"*"} and s:
                if rows:                         # banner of the next section
                    break
                continue                         # the title's own closing banner
            if not s or set(s) <= {"-"}:         # blank or a rule
                continue
            rows.append(s.split())
    return rows


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    if not os.path.exists(RPT):
        print("no report yet:", RPT)
        return
    txt = open(RPT, errors="replace").read()

    err = re.findall(r"^\s*ERROR.*$", txt, re.M)
    if err:
        print(f"{len(err)} errors in report; first: {err[0].strip()[:100]}")

    # the label itself is padded with dots, so the value pattern must require a digit
    for label, pat in (("runoff continuity error",
                        r"Runoff Quantity Continuity.*?Continuity Error \(%\)[ .]*(-?\d+\.\d+)"),
                       ("flow routing continuity error",
                        r"Flow Routing Continuity.*?Continuity Error \(%\)[ .]*(-?\d+\.\d+)")):
        mm = re.search(pat, txt, re.S)
        if mm:
            print(f"{label}: {mm.group(1)}%")

    flooding = parse_section(RPT, "Node Flooding Summary")
    flooded = {}
    for r in flooding:
        if len(r) >= 7 and num(r[1]) is not None:
            flooded[r[0]] = {"hours": num(r[1]), "peak_cms": num(r[2]),
                             "volume_Ml": num(r[-2])}
    print(f"\nSWMM reports flooding at {len(flooded)} nodes")

    nodes = json.load(open(f"{NET}/nodes.json"))
    by_uid = {f"W{n['ward']}_{n['node_id']}": n for n in nodes}

    # Only nodes actually in the model can be scored.
    inp = open(f"{ROOT}/derived/swd_model.inp", errors="replace").read()
    modelled = set()
    for sec in ("JUNCTIONS", "OUTFALLS"):
        m = re.search(rf"\[{sec}\](.*?)(?=\n\[|\Z)", inp, re.S)
        if m:
            for line in m.group(1).splitlines():
                line = line.strip()
                if line and not line.startswith(";"):
                    modelled.add(line.split()[0])
    scored = [u for u in modelled if u in by_uid]
    print(f"{len(scored)} modelled nodes can be scored against the flood record")

    tp = fp = fn = tn = 0
    for u in scored:
        pred = u in flooded
        obs = by_uid[u].get("flood_m") is not None
        if pred and obs:
            tp += 1
        elif pred and not obs:
            fp += 1
        elif obs:
            fn += 1
        else:
            tn += 1
    tot = tp + fp + fn + tn
    print(f"\n                     observed flooded   not observed")
    print(f"  model floods       {tp:14d} {fp:14d}")
    print(f"  model dry          {fn:14d} {tn:14d}")
    if tot:
        base = (tp + fn) / tot
        pod = tp / (tp + fn) if (tp + fn) else 0.0
        far = fp / (tp + fp) if (tp + fp) else 0.0
        csi = tp / (tp + fp + fn) if (tp + fp + fn) else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        lift = prec / base if base else 0.0
        print(f"\n  base rate (observed flooded)   {100*base:5.1f}%")
        print(f"  precision (of predicted)       {100*prec:5.1f}%   lift x{lift:.2f}")
        print(f"  probability of detection       {100*pod:5.1f}%")
        print(f"  false alarm ratio              {100*far:5.1f}%")
        print(f"  critical success index         {100*csi:5.1f}%")

    # the priority list: predicted to flood, observed to flood, low-lying
    inv = sorted(n["invert"] for n in nodes if n.get("invert") is not None)
    q1 = inv[len(inv) // 4] if inv else 0
    prio = []
    for u in scored:
        n = by_uid[u]
        if u in flooded and n.get("flood_m") is not None:
            prio.append((flooded[u]["hours"] or 0, flooded[u]["volume_Ml"] or 0, u,
                         n.get("invert"), n.get("ward"), n.get("flood_road")))
    prio.sort(reverse=True)
    print(f"\n{len(prio)} nodes both predicted AND observed to flood. Worst 15:")
    print("   node                 ward  hrs   vol(Ml)  invert  street type")
    for hrs, vol, u, iv, w, road in prio[:15]:
        print(f"   {u:<20} {str(w):>4}  {hrs:4.1f}  {vol:7.2f}  {iv if iv is not None else '-':>6}  {road or ''}")

    for n in nodes:
        u = f"W{n['ward']}_{n['node_id']}"
        f = flooded.get(u)
        n["swmm_flood_hr"] = f["hours"] if f else (0.0 if u in modelled else None)
        n["swmm_flood_Ml"] = f["volume_Ml"] if f else (0.0 if u in modelled else None)
        obs = n.get("flood_m") is not None
        n["agree"] = ("" if u not in modelled else
                      "hit" if (f and obs) else
                      "false_alarm" if f else
                      "miss" if obs else "correct_dry")
    with open(f"{NET}/nodes.json", "w") as fh:
        json.dump(nodes, fh)
    print("\nwritten swmm_flood_hr / swmm_flood_Ml / agree onto nodes.json")


if __name__ == "__main__":
    main()
