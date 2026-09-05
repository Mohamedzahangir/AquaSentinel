#!/usr/bin/env python3
"""Rewrite only the per-ward map_context layer (no OCR, so this is quick)."""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = "/Users/admin/Desktop/projects/swd"
PDFS_DIR = f"{ROOT}/GeoPDF"
WARDS_DIR = f"{ROOT}/derived/wards"
AFFINES = f"{ROOT}/derived/affines.json"

from run_extract import CONTEXT_ROLES  # noqa: E402


def work(args):
    fn, rec = args
    from swdlib.grid import Affine
    from swdlib.geom import extract_paths
    from swdlib import export
    ward = rec.get("ward")
    out_dir = os.path.join(WARDS_DIR, f"ward_{ward:03d}")
    if not os.path.isdir(out_dir):
        return fn, ward, 0
    aff = Affine(rec["ae"], rec["be"], rec["an"], rec["bn"], rec["swap"])
    paths = extract_paths(os.path.join(PDFS_DIR, fn), aff, roles=CONTEXT_ROLES)
    n = export.write_lines(os.path.join(out_dir, "map_context.shp"), paths, ward=ward)
    return fn, ward, n


def main():
    affines = json.load(open(AFFINES))
    todo = [(fn, r) for fn, r in sorted(affines.items()) if r.get("ok")]
    print(f"{len(todo)} sheets; roles={sorted(CONTEXT_ROLES)}", flush=True)
    total = 0
    done = 0
    with ProcessPoolExecutor(max_workers=6) as ex:
        for fut in as_completed([ex.submit(work, t) for t in todo]):
            fn, ward, n = fut.result()
            total += n
            done += 1
            if done % 20 == 0:
                print(f"  [{done}/{len(todo)}] running total {total}", flush=True)
    print(f"DONE  {total} context features")


if __name__ == "__main__":
    main()
