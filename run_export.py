#!/usr/bin/env python3
"""
Export the city-wide network: GeoPackage + shapefiles + a runnable SWMM 5 model.

Reads derived/network/{nodes,conduits,subcatchments,runs}.json and writes
everything under derived/. Rainfall, if a series has been fetched, is written
into the model as a time series and rain gauge.
"""
import json
import math
import os
import sys
from collections import defaultdict

from osgeo import ogr, osr

ogr.UseExceptions()

ROOT = "/Users/admin/Desktop/projects/swd"
NET = f"{ROOT}/derived/network"
OUT = f"{ROOT}/derived"
GPKG = f"{OUT}/chennai_swd.gpkg"
SHP = f"{OUT}/shp"
RAIN = f"{OUT}/rainfall"


def srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(32644)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


NODE_FIELDS = [
    ("uid", ogr.OFTString, 28), ("node_id", ogr.OFTString, 24),
    ("ward", ogr.OFTInteger, 0), ("si", ogr.OFTInteger, 0),
    ("drain_top", ogr.OFTReal, 0), ("invert", ogr.OFTReal, 0),
    ("road_edge", ogr.OFTReal, 0), ("width_m", ogr.OFTReal, 0),
    ("depth_m", ogr.OFTReal, 0), ("type", ogr.OFTString, 4),
    ("is_outlet", ogr.OFTInteger, 0), ("microws", ogr.OFTString, 24),
    ("subbasin", ogr.OFTString, 60), ("locator", ogr.OFTString, 10),
    ("conf", ogr.OFTReal, 0), ("snap_m", ogr.OFTReal, 0),
    ("flags", ogr.OFTString, 120),
    ("flood_m", ogr.OFTReal, 0), ("flood_road", ogr.OFTString, 24),
    ("swmm_fl_hr", ogr.OFTReal, 0), ("swmm_fl_Ml", ogr.OFTReal, 0),
    ("agree", ogr.OFTString, 14),
]
COND_FIELDS = [
    ("cond_id", ogr.OFTString, 60), ("from_uid", ogr.OFTString, 28),
    ("to_uid", ogr.OFTString, 28), ("from_ward", ogr.OFTInteger, 0),
    ("to_ward", ogr.OFTInteger, 0), ("cross_ward", ogr.OFTInteger, 0),
    ("length_m", ogr.OFTReal, 0), ("us_invert", ogr.OFTReal, 0),
    ("ds_invert", ogr.OFTReal, 0), ("width_m", ogr.OFTReal, 0),
    ("depth_m", ogr.OFTReal, 0), ("type", ogr.OFTString, 4),
    ("slope", ogr.OFTReal, 0), ("dir_src", ogr.OFTString, 10),
    ("flags", ogr.OFTString, 60),
]
SUB_FIELDS = [
    ("uid", ogr.OFTString, 28), ("ward", ogr.OFTInteger, 0),
    ("area_ha", ogr.OFTReal, 0), ("width_m", ogr.OFTReal, 0),
    ("imperv_pct", ogr.OFTReal, 0), ("slope_pct", ogr.OFTReal, 0),
    ("microws", ogr.OFTString, 24), ("subbasin", ogr.OFTString, 60),
]
RUN_FIELDS = [("run_id", ogr.OFTInteger, 0), ("length_m", ogr.OFTReal, 0)]


def make_layer(ds, name, gtype, fields):
    lyr = ds.CreateLayer(name, srs(), gtype)
    for n, t, w in fields:
        fd = ogr.FieldDefn(n, t)
        if t == ogr.OFTReal:
            fd.SetWidth(19)
            fd.SetPrecision(6)
        elif w:
            fd.SetWidth(w)
        lyr.CreateField(fd)
    return lyr


def setf(f, name, v):
    if v is None or v == "":
        f.SetFieldNull(name)
    elif isinstance(v, bool):
        f.SetField(name, int(v))
    else:
        f.SetField(name, v)


def write(name, gtype, fields, rows, geom_of, gpkg, shp_dir):
    drv = ogr.GetDriverByName("ESRI Shapefile")
    path = os.path.join(shp_dir, f"{name}.shp")
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = os.path.splitext(path)[0] + ext
        if os.path.exists(p):
            os.remove(p)
    sd = drv.CreateDataSource(path)
    ls = make_layer(sd, name, gtype, fields)
    lg = make_layer(gpkg, name, gtype, fields)
    names = [n for n, _, _ in fields]
    for out in (lg, ls):
        out.StartTransaction()
    n = 0
    for r in rows:
        g = geom_of(r)
        if g is None:
            continue
        for out in (lg, ls):
            f = ogr.Feature(out.GetLayerDefn())
            for k in names:
                setf(f, k, r.get(k))
            f.SetGeometry(g)
            out.CreateFeature(f)
            f = None
        n += 1
    for out in (lg, ls):
        out.CommitTransaction()
    sd = None
    return n


def swmm(path, nodes, conduits, subs, rain=None):
    by_uid = {n["uid"]: n for n in nodes}
    used = set()
    for c in conduits:
        used.add(c["from_uid"])
        used.add(c["to_uid"])

    junctions, outfalls = [], []
    for uid in sorted(used):
        n = by_uid.get(uid)
        if not n or n.get("invert") is None:
            continue
        top = n.get("road_edge") if n.get("road_edge") is not None else n.get("drain_top")
        depth = max(top - n["invert"], 0.3) if top is not None else 1.0
        (outfalls if n.get("is_outlet") else junctions).append((uid, n["invert"], depth))
    modelled = {u for u, _, _ in junctions} | {u for u, _, _ in outfalls}

    # SWMM allows an outfall exactly one inlet link and no outlet link. A
    # manhole that discharges but also has several pipes arriving therefore has
    # to stay a junction, with the discharge represented by a short link to a
    # dedicated outfall node just downstream. This is the conventional way to
    # model it and changes no hydraulics.
    indeg, outdeg = defaultdict(int), defaultdict(int)
    for c in conduits:
        if c["from_uid"] in modelled and c["to_uid"] in modelled:
            indeg[c["to_uid"]] += 1
            outdeg[c["from_uid"]] += 1
    extra_links, kept_out, demoted = [], [], 0
    for uid, inv, depth in outfalls:
        if indeg[uid] <= 1 and outdeg[uid] == 0:
            kept_out.append((uid, inv, depth))
            continue
        junctions.append((uid, inv, depth))
        oid = f"{uid}_OF"
        kept_out.append((oid, round(inv - 0.05, 3), depth))
        extra_links.append({
            "cond_id": f"{uid}->{oid}", "from_uid": uid, "to_uid": oid,
            "length_m": 5.0, "us_invert": inv, "ds_invert": round(inv - 0.05, 3),
            "width_m": None, "depth_m": None, "type": "O", "slope": 0.01,
        })
        n = by_uid[uid]
        by_uid[oid] = {"east": n["east"] + 5.0, "north": n["north"],
                       "invert": round(inv - 0.05, 3), "is_outlet": True}
        demoted += 1
    outfalls = sorted(kept_out)
    junctions = sorted(junctions)
    conduits = list(conduits) + extra_links
    modelled = {u for u, _, _ in junctions} | {u for u, _, _ in outfalls}
    if demoted:
        print(f"  {demoted} outfalls with several inlets given a dedicated outfall node")

    with open(path, "w") as f:
        f.write("[TITLE]\nGreater Chennai storm water drain network\n")
        f.write("Extracted from the GCC / SECON-JBA ward base maps (2021 survey).\n")
        f.write("Subcatchments are Thiessen cells around each manhole.\n\n")
        f.write("[OPTIONS]\n")
        for k, v in (("FLOW_UNITS", "CMS"), ("INFILTRATION", "HORTON"),
                     ("FLOW_ROUTING", "DYNWAVE"), ("LINK_OFFSETS", "DEPTH"),
                     ("MIN_SLOPE", "0"), ("ALLOW_PONDING", "NO"),
                     ("SKIP_STEADY_STATE", "NO"),
                     ("START_DATE", rain["start"] if rain else "01/01/2021"),
                     ("START_TIME", "00:00:00"),
                     ("END_DATE", rain["end"] if rain else "01/02/2021"),
                     ("END_TIME", "00:00:00"),
                     ("REPORT_STEP", "00:15:00"), ("WET_STEP", "00:05:00"),
                     ("DRY_STEP", "01:00:00"), ("ROUTING_STEP", "0:00:15")):
            f.write(f"{k:<21}{v}\n")

        if rain:
            f.write("\n[RAINGAGES]\n;;Name  Format  Interval  SCF  Source\n")
            f.write(f"{'GAGE1':<16}INTENSITY    {rain['interval']}     1.0      "
                    f"TIMESERIES {rain['series']}\n")

        f.write("\n[EVAPORATION]\nCONSTANT     3.0\nDRY_ONLY     NO\n")

        if subs:
            f.write("\n;;Name  RainGage  Outlet  Area  %Imperv  Width  %Slope  CurbLen\n")
            f.write("[SUBCATCHMENTS]\n")
            for s in subs:
                if s["uid"] not in modelled:
                    continue
                f.write(f"S_{s['uid']:<26}{'GAGE1':<12}{s['uid']:<28}"
                        f"{s['area_ha']:<10.4f}{s['imperv_pct']:<9.1f}"
                        f"{s['width_m']:<10.2f}{s['slope_pct']:<8.2f}0\n")
            f.write("\n;;Name  N-Imperv  N-Perv  S-Imperv  S-Perv  %Zero  RouteTo\n")
            f.write("[SUBAREAS]\n")
            for s in subs:
                if s["uid"] not in modelled:
                    continue
                f.write(f"S_{s['uid']:<26}0.013     0.15    1.8       5.0     25     OUTLET\n")
            f.write("\n;;Name  MaxRate  MinRate  Decay  DryTime  MaxInfil\n[INFILTRATION]\n")
            for s in subs:
                if s["uid"] not in modelled:
                    continue
                f.write(f"S_{s['uid']:<26}75.0      3.0     4.0    7        0\n")

        f.write("\n;;Name  InvertElev  MaxDepth  InitDepth  SurDepth  Aponded\n[JUNCTIONS]\n")
        for uid, inv, d in junctions:
            f.write(f"{uid:<28}{inv:<12.3f}{d:<10.3f}0          0         0\n")

        f.write("\n;;Name  InvertElev  Type  StageData  Gated  RouteTo\n[OUTFALLS]\n")
        for uid, inv, _ in outfalls:
            f.write(f"{uid:<28}{inv:<12.3f}FREE                    NO\n")

        f.write("\n;;Name  FromNode  ToNode  Length  Roughness  InOffset  OutOffset\n")
        f.write("[CONDUITS]\n")
        links = []
        for c in conduits:
            if c["from_uid"] not in modelled or c["to_uid"] not in modelled:
                continue
            if c.get("us_invert") is None or c.get("ds_invert") is None:
                continue
            cid = f"{c['from_uid']}__{c['to_uid']}"
            links.append((cid, c))
            f.write(f"{cid:<58}{c['from_uid']:<28}{c['to_uid']:<28}"
                    f"{c['length_m']:<10.2f}0.013     0          0\n")

        f.write("\n;;Link  Shape  Geom1  Geom2  Geom3  Geom4  Barrels\n[XSECTIONS]\n")
        for cid, c in links:
            w = c.get("width_m") or 0.6
            d = c.get("depth_m") or 0.6
            shape = "RECT_CLOSED" if (c.get("type") or "C") == "C" else "RECT_OPEN"
            f.write(f"{cid:<58}{shape:<14}{d:<8.3f}{w:<8.3f}0        0        1\n")

        if rain:
            f.write(f"\n[TIMESERIES]\n;;Name  Date  Time  Value\n")
            for line in rain["rows"]:
                f.write(line + "\n")

        f.write("\n[REPORT]\nINPUT      NO\nCONTROLS   NO\nSUBCATCHMENTS ALL\nNODES ALL\nLINKS ALL\n")

        f.write("\n[COORDINATES]\n;;Node  X  Y\n")
        for uid in sorted(modelled):
            n = by_uid.get(uid)
            if n:
                f.write(f"{uid:<28}{n['east']:<14.3f}{n['north']:.3f}\n")
    return len(junctions), len(outfalls), len(links)


def main():
    nodes = json.load(open(f"{NET}/nodes.json"))
    conduits = json.load(open(f"{NET}/conduits.json"))
    subs = json.load(open(f"{NET}/subcatchments.json"))
    runs = json.load(open(f"{NET}/runs.json"))
    for n in nodes:
        n["uid"] = f"W{n['ward']}_{n['node_id']}"
        n["width_m"], n["depth_m"] = n.get("width"), n.get("depth")
        n["type"] = n.get("drain_type")
        n["swmm_fl_hr"] = n.get("swmm_flood_hr")
        n["swmm_fl_Ml"] = n.get("swmm_flood_Ml")

    os.makedirs(SHP, exist_ok=True)
    if os.path.exists(GPKG):
        os.remove(GPKG)
    gp = ogr.GetDriverByName("GPKG").CreateDataSource(GPKG)

    def pt(r):
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint(r["east"], r["north"])
        return g

    def ln(r):
        g = ogr.Geometry(ogr.wkbLineString)
        for x, y in r["geometry"]:
            g.AddPoint(x, y)
        return g

    def poly(r):
        return ogr.CreateGeometryFromWkt(r["wkt"])

    print("swd_nodes       ", write("swd_nodes", ogr.wkbPoint, NODE_FIELDS, nodes, pt, gp, SHP))
    print("swd_conduits    ", write("swd_conduits", ogr.wkbLineString, COND_FIELDS,
                                    conduits, ln, gp, SHP))
    print("swd_subcatchments", write("swd_subcatchments", ogr.wkbPolygon, SUB_FIELDS,
                                     subs, poly, gp, SHP))
    run_rows = [{"run_id": i,
                 "length_m": round(sum(math.dist(r[k], r[k + 1]) for k in range(len(r) - 1)), 2),
                 "geometry": r} for i, r in enumerate(runs)]
    print("swd_drains      ", write("swd_drains", ogr.wkbLineString, RUN_FIELDS,
                                    run_rows, ln, gp, SHP))
    gp = None

    # map_text is produced by run_merge_text.py; re-adding it here keeps the
    # GeoPackage complete after this script recreates it.
    os.system(f"{sys.executable} {os.path.join(ROOT,'run_merge_text.py')} >/dev/null 2>&1")

    rain = None
    rp = f"{RAIN}/swmm_series.json"
    if os.path.exists(rp):
        rain = json.load(open(rp))
        print(f"rainfall series : {rain['series']} {rain['start']}..{rain['end']} "
              f"({len(rain['rows'])} rows)")
    nj, nof, nl = swmm(f"{OUT}/swd_model.inp", nodes, conduits, subs, rain)
    print(f"SWMM            : {nj} junctions, {nof} outfalls, {nl} conduits, "
          f"{sum(1 for s in subs)} subcatchments defined")


if __name__ == "__main__":
    main()
