#!/usr/bin/env python3
"""Batch: solve the exact page->UTM affine for every ward sheet."""
import json
import os
import re
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PDFS_DIR = "/Users/admin/Desktop/projects/swd/GeoPDF"
OUT = "/Users/admin/Desktop/projects/swd/derived/affines.json"

SKIP = {"Council Resolution 31.01.2024.pdf"}


def ward_number(fn: str):
    m = re.search(r"(\d{1,3})", os.path.splitext(fn)[0])
    return int(m.group(1)) if m else None


def work(fn: str):
    from swdlib.grid import georeference
    path = os.path.join(PDFS_DIR, fn)
    t0 = time.time()
    try:
        aff, gi, labels = georeference(path)
    except Exception as e:
        return fn, {"ok": False, "error": f"{e}", "tb": traceback.format_exc()[-800:]}
    rec = {
        "ok": aff is not None,
        "ward": ward_number(fn),
        "seconds": round(time.time() - t0, 1),
        "n_labels": len(labels),
        "grid": [len(gi.v_lines), len(gi.h_lines)],
        "frame": [gi.frame.x0, gi.frame.y0, gi.frame.x1, gi.frame.y1] if gi.frame else None,
        "page": [gi.page_w, gi.page_h],
    }
    if aff:
        rec.update({
            "ae": aff.ae, "be": aff.be, "an": aff.an, "bn": aff.bn,
            "swap": aff.swap,
            "n_e": aff.n_e_labels, "n_n": aff.n_n_labels,
            "rms_e": aff.rms_e, "rms_n": aff.rms_n,
        })
    return fn, rec


def main():
    files = sorted(f for f in os.listdir(PDFS_DIR)
                   if f.lower().endswith(".pdf") and f not in SKIP)
    print(f"{len(files)} sheets", flush=True)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)

    results = {}
    done = 0
    with ProcessPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(work, f): f for f in files}
        for fut in as_completed(futs):
            fn, rec = fut.result()
            results[fn] = rec
            done += 1
            tag = "ok  " if rec.get("ok") else "FAIL"
            extra = (f"swap={rec['swap']} n=({rec['n_e']},{rec['n_n']}) "
                     f"rms=({rec['rms_e']:.2f},{rec['rms_n']:.2f})") if rec.get("ok") else \
                    rec.get("error", "no affine")[:60]
            print(f"[{done:3d}/{len(files)}] {tag} {fn[:32]:32s} {extra}", flush=True)

    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=2)
    ok = sum(1 for r in results.values() if r.get("ok"))
    print(f"\nDONE  ok={ok}  fail={len(results)-ok}  -> {OUT}")


if __name__ == "__main__":
    main()
