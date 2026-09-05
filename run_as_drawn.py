#!/usr/bin/env python3
"""
Export the SWD network exactly as the PDF sheets draw it — nothing stitched,
nothing inferred, nothing joined across ward boundaries.

The main pipeline deliberately adds connections so the network can be routed
hydraulically: straight chords between manholes that are consecutive in the
schedule, links where two drawn runs touch, and outfalls promoted onto the
lowest node of a component that had none. Those are assumptions, and about a
quarter of the straight chords sit more than 20 m from any drawn drain — they
cut across blocks rather than following the road.

This export strips all of that back out. What is left is what a surveyor drew
and typed on the sheet, in the formats people actually open.

    python run_as_drawn.py
"""
import csv
import glob
import json
import os
import shutil
import subprocess
import sys
import zipfile

from osgeo import ogr, osr

ogr.UseExceptions()
osr.UseExceptions()

ROOT = os.path.dirname(os.path.abspath(__file__))
NET = f"{ROOT}/derived/network"
WARDS = f"{ROOT}/derived/wards"
OUT = f"{ROOT}/as_drawn"

# map_context roles worth carrying. river/sea are hatch fills — hundreds of
# thousands of short strokes that shade a water body, not a centreline — so they
# are left in the main chennai_swd.gpkg rather than bloating every format here.
CONTEXT_ROLES = {"road", "ward_boundary", "zone_boundary", "railway",
                 "flyover", "divider", "canal", "bridge", "pump"}

# Shapefile truncates field names at 10 characters, so they are shortened here
# deliberately rather than letting GDAL mangle them. DATA_DICTIONARY.md carries
# the long name and the provenance of every one.
NODE_FIELDS = [
    ("uid",        "uid",        ogr.OFTString),
    ("node_id",    "node_id",    ogr.OFTString),
    ("ward",       "ward",       ogr.OFTInteger),
    ("si",         "si",         ogr.OFTInteger),
    ("drain_top",  "drain_top",  ogr.OFTReal),
    ("invert",     "invert",     ogr.OFTReal),
    ("road_edge",  "road_edge",  ogr.OFTReal),
    ("width",      "width",      ogr.OFTReal),
    ("depth",      "depth",      ogr.OFTReal),
    ("drain_type", "drain_type", ogr.OFTString),
    ("is_outlet",  "is_outlet",  ogr.OFTInteger),
    ("locator",    "locator",    ogr.OFTString),
    ("conf",       "conf",       ogr.OFTReal),
    ("snap_m",     "snap_m",     ogr.OFTReal),
    ("raw_label",  "raw_label",  ogr.OFTString),
    ("flags",      "flags",      ogr.OFTString),
    ("microws",    "microws",    ogr.OFTString),
    ("subbasin",   "subbasin",   ogr.OFTString),
    ("flood_m",    "flood_m",    ogr.OFTReal),
    ("flood_road", "flood_road", ogr.OFTString),
]
COND_FIELDS = [
    ("cond_id",   "cond_id",   ogr.OFTString),
    ("from_uid",  "from_uid",  ogr.OFTString),
    ("to_uid",    "to_uid",    ogr.OFTString),
    ("from_ward", "from_ward", ogr.OFTInteger),
    ("length_m",  "length_m",  ogr.OFTReal),
    ("us_invert", "us_invert", ogr.OFTReal),
    ("ds_invert", "ds_invert", ogr.OFTReal),
    ("width_m",   "width_m",   ogr.OFTReal),
    ("depth_m",   "depth_m",   ogr.OFTReal),
    ("type",      "type",      ogr.OFTString),
    ("slope",     "slope",     ogr.OFTReal),
    ("dir_src",   "dir_src",   ogr.OFTString),
    ("flags",     "flags",     ogr.OFTString),
]


def srs():
    s = osr.SpatialReference()
    s.ImportFromEPSG(32644)
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


def set_fields(feat, spec, rec):
    for short, long, _ in spec:
        v = rec.get(long)
        if v is None or v == "":
            continue
        feat.SetField(short, int(v) if isinstance(v, bool) else v)


# --------------------------------------------------------------------------
def load_nodes():
    """Manholes with the pipeline's own assumptions reverted."""
    nodes = json.load(open(f"{NET}/nodes.json"))
    out = []
    reverted = 0
    for n in nodes:
        n = dict(n)
        flags = n.get("flags") or ""
        if "synthetic_outfall" in flags:
            # run_connect promoted this node to an outfall; the sheet did not
            n["is_outlet"] = False
            n["flags"] = ";".join(f for f in flags.split(";")
                                  if f and f != "synthetic_outfall")
            reverted += 1
        for k in ("swmm_flood_hr", "swmm_flood_Ml", "agree"):
            n.pop(k, None)
        n["is_outlet"] = 1 if n.get("is_outlet") else 0
        out.append(n)
    print(f"  nodes: {len(out)}  ({reverted} promoted outfalls reverted to "
          f"{sum(x['is_outlet'] for x in out)} marked (OL) on the sheets)")
    return out


def load_conduits():
    """Only conduits that follow linework actually drawn on one sheet."""
    cond = json.load(open(f"{NET}/conduits.json"))
    keep, drop = [], {"inferred": 0, "junction": 0, "cross_ward": 0}
    for c in cond:
        f = c.get("flags") or ""
        if "geometry_inferred" in f:
            drop["inferred"] += 1
            continue
        if "junction_link" in f:
            drop["junction"] += 1
            continue
        if c.get("cross_ward"):
            drop["cross_ward"] += 1
            continue
        c = dict(c)
        c["from_ward"] = c.get("from_ward")
        keep.append(c)
    print(f"  conduits: {len(keep)} kept; dropped "
          f"{drop['inferred']} straight-line schedule links, "
          f"{drop['junction']} junction links, {drop['cross_ward']} cross-ward")
    return keep


def load_ward_shapes(layer_name):
    """Concatenate a per-sheet shapefile across all 107 wards."""
    feats = []
    for d in sorted(glob.glob(f"{WARDS}/ward_*")):
        p = f"{d}/{layer_name}.shp"
        if not os.path.exists(p):
            continue
        ds = ogr.Open(p)
        lyr = ds.GetLayer(0)
        for f in lyr:
            g = f.GetGeometryRef()
            if g is None:
                continue
            feats.append({
                "ward": f.GetField("ward"),
                "role": f.GetField("role"),
                "cad_layer": f.GetField("cad_layer"),
                "wkb": g.ExportToWkb(),
            })
        ds = None
    return feats


def load_json_per_ward(name):
    rows = []
    for d in sorted(glob.glob(f"{WARDS}/ward_*")):
        p = f"{d}/{name}"
        if os.path.exists(p):
            rows.extend(json.load(open(p)))
    return rows


# --------------------------------------------------------------------------
def build_gpkg(path, nodes, conduits, drains, context, text):
    if os.path.exists(path):
        os.remove(path)
    drv = ogr.GetDriverByName("GPKG")
    ds = drv.CreateDataSource(path)
    s = srs()

    lyr = ds.CreateLayer("swd_nodes_as_drawn", s, ogr.wkbPoint25D)
    for short, _, typ in NODE_FIELDS:
        lyr.CreateField(ogr.FieldDefn(short, typ))
    for n in nodes:
        f = ogr.Feature(lyr.GetLayerDefn())
        set_fields(f, NODE_FIELDS, n)
        g = ogr.Geometry(ogr.wkbPoint25D)
        g.AddPoint(n["east"], n["north"], n.get("invert") or 0.0)
        f.SetGeometry(g)
        lyr.CreateFeature(f)

    lyr = ds.CreateLayer("swd_conduits_as_drawn", s, ogr.wkbLineString25D)
    for short, _, typ in COND_FIELDS:
        lyr.CreateField(ogr.FieldDefn(short, typ))
    for c in conduits:
        f = ogr.Feature(lyr.GetLayerDefn())
        set_fields(f, COND_FIELDS, c)
        g = ogr.Geometry(ogr.wkbLineString25D)
        geom = c["geometry"]
        ui, di = c.get("us_invert"), c.get("ds_invert")
        for i, (x, y) in enumerate(geom):
            t = i / max(1, len(geom) - 1)
            z = (ui + (di - ui) * t) if (ui is not None and di is not None) else 0.0
            g.AddPoint(x, y, z)
        f.SetGeometry(g)
        lyr.CreateFeature(f)

    # Drains stay as the individual fragments the sheet draws, so you can see
    # exactly what was traced. Context is CAD stroke soup — 387k separate paths
    # for roads and boundaries — so it is grouped into one multi-geometry per
    # ward/role/layer. Identical geometry, a fraction of the attribute overhead.
    lyr = ds.CreateLayer("swd_drains_as_drawn", s, ogr.wkbUnknown)
    for fld, w in (("ward", 4), ("role", 24), ("cad_layer", 64)):
        fd = ogr.FieldDefn(fld, ogr.OFTString)
        fd.SetWidth(w)
        lyr.CreateField(fd)
    for r in drains:
        f = ogr.Feature(lyr.GetLayerDefn())
        for fld in ("ward", "role", "cad_layer"):
            if r.get(fld) is not None:
                f.SetField(fld, str(r[fld])[:64])
        f.SetGeometry(ogr.CreateGeometryFromWkb(r["wkb"]))
        lyr.CreateFeature(f)

    lyr = ds.CreateLayer("map_context_as_drawn", s, ogr.wkbMultiLineString)
    for fld, w in (("ward", 4), ("role", 24), ("cad_layer", 64), ("n_paths", 0)):
        fd = ogr.FieldDefn(fld, ogr.OFTInteger if fld == "n_paths" else ogr.OFTString)
        if w:
            fd.SetWidth(w)
        lyr.CreateField(fd)
    groups = {}
    for r in context:
        key = (str(r.get("ward")), str(r.get("role")), str(r.get("cad_layer"))[:64])
        groups.setdefault(key, []).append(r["wkb"])
    for (ward, role, cad), wkbs in sorted(groups.items()):
        multi = ogr.Geometry(ogr.wkbMultiLineString)
        for w in wkbs:
            g = ogr.CreateGeometryFromWkb(w)
            if g.GetGeometryType() in (ogr.wkbMultiLineString, ogr.wkbMultiLineString25D):
                for i in range(g.GetGeometryCount()):
                    multi.AddGeometry(g.GetGeometryRef(i))
            else:
                multi.AddGeometry(g)
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetField("ward", ward)
        f.SetField("role", role)
        f.SetField("cad_layer", cad)
        f.SetField("n_paths", len(wkbs))
        f.SetGeometry(multi)
        lyr.CreateFeature(f)
    print(f"  context grouped into {len(groups)} multi-line features")

    lyr = ds.CreateLayer("map_text_as_drawn", s, ogr.wkbPoint)
    for fld, typ in (("ward", ogr.OFTInteger), ("role", ogr.OFTString),
                     ("text", ogr.OFTString), ("conf", ogr.OFTReal),
                     ("size_pt", ogr.OFTReal)):
        lyr.CreateField(ogr.FieldDefn(fld, typ))
    for r in text:
        f = ogr.Feature(lyr.GetLayerDefn())
        for fld in ("ward", "role", "text", "conf", "size_pt"):
            if r.get(fld) is not None:
                f.SetField(fld, r[fld])
        g = ogr.Geometry(ogr.wkbPoint)
        g.AddPoint_2D(float(r["east"]), float(r["north"]))
        f.SetGeometry(g)
        lyr.CreateFeature(f)
    ds = None


def translate(gpkg, layer, dst, fmt, extra=None):
    """ogr2ogr one layer out of the GeoPackage into another format."""
    if os.path.exists(dst):
        os.remove(dst) if os.path.isfile(dst) else shutil.rmtree(dst)
    cmd = ["ogr2ogr", "-f", fmt, dst, gpkg, layer]
    if fmt in ("GeoJSON", "KML"):
        cmd += ["-t_srs", "EPSG:4326"]
    cmd += extra or []
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        print(f"    ! {layer} -> {fmt}: {r.stderr.strip()[:160]}")
        return False
    return True


def main():
    print("reading the per-sheet extraction ...")
    os.makedirs(OUT, exist_ok=True)
    nodes = load_nodes()
    conduits = load_conduits()
    drains = load_ward_shapes("swd_drains")
    print(f"  drains: {len(drains)} drawn centreline fragments")
    context = [r for r in load_ward_shapes("map_context")
               if r["role"] in CONTEXT_ROLES]
    print(f"  context: {len(context)} features "
          f"({', '.join(sorted(CONTEXT_ROLES))}; river/sea hatch excluded)")
    text = load_json_per_ward("map_text.json")
    print(f"  map text: {len(text)} labels")
    schedule = load_json_per_ward("schedule.json")
    print(f"  schedule: {len(schedule)} LIST OF POINTS rows")

    gpkg = f"{OUT}/gpkg/chennai_swd_as_drawn.gpkg"
    os.makedirs(f"{OUT}/gpkg", exist_ok=True)
    print("\nwriting GeoPackage ...")
    build_gpkg(gpkg, nodes, conduits, drains, context, text)
    print(f"  {gpkg}  ({os.path.getsize(gpkg)/1e6:.1f} MB)")

    # the SWD network itself goes to every format; the CAD context basemap is
    # bulky and rarely wanted outside GIS, so it stays in GeoPackage and Shapefile
    swd = ["swd_nodes_as_drawn", "swd_conduits_as_drawn",
           "swd_drains_as_drawn", "map_text_as_drawn"]
    layers = swd + ["map_context_as_drawn"]

    print("\nwriting Shapefiles (EPSG:32644) ...")
    os.makedirs(f"{OUT}/shp", exist_ok=True)
    for lname in layers:
        translate(gpkg, lname, f"{OUT}/shp/{lname}.shp", "ESRI Shapefile")
    print("\nwriting GeoJSON (EPSG:4326) ...")
    os.makedirs(f"{OUT}/geojson", exist_ok=True)
    for lname in swd:
        translate(gpkg, lname, f"{OUT}/geojson/{lname}.geojson", "GeoJSON",
                  ["-lco", "RFC7946=YES", "-lco", "COORDINATE_PRECISION=7"])
    print("\nwriting KML (EPSG:4326, for Google Earth) ...")
    os.makedirs(f"{OUT}/kml", exist_ok=True)
    for lname in swd:
        translate(gpkg, lname, f"{OUT}/kml/{lname}.kml", "KML")

    print("\nwriting CSV tables ...")
    os.makedirs(f"{OUT}/csv", exist_ok=True)
    with open(f"{OUT}/csv/swd_nodes_as_drawn.csv", "w", newline="") as fh:
        cols = ["uid", "node_id", "ward", "si", "east", "north",
                *[l for _, l, _ in NODE_FIELDS if l not in ("uid", "node_id", "ward", "si")]]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(nodes)
    with open(f"{OUT}/csv/swd_conduits_as_drawn.csv", "w", newline="") as fh:
        cols = [l for _, l, _ in COND_FIELDS]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(conduits)
    if schedule:
        keys = sorted({k for r in schedule for k in r})
        with open(f"{OUT}/csv/schedule_list_of_points.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(schedule)
    with open(f"{OUT}/csv/map_text_as_drawn.csv", "w", newline="") as fh:
        keys = sorted({k for r in text for k in r})
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(text)

    print("\nzipping shapefiles ...")
    zpath = f"{OUT}/shp/chennai_swd_as_drawn_shapefiles.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(glob.glob(f"{OUT}/shp/*")):
            if not p.endswith(".zip"):
                z.write(p, os.path.basename(p))
    print(f"  {zpath}  ({os.path.getsize(zpath)/1e6:.1f} MB)")

    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(OUT) for f in fs)
    print(f"\nas_drawn/ total {total/1e6:.1f} MB")


if __name__ == "__main__":
    main()
