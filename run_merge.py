#!/usr/bin/env python3
"""
Combine the per-ward extractions into city-wide datasets.

Writes:
  derived/chennai_swd.gpkg          all layers, EPSG:32644
  derived/shp/*.shp                 the same as flat shapefiles
  derived/swd_model.inp             a SWMM 5 skeleton (junctions/outfalls/conduits)
  derived/qa_report.json            per-ward and overall quality figures
"""
import glob
import json
import os
import sys

from osgeo import gdal, ogr, osr

gdal.UseExceptions()
ogr.UseExceptions()

ROOT = "/Users/admin/Desktop/projects/swd"
WARDS = f"{ROOT}/derived/wards"
OUT = f"{ROOT}/derived"
GPKG = f"{OUT}/chennai_swd.gpkg"
SHP_DIR = f"{OUT}/shp"

LAYERS = ["swd_nodes", "swd_conduits", "swd_drains", "map_context"]


def srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(32644)
    return s


def merge_layer(name: str, gpkg_ds, shp_path: str):
    parts = sorted(glob.glob(f"{WARDS}/ward_*/{name}.shp"))
    if not parts:
        return 0
    src0 = ogr.Open(parts[0])
    l0 = src0.GetLayer()
    geom_type = l0.GetGeomType()
    defn = l0.GetLayerDefn()
    fields = [defn.GetFieldDefn(i) for i in range(defn.GetFieldCount())]

    out_g = gpkg_ds.CreateLayer(name, srs(), geom_type)
    for fd in fields:
        out_g.CreateField(fd)

    shp_drv = ogr.GetDriverByName("ESRI Shapefile")
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = os.path.splitext(shp_path)[0] + ext
        if os.path.exists(p):
            os.remove(p)
    shp_ds = shp_drv.CreateDataSource(shp_path)
    out_s = shp_ds.CreateLayer(name, srs(), geom_type)
    for fd in fields:
        out_s.CreateField(fd)

    n = 0
    for part in parts:
        ds = ogr.Open(part)
        lyr = ds.GetLayer()
        for feat in lyr:
            for out in (out_g, out_s):
                nf = ogr.Feature(out.GetLayerDefn())
                nf.SetFrom(feat)
                out.CreateFeature(nf)
                nf = None
            n += 1
        ds = None
    shp_ds = None
    return n


def load_json(name):
    out = []
    for p in sorted(glob.glob(f"{WARDS}/ward_*/{name}.json")):
        try:
            out.extend(json.load(open(p)))
        except Exception:
            pass
    return out


def uid(ward, node_id):
    """Globally unique node name.

    The sheets number manholes per ward, so "A10" exists in ~100 wards. Keying
    on the bare identifier silently merges unrelated structures across the city,
    so every name is scoped by its ward.
    """
    return f"W{ward}_{node_id}" if ward is not None else str(node_id)


def write_swmm(path: str, nodes, conduits):
    """A SWMM 5 skeleton. Hydrology (rain, subcatchments) still has to be added."""
    by_id = {uid(n.get("ward"), n["node_id"]): n for n in nodes}
    used = set()
    for c in conduits:
        c["_from"] = uid(c.get("ward"), c["from_node"])
        c["_to"] = uid(c.get("ward"), c["to_node"])
        used.add(c["_from"])
        used.add(c["_to"])

    junctions, outfalls = [], []
    for nid in sorted(used):
        n = by_id.get(nid)
        if not n:
            continue
        invert = n.get("invert")
        top = n.get("road_edge") if n.get("road_edge") is not None else n.get("drain_top")
        if invert is None:
            continue
        depth = max((top - invert), 0.3) if top is not None else 1.0
        (outfalls if n.get("is_outlet") else junctions).append((nid, invert, depth))

    with open(path, "w") as f:
        f.write("[TITLE]\nGCC storm water drain network\n")
        f.write("Extracted from GCC/SECON-JBA ward base maps (2021 survey)\n\n")
        f.write("[OPTIONS]\n")
        f.write("FLOW_UNITS           CMS\nINFILTRATION         HORTON\n")
        f.write("FLOW_ROUTING         DYNWAVE\nLINK_OFFSETS         DEPTH\n")
        f.write("MIN_SLOPE            0\nSTART_DATE           01/01/2021\n")
        f.write("REPORT_STEP          00:05:00\nROUTING_STEP         0:00:15\n\n")

        f.write(";;Name  InvertElev  MaxDepth  InitDepth  SurDepth  Aponded\n[JUNCTIONS]\n")
        for nid, inv, d in junctions:
            f.write(f"{nid:<20}{inv:<12.3f}{d:<10.3f}0          0         0\n")

        f.write("\n;;Name  InvertElev  Type  StageData  Gated  RouteTo\n[OUTFALLS]\n")
        for nid, inv, _ in outfalls:
            f.write(f"{nid:<20}{inv:<12.3f}FREE                    NO\n")

        f.write("\n;;Name  FromNode  ToNode  Length  Roughness  InOffset  OutOffset\n[CONDUITS]\n")
        for c in conduits:
            if c["_from"] not in used or c["_to"] not in used:
                continue
            if c.get("us_invert") is None or c.get("ds_invert") is None:
                continue
            cid = f"{c['_from']}->{c['_to']}"
            f.write(f"{cid[:31]:<34}{c['_from']:<20}{c['_to']:<20}"
                    f"{c['length_m']:<10.2f}0.013     0          0\n")

        f.write("\n;;Link  Shape  Geom1  Geom2  Geom3  Geom4  Barrels\n[XSECTIONS]\n")
        for c in conduits:
            if c.get("us_invert") is None or c.get("ds_invert") is None:
                continue
            if c["_from"] not in used or c["_to"] not in used:
                continue
            w = c.get("width_m") or 0.6
            d = c.get("depth_m") or 0.6
            shape = "RECT_CLOSED" if (c.get("type") or "C") == "C" else "RECT_OPEN"
            cid = f"{c['_from']}->{c['_to']}"
            f.write(f"{cid[:31]:<34}{shape:<14}{d:<8.3f}{w:<8.3f}0        0        1\n")

        f.write("\n[COORDINATES]\n;;Node  X  Y\n")
        for nid in sorted(used):
            n = by_id.get(nid)
            if n:
                f.write(f"{nid:<20}{n['east']:<14.3f}{n['north']:.3f}\n")
    return len(junctions), len(outfalls)


def main():
    os.makedirs(SHP_DIR, exist_ok=True)
    if os.path.exists(GPKG):
        os.remove(GPKG)
    gpkg = ogr.GetDriverByName("GPKG").CreateDataSource(GPKG)

    counts = {}
    for name in LAYERS:
        counts[name] = merge_layer(name, gpkg, f"{SHP_DIR}/{name}.shp")
        print(f"  {name:14s} {counts[name]:7d} features")
    gpkg = None

    nodes = load_json("nodes")
    conduits = load_json("conduits")
    nj, nof = write_swmm(f"{OUT}/swd_model.inp", nodes, conduits)
    print(f"  SWMM: {nj} junctions, {nof} outfalls, {len(conduits)} conduits")

    summary = json.load(open(f"{OUT}/extract_summary.json"))
    ok = [r for r in summary if r.get("ok")]
    qa = {
        "wards": len(ok),
        "features": counts,
        "nodes_total": len(nodes),
        "nodes_with_invert": sum(1 for n in nodes if n.get("invert") is not None),
        "nodes_with_all_levels": sum(
            1 for n in nodes
            if all(n.get(k) is not None for k in ("invert", "drain_top", "road_edge"))),
        "outlets": sum(1 for n in nodes if n.get("is_outlet")),
        "conduits_total": len(conduits),
        "conduits_with_slope": sum(1 for c in conduits if c.get("slope") is not None),
        "conduits_adverse": sum(1 for c in conduits
                                if c.get("slope") is not None and c["slope"] < 0),
        "direction_source": {
            k: sum(1 for c in conduits if c.get("dir_src") == k)
            for k in ("invert", "arrow", "assumed")},
        "median_located_pct": sorted(r["nodes"]["located_of_schedule"] for r in ok)[len(ok) // 2],
        "per_ward": sorted(
            [{"ward": r["ward"], "nodes": r["nodes"]["nodes"],
              "located_pct": r["nodes"]["located_of_schedule"],
              "with_invert": r["nodes"]["with_invert"],
              "conduits": r["conduits"]["conduits"],
              "adverse": r["conduits"]["adverse_slope"]} for r in ok],
            key=lambda d: d["ward"] if d["ward"] is not None else 0),
    }
    with open(f"{OUT}/qa_report.json", "w") as f:
        json.dump(qa, f, indent=1)
    print(json.dumps({k: v for k, v in qa.items() if k != "per_ward"}, indent=1))


if __name__ == "__main__":
    main()
