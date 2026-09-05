#!/usr/bin/env python3
"""Run the SWMM model on Linux through swmm-toolkit (the Mac-built bin/runswmm
cannot execute here). Same engine (EPA SWMM 5.2), same .inp, same outputs."""
import sys, time
from swmm.toolkit import solver

inp = sys.argv[1] if len(sys.argv) > 1 else "derived/swd_model.inp"
rpt = sys.argv[2] if len(sys.argv) > 2 else "derived/swmm/run2015.rpt"
out = sys.argv[3] if len(sys.argv) > 3 else "derived/swmm/run2015.out"
t0 = time.time()
solver.swmm_run(inp, rpt, out)
print(f"done in {(time.time()-t0)/60:.1f} min", flush=True)
