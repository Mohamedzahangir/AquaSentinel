#!/usr/bin/env python3
"""
Full extraction: every GCC SWD ward sheet -> georeferenced simulation-ready data.

Per sheet:
  * exact page->UTM affine   (pre-computed by run_georef.py)
  * manhole schedule         (OCR of the vector cell grid)
  * manhole positions        (OCR of the on-map identifier labels)
  * drain centrelines etc.   (CAD layers, straight to UTM)
  * node/conduit network     (topology from the identifier sequence,
                              flow direction from surveyed inverts)

Outputs per ward under derived/wards/ward_NNN/ and a city-wide GeoPackage.
"""
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = "/Users/admin/Desktop/projects/swd"
PDFS_DIR = f"{ROOT}/GeoPDF"
OUT_ROOT = f"{ROOT}/derived"
WARDS_DIR = f"{OUT_ROOT}/wards"
AFFINES = f"{OUT_ROOT}/affines.json"

# Genuinely useful context linework. Deliberately excludes the hatch-fill
# layers (Outlet_hatch, Pond, Inundated_area): those are drawn as 10^5 tiny
# strokes per sheet, which bloats the output enormously and carries no
# information the outfall flag on the nodes does not already give.
CONTEXT_ROLES = {
    "road", "canal", "river", "sea",
    "ward_boundary", "zone_boundary", "railway", "flyover", "bridge",
    "divider", "pump",
}
DRAIN_ROLES = {"drain", "drain_open", "drain_pipe", "canal"}


def process(fn: str, aff_rec: dict) -> dict:
    from swdlib.grid import Affine
    from swdlib.geom import extract_paths
    from swdlib.maplabels import read_role
    from swdlib.tables import read_sheet_tables
    from swdlib.nodes import build_nodes
    from swdlib.network import build_conduits, flow_arrows, merge_lines
    from swdlib import export

    path = os.path.join(PDFS_DIR, fn)
    ward = aff_rec.get("ward")
    t0 = time.time()
    aff = Affine(aff_rec["ae"], aff_rec["be"], aff_rec["an"], aff_rec["bn"],
                 aff_rec["swap"])

    schedule = read_sheet_tables(path)
    t_tab = time.time()
    labels = read_role(path, "node_label", dpi=1100)
    inv_labels = read_role(path, "invert_label", dpi=1100)
    t_lbl = time.time()

    nodes, nstats = build_nodes(labels, schedule, aff, ward=ward,
                                invert_labels=inv_labels)
    drains = extract_paths(path, aff, roles=DRAIN_ROLES)
    arrows = flow_arrows(extract_paths(path, aff, roles={"flow_arrow"}))
    conduits, cstats = build_conduits(nodes, drains, arrows, ward=ward, snap_tol=35.0)

    out_dir = os.path.join(WARDS_DIR, f"ward_{ward:03d}" if ward is not None else
                           os.path.splitext(fn)[0])
    os.makedirs(out_dir, exist_ok=True)

    export.write_nodes(os.path.join(out_dir, "swd_nodes.shp"), nodes)
    export.write_conduits(os.path.join(out_dir, "swd_conduits.shp"), conduits)

    merged = merge_lines([ln for p in drains for ln in p.lines if len(ln) >= 2])
    export.write_merged_lines(os.path.join(out_dir, "swd_drains.shp"), merged, ward=ward)

    context = extract_paths(path, aff, roles=CONTEXT_ROLES)
    export.write_lines(os.path.join(out_dir, "map_context.shp"), context, ward=ward)

    with open(os.path.join(out_dir, "schedule.json"), "w") as fh:
        json.dump([{k: v for k, v in r.items() if not k.startswith("_")}
                   for r in schedule], fh, indent=1)
    with open(os.path.join(out_dir, "nodes.json"), "w") as fh:
        json.dump([n.as_record() for n in nodes], fh, indent=1)
    with open(os.path.join(out_dir, "conduits.json"), "w") as fh:
        json.dump([c.as_record() for c in conduits], fh, indent=1)

    return {
        "ok": True, "file": fn, "ward": ward, "out": out_dir,
        "seconds": round(time.time() - t0, 1),
        "t_table": round(t_tab - t0, 1), "t_labels": round(t_lbl - t_tab, 1),
        "nodes": nstats, "conduits": cstats,
        "context_paths": len(context), "drain_runs": len(merged),
    }


def work(args):
    fn, rec = args
    try:
        return process(fn, rec)
    except Exception as e:
        return {"ok": False, "file": fn, "ward": rec.get("ward"),
                "error": f"{e}", "tb": traceback.format_exc()[-1200:]}


def main():
    affines = json.load(open(AFFINES))
    todo = [(fn, rec) for fn, rec in sorted(affines.items()) if rec.get("ok")]
    only = sys.argv[1:] or None
    if only:
        todo = [(f, r) for f, r in todo if any(o in f for o in only)]
    os.makedirs(WARDS_DIR, exist_ok=True)
    print(f"{len(todo)} sheets", flush=True)

    results = []
    done = 0
    with ProcessPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(work, t) for t in todo]
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            done += 1
            if r["ok"]:
                n, c = r["nodes"], r["conduits"]
                print(f"[{done:3d}/{len(todo)}] ok   ward {str(r['ward']):>4s} "
                      f"{r['seconds']:5.0f}s  nodes {n['nodes']:4d} "
                      f"(loc {n['located_of_schedule']:5.1f}%, inv {n['with_invert']:4d})  "
                      f"conduits {c['conduits']:4d} "
                      f"(inv {c['dir_from_invert']:3d}/arr {c['dir_from_arrow']:3d}/asm {c['dir_assumed']:3d}, "
                      f"adverse {c['adverse_slope']:3d})", flush=True)
            else:
                print(f"[{done:3d}/{len(todo)}] FAIL ward {r['ward']} {r['file']}: {r['error'][:90]}", flush=True)

    with open(os.path.join(OUT_ROOT, "extract_summary.json"), "w") as fh:
        json.dump(results, fh, indent=1)
    ok = [r for r in results if r["ok"]]
    print(f"\nDONE  ok={len(ok)}  fail={len(results)-len(ok)}")
    if ok:
        print(f"  nodes total    {sum(r['nodes']['nodes'] for r in ok)}")
        print(f"  conduits total {sum(r['conduits']['conduits'] for r in ok)}")
        print(f"  adverse slopes {sum(r['conduits']['adverse_slope'] for r in ok)}")


if __name__ == "__main__":
    main()
