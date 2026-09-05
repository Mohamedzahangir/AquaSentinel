#!/usr/bin/env python3
"""
Batch-georeference GCC ward storm-water-drain (SWD) PDF maps.

Each PDF is an AutoCAD Map 3D plot in UTM zone 44N at 1:5000 with a 500 m grid.
We detect the regular 283.5 pt grid in the PDF, use the published ward centroid
from GCCWardBoundary GeoJSON to snap the grid to its true 500 m UTM multiples,
then write:
  - GeoTIFF rasters
  - ESRI shapefiles for each vector layer in the source PDF
all in EPSG:32644.
"""

import os, re, math, json, sys, shutil, traceback
from collections import defaultdict
import fitz
from osgeo import gdal, ogr, osr
from pyproj import Transformer

# ---- Paths ----
PDFS_DIR = "/Users/admin/Desktop/projects/swd/GeoPDF"
OUT_DIR  = "/Users/admin/Desktop/projects/swd/georeferenced"
WARD_GEOJSON = "/Users/admin/Desktop/projects/cumta/repos/GCC_Flood_Inter_dept/data/GCCWardBoundary_2.js"

os.makedirs(OUT_DIR, exist_ok=True)

# ---- Constants ----
DPI = 150
PT2PX = DPI / 72.0       # 2.0833
M_PER_PX = 0.84676       # 1:5000 @ 150 dpi  (500 m / 283.5 pt / 2.0833 px/pt)
GRID_PT = 283.5
TOL = 2.0

UTM_EPSG = 32644

# ---- Load ward centroids in UTM ----
def load_wards():
    txt = open(WARD_GEOJSON).read()
    data = json.loads(txt[txt.index("{"):].rstrip(";\n "))
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{UTM_EPSG}", always_xy=True)
    out = {}
    for feat in data["features"]:
        m = re.match(r"Ward\s+(\d+)$", feat["properties"].get("Name",""), re.I)
        if not m: continue
        n = int(m.group(1))
        # MultiPolygon → centroid via vertex averaging (good enough for centroid snap)
        pts = []
        for poly in feat["geometry"]["coordinates"]:
            for ring in poly:
                pts.extend(ring)
        if not pts: continue
        # Convert all points then average
        xs, ys = zip(*[tf.transform(x, y) for x, y in pts])
        out[n] = (sum(xs)/len(xs), sum(ys)/len(ys))
    return out

# ---- PDF helpers ----
def detect_grid(path):
    """Return (Vs_pt, Hs_pt, page_height_pt) — lists of regular UTM grid line positions."""
    doc = fitz.open(path)
    try:
        page = doc[0]
        ph = page.rect.height
        Vs, Hs = defaultdict(float), defaultdict(float)
        for dr in page.get_drawings():
            for item in dr.get("items", []):
                if item[0] != "l": continue
                p1, p2 = item[1], item[2]
                dx, dy = p2.x - p1.x, p2.y - p1.y
                L = math.hypot(dx, dy)
                if L < 200: continue
                if abs(dx) < 1.0:
                    k = round(p1.x, 2); Vs[k] = max(Vs[k], L)
                elif abs(dy) < 1.0:
                    k = round(p1.y, 2); Hs[k] = max(Hs[k], L)
        def chain(d):
            ps = sorted(d.keys()); best = []
            for i in range(len(ps)):
                c = [ps[i]]; last = ps[i]
                while True:
                    exp = last + GRID_PT
                    close = [(abs(p-exp), p) for p in ps if abs(p-exp) < TOL]
                    if not close: break
                    close.sort(); c.append(close[0][1]); last = close[0][1]
                if len(c) > len(best): best = c
            return best
        return chain(Vs), chain(Hs), ph
    finally:
        doc.close()

def pdf_bbox_pixels(path):
    """Bounding box of all vector features across all PDF layers (OGR pixel space)."""
    ds = ogr.Open(path)
    if ds is None: return None
    minx = miny = float("inf"); maxx = maxy = float("-inf")
    for i in range(ds.GetLayerCount()):
        lyr = ds.GetLayerByIndex(i)
        ex = lyr.GetExtent()
        if ex[0] != ex[1] or ex[2] != ex[3]:
            minx = min(minx, ex[0]); maxx = max(maxx, ex[1])
            miny = min(miny, ex[2]); maxy = max(maxy, ex[3])
    ds = None
    if not math.isfinite(minx): return None
    return (minx, miny, maxx, maxy)

def compute_offsets(Vs_pt, Hs_pt, page_h, vcen_px, ward_cen_utm):
    """Returns (E0, N0, snap_err_E, snap_err_N) for transform UTM = (E0+M*x, N0+M*y)."""
    Vs_px = [v * PT2PX for v in Vs_pt]
    Hs_px = sorted([(page_h - h) * PT2PX for h in Hs_pt])
    vxA = Vs_px[len(Vs_px)//2]
    vyA = Hs_px[len(Hs_px)//2]
    CE, CN = ward_cen_utm
    vx_c, vy_c = vcen_px
    est_E = CE + M_PER_PX * (vxA - vx_c)
    est_N = CN + M_PER_PX * (vyA - vy_c)
    snap_E = round(est_E / 500) * 500
    snap_N = round(est_N / 500) * 500
    E0 = snap_E - M_PER_PX * vxA
    N0 = snap_N - M_PER_PX * vyA
    return E0, N0, abs(est_E - snap_E), abs(est_N - snap_N)

# ---- Vector transform ----
def transform_geom(geom, E0, N0):
    """Apply UTM = (E0 + M_PER_PX*x, N0 + M_PER_PX*y) in-place to an OGR geometry."""
    gt = geom.GetGeometryType()
    name = geom.GetGeometryName()
    if name == "POINT":
        x, y = geom.GetX(), geom.GetY()
        geom.SetPoint(0, E0 + M_PER_PX*x, N0 + M_PER_PX*y)
        return
    # Containers
    for i in range(geom.GetGeometryCount()):
        transform_geom(geom.GetGeometryRef(i), E0, N0)
        continue
    # For linestrings (and polygon rings), iterate points directly
    if geom.GetGeometryCount() == 0:
        for i in range(geom.GetPointCount()):
            p = geom.GetPoint(i)
            geom.SetPoint(i, E0 + M_PER_PX*p[0], N0 + M_PER_PX*p[1])

def write_shapefiles(pdf_path, out_dir, E0, N0):
    """Write one shapefile per (layer × geom type) found in PDF."""
    src_ds = ogr.Open(pdf_path)
    if src_ds is None: return []
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(UTM_EPSG)
    drv = ogr.GetDriverByName("ESRI Shapefile")
    written = []
    for li in range(src_ds.GetLayerCount()):
        in_lyr = src_ds.GetLayerByIndex(li)
        name = in_lyr.GetName()
        # Bucket features by geometry type
        buckets = defaultdict(list)
        in_lyr.ResetReading()
        for feat in in_lyr:
            g = feat.GetGeometryRef()
            if g is None: continue
            buckets[g.GetGeometryName()].append((feat.Clone(), g.Clone()))
        for gt_name, items in buckets.items():
            suffix = "" if len(buckets) == 1 else f"_{gt_name.lower()}"
            shp_name = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") + suffix
            shp_path = os.path.join(out_dir, shp_name + ".shp")
            for ext in (".shp",".shx",".dbf",".prj",".cpg"):
                p = shp_path[:-4] + ext
                if os.path.exists(p): os.remove(p)
            wkb_type = ogr.wkbUnknown
            wkb_map = {
                "POINT": ogr.wkbPoint, "MULTIPOINT": ogr.wkbMultiPoint,
                "LINESTRING": ogr.wkbLineString, "MULTILINESTRING": ogr.wkbMultiLineString,
                "POLYGON": ogr.wkbPolygon, "MULTIPOLYGON": ogr.wkbMultiPolygon,
            }
            wkb_type = wkb_map.get(gt_name, ogr.wkbUnknown)
            out_ds = drv.CreateDataSource(shp_path)
            out_lyr = out_ds.CreateLayer(shp_name, srs, wkb_type)
            # Copy fields
            src_defn = in_lyr.GetLayerDefn()
            for fi in range(src_defn.GetFieldCount()):
                out_lyr.CreateField(src_defn.GetFieldDefn(fi))
            out_defn = out_lyr.GetLayerDefn()
            for src_feat, g in items:
                transform_geom(g, E0, N0)
                nf = ogr.Feature(out_defn)
                for fi in range(src_defn.GetFieldCount()):
                    nf.SetField(fi, src_feat.GetField(fi))
                nf.SetGeometry(g)
                out_lyr.CreateFeature(nf)
                nf = None
            out_ds = None
            written.append((shp_name, len(items), shp_path))
    src_ds = None
    return written

def write_geotiff(pdf_path, out_tif, E0, N0):
    """Render PDF to GeoTIFF at 150 DPI with proper geotransform & projection."""
    src = gdal.Open(pdf_path)
    if src is None: return False
    W, H = src.RasterXSize, src.RasterYSize
    # ulx=E0+0, uly = N0 + M*H (since vector y is bottom-up, top-of-image is high y)
    ulx = E0
    uly = N0 + M_PER_PX * H
    lrx = E0 + M_PER_PX * W
    lry = N0
    gdal.Translate(
        out_tif, src,
        options=gdal.TranslateOptions(
            format="GTiff",
            outputSRS=f"EPSG:{UTM_EPSG}",
            outputBounds=[ulx, uly, lrx, lry],
            creationOptions=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"]
        )
    )
    src = None
    return True

# ---- Driver ----
def wnum(fn):
    m = re.search(r"(\d+)", fn)
    return int(m.group(1)) if m else None

def process_pdf(pdf_path, ward_info, out_root, verbose=True):
    fn = os.path.basename(pdf_path)
    n = wnum(fn)
    if n is None or n not in ward_info:
        return ("skip", fn, n, "no_ward_in_geojson")
    try:
        Vs, Hs, ph = detect_grid(pdf_path)
        if len(Vs) < 2 or len(Hs) < 2:
            return ("skip", fn, n, f"grid_V{len(Vs)}_H{len(Hs)}")
        bb = pdf_bbox_pixels(pdf_path)
        if bb is None:
            return ("skip", fn, n, "no_vectors")
        vcen = ((bb[0]+bb[2])/2, (bb[1]+bb[3])/2)
        E0, N0, eE, eN = compute_offsets(Vs, Hs, ph, vcen, ward_info[n])
        out_dir = os.path.join(out_root, f"ward_{n:03d}")
        os.makedirs(out_dir, exist_ok=True)
        out_tif = os.path.join(out_dir, f"ward_{n:03d}.tif")
        write_geotiff(pdf_path, out_tif, E0, N0)
        shps = write_shapefiles(pdf_path, out_dir, E0, N0)
        return ("ok", fn, n, {"E0": E0, "N0": N0, "eE": eE, "eN": eN,
                              "shapefiles": [(s[0], s[1]) for s in shps],
                              "tif": out_tif, "out_dir": out_dir})
    except Exception as e:
        return ("err", fn, n, f"{e}\n{traceback.format_exc()}")

def main():
    if not os.path.isdir(PDFS_DIR):
        print("Missing", PDFS_DIR, file=sys.stderr); sys.exit(1)
    if not os.path.exists(WARD_GEOJSON):
        print("Missing", WARD_GEOJSON, file=sys.stderr); sys.exit(1)

    print("Loading ward centroids...", flush=True)
    ward_info = load_wards()
    print(f"  {len(ward_info)} wards loaded", flush=True)

    files = sorted(f for f in os.listdir(PDFS_DIR) if f.lower().endswith(".pdf"))
    print(f"Found {len(files)} PDFs", flush=True)

    summary = {"ok": [], "skip": [], "err": []}
    for i, fn in enumerate(files, 1):
        path = os.path.join(PDFS_DIR, fn)
        status, fn_, n, info = process_pdf(path, ward_info, OUT_DIR)
        summary[status].append({"file": fn, "ward": n, "info": info})
        tag = "OK " if status=="ok" else ("SKIP" if status=="skip" else "ERR ")
        if status == "ok":
            extra = f"E0={info['E0']:.1f} N0={info['N0']:.1f} eE={info['eE']:.1f}m eN={info['eN']:.1f}m shps={len(info['shapefiles'])}"
        else:
            extra = info if isinstance(info, str) else str(info)[:120]
        print(f"  [{i:3d}/{len(files)}] {tag} {fn[:35]:35s} ward {str(n):>4s}  {extra}", flush=True)

    print()
    print(f"DONE.  ok={len(summary['ok'])}  skip={len(summary['skip'])}  err={len(summary['err'])}")

    # Save summary
    with open(os.path.join(OUT_DIR, "_summary.json"), "w") as f:
        json.dump(summary, f, default=str, indent=2)

if __name__ == "__main__":
    main()
