#!/usr/bin/env python3
"""
Pack the SWMM binary output for the viewer.

run2015.out is 271 MB at a 15-minute report step. The viewer needs, per hour:
node depth (surcharge up the shaft), node flooding rate (ponding at the
street), link depth (water surface inside the culvert) and link velocity
(how fast the flow band travels). Everything is quantised to uint16 and
written as one flat binary with a small JSON index alongside.

Per hour the *maximum* of the four 15-min samples is kept for depths and the
flooding rate (so a short surcharge is not missed between frames) and the
mean for velocity.

Output: web/data/swmm.bin  +  web/data/swmm.json
"""
import json, os, sys, time
import numpy as np
from swmm.toolkit import output, shared_enum as se

OUT = sys.argv[1] if len(sys.argv) > 1 else "derived/swmm/run2015.out"
WEB = "web/data"
PER_HOUR = 4          # 15-min report step -> hourly bins

t0 = time.time()
h = output.init()
output.open(h, OUT)
n_sub, n_nodes, n_links = output.get_proj_size(h)[:3]   # then system, pollutants
n_per = output.get_times(h, se.Time.NUM_PERIODS)
rs = output.get_times(h, se.Time.REPORT_STEP)
start = output.get_start_date(h)
print(f"{n_nodes} nodes, {n_links} links, {n_per} periods at {rs}s, start {start}")
hours = n_per // PER_HOUR

def series(getter, n, attr, agg):
    arr = np.zeros((n, hours), dtype=np.float32)
    for i in range(n):
        s = np.asarray(getter(h, i, attr, 0, n_per - 1), dtype=np.float32)
        if len(s) < hours * PER_HOUR:     # end index is exclusive: repeat the last sample
            s = np.concatenate([s, np.repeat(s[-1:], hours * PER_HOUR - len(s))])
        s = s[:hours * PER_HOUR]
        s = s.reshape(hours, PER_HOUR)
        arr[i] = s.max(axis=1) if agg == "max" else s.mean(axis=1)
    return arr

node_depth = series(output.get_node_series, n_nodes, se.NodeAttribute.INVERT_DEPTH, "max")
node_flood = series(output.get_node_series, n_nodes, se.NodeAttribute.FLOODING_LOSSES, "max")
link_depth = series(output.get_link_series, n_links, se.LinkAttribute.FLOW_DEPTH, "max")
link_vel   = series(output.get_link_series, n_links, se.LinkAttribute.FLOW_VELOCITY, "mean")
node_names = [output.get_elem_name(h, se.ElementType.NODE, i) for i in range(n_nodes)]
link_names = [output.get_elem_name(h, se.ElementType.LINK, i) for i in range(n_links)]
output.close(h)

q = lambda a, scale, cap: np.clip(np.nan_to_num(a) * scale, 0, cap).astype(np.uint16)
blob = np.concatenate([
    q(node_depth, 1000, 65000).ravel(),     # mm
    q(node_flood, 1000, 65000).ravel(),     # L/s
    q(link_depth, 1000, 65000).ravel(),     # mm
    q(np.abs(link_vel), 1000, 65000).ravel(),  # mm/s
])
os.makedirs(WEB, exist_ok=True)
blob.tofile(f"{WEB}/swmm.bin")
# SWMM's decoded start date is a datetime-like; the .inp says 11/30/2015 00:00
json.dump({
    "start": "2015-11-30T00:00:00+05:30", "dt_h": 1, "steps": hours,
    "nodes": node_names, "links": link_names,
    "layout": "uint16 little-endian: node_depth_mm[nodes*steps], node_flood_Lps[nodes*steps], "
              "link_depth_mm[links*steps], link_vel_mmps[links*steps]; each hour is the max "
              "of its four 15-min samples (mean for velocity)",
    "model": "EPA SWMM 5.2 dynamic wave, 15 s routing step, 30 Nov - 8 Dec 2015",
    "skill": "none: lift x0.97 against the flooded-street record (807 hits, 686 false alarms, "
             "1834 misses, 1422 correct dry). 372 nodes flood >120 h of 192 h = hydraulic dead ends.",
}, open(f"{WEB}/swmm.json", "w"))
print(f"{hours} hourly steps -> swmm.bin {os.path.getsize(f'{WEB}/swmm.bin')/1e6:.1f} MB, "
      f"nodes flooding at peak: {(node_flood.max(axis=1) > 0).sum()}  ({time.time()-t0:.0f}s)")
