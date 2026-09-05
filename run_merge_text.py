#!/usr/bin/env python3
"""Add the map's own text annotation to the city-wide outputs as a point layer."""
import glob
import json
import os

from osgeo import ogr, osr

ogr.UseExceptions()

ROOT = "/Users/admin/Desktop/projects/swd"
WARDS = f"{ROOT}/derived/wards"
GPKG = f"{ROOT}/derived/chennai_swd.gpkg"
SHP = f"{ROOT}/derived/shp/map_text.shp"

FIELDS = [("ward", ogr.OFTInteger), ("role", ogr.OFTString),
          ("text", ogr.OFTString), ("conf", ogr.OFTReal),
          ("size_pt", ogr.OFTReal)]


def srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(32644)
    return s


def make(ds, name):
    lyr = ds.CreateLayer(name, srs(), ogr.wkbPoint)
    for n, t in FIELDS:
        fd = ogr.FieldDefn(n, t)
        if t == ogr.OFTString:
            fd.SetWidth(120)
        elif t == ogr.OFTReal:
            fd.SetWidth(12)
            fd.SetPrecision(3)
        lyr.CreateField(fd)
    return lyr


def main():
    rows = []
    for p in sorted(glob.glob(f"{WARDS}/ward_*/map_text.json")):
        try:
            rows.extend(json.load(open(p)))
        except Exception:
            pass
    print(f"{len(rows)} labels from {len(glob.glob(f'{WARDS}/ward_*/map_text.json'))} wards")
    if not rows:
        return

    gp = ogr.Open(GPKG, 1)
    if gp.GetLayerByName("map_text"):
        gp.DeleteLayer("map_text")
    lg = make(gp, "map_text")

    drv = ogr.GetDriverByName("ESRI Shapefile")
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        f = os.path.splitext(SHP)[0] + ext
        if os.path.exists(f):
            os.remove(f)
    sd = drv.CreateDataSource(SHP)
    ls = make(sd, "map_text")

    for out in (lg, ls):
        out.StartTransaction()
    for r in rows:
        for out in (lg, ls):
            f = ogr.Feature(out.GetLayerDefn())
            f.SetField("ward", r["ward"])
            f.SetField("role", r["role"])
            f.SetField("text", r["text"][:120])
            f.SetField("conf", r["conf"])
            f.SetField("size_pt", r["size_pt"])
            g = ogr.Geometry(ogr.wkbPoint)
            g.AddPoint(r["east"], r["north"])
            f.SetGeometry(g)
            out.CreateFeature(f)
            f = None
    for out in (lg, ls):
        out.CommitTransaction()
    gp = None
    sd = None

    from collections import Counter
    c = Counter(r["role"] for r in rows)
    print("by role:", dict(c))
    print(f"written -> {GPKG} (map_text) and {SHP}")


if __name__ == "__main__":
    main()
