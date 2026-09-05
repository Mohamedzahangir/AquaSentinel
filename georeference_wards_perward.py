#!/usr/bin/env python3
"""
Per-ward rotation: for each /Rotate 270 PDF, apply the rotation that empirically
gives the best alignment.

Best rotation determined by: (a) maximum CarriageWay containment in the GeoJSON
ward polygon, with (b) Ward 28 override to rot=0 (CANAL→Periyathoppu Lake check).
"""
import os, re, math, json, traceback
from collections import defaultdict
import fitz
from osgeo import gdal, ogr, osr
from pyproj import Transformer

PDFS_DIR = "/Users/admin/Desktop/projects/swd/GeoPDF"
OUT_DIR  = "/Users/admin/Desktop/projects/swd/georeferenced"
WARD_GEOJSON = "/Users/admin/Desktop/projects/cumta/repos/GCC_Flood_Inter_dept/data/GCCWardBoundary_2.js"

PT2PX = 150/72.0; M = 0.84676; GRID_PT = 283.5; TOL = 2.0; UTM_EPSG = 32644

# Per-ward best rotation (empirically determined). Default: 90.
WARD_ROTATION = {
    28:  0,    # CANAL → Periyathoppu Lake match
    37:  180,
    68:  0,    # all rotations give 100% containment; default to native (lake-like reasoning)
    110: 270,
    171: 90,
    175: 270,
    176: 90,
}
DEFAULT_ROTATION = 90  # for /Rotate 270 PDFs not in the override table

def load_wards():
    txt = open(WARD_GEOJSON).read()
    data = json.loads(txt[txt.index("{"):].rstrip(";\n "))
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{UTM_EPSG}", always_xy=True)
    out = {}
    for feat in data["features"]:
        m = re.match(r"Ward\s+(\d+)$", feat["properties"].get("Name",""), re.I)
        if not m: continue
        n = int(m.group(1))
        pts = []
        for poly in feat["geometry"]["coordinates"]:
            for ring in poly: pts.extend(ring)
        if not pts: continue
        xs, ys = zip(*[tf.transform(x, y) for x, y in pts])
        out[n] = (sum(xs)/len(xs), sum(ys)/len(ys))
    return out

def detect_grid(pdf):
    doc = fitz.open(pdf); page = doc[0]; ph = page.rect.height; pw = page.rect.width
    Vs, Hs = defaultdict(float), defaultdict(float)
    for dr in page.get_drawings():
        for item in dr.get("items", []):
            if item[0] != "l": continue
            p1, p2 = item[1], item[2]
            L = math.hypot(p2.x-p1.x, p2.y-p1.y)
            if L < 200: continue
            if abs(p2.x-p1.x)<1: Vs[round(p1.x,2)] = max(Vs[round(p1.x,2)], L)
            elif abs(p2.y-p1.y)<1: Hs[round(p1.y,2)] = max(Hs[round(p1.y,2)], L)
    def chain(d):
        ps = sorted(d.keys()); best = []
        for i in range(len(ps)):
            c = [ps[i]]; last = ps[i]
            while True:
                exp = last + GRID_PT
                close = [(abs(p-exp),p) for p in ps if abs(p-exp)<TOL]
                if not close: break
                close.sort(); c.append(close[0][1]); last = close[0][1]
            if len(c) > len(best): best = c
        return best
    doc.close()
    return chain(Vs), chain(Hs), pw, ph

def n2d(x, y, W, H, rot):
    if rot == 0: return x, y
    if rot == 90: return H-1-y, x
    if rot == 180: return W-1-x, H-1-y
    if rot == 270: return y, W-1-x

def pdf_bbox_px(pdf):
    ds = ogr.Open(pdf)
    if ds is None: return None
    minx=miny=float("inf"); maxx=maxy=float("-inf")
    for i in range(ds.GetLayerCount()):
        ex = ds.GetLayerByIndex(i).GetExtent()
        if ex[0]!=ex[1] or ex[2]!=ex[3]:
            minx=min(minx,ex[0]); maxx=max(maxx,ex[1]); miny=min(miny,ex[2]); maxy=max(maxy,ex[3])
    ds = None
    return (minx,miny,maxx,maxy) if math.isfinite(minx) else None

def compute_offsets(Vs_pt, Hs_pt, pw, ph, vcen_n, ward_cen, rot):
    W_n = pw*PT2PX; H_n = ph*PT2PX
    nat_V_px = [v*PT2PX for v in Vs_pt]
    nat_H_px = sorted([(ph - h)*PT2PX for h in Hs_pt])
    cx_d, cy_d = n2d(vcen_n[0], vcen_n[1], W_n, H_n, rot)
    if rot in (0, 180):
        V_E = sorted([n2d(x, 0, W_n, H_n, rot)[0] for x in nat_V_px])
        H_N = sorted([n2d(0, y, W_n, H_n, rot)[1] for y in nat_H_px])
    else:
        V_E = sorted([n2d(0, y, W_n, H_n, rot)[0] for y in nat_H_px])
        H_N = sorted([n2d(x, 0, W_n, H_n, rot)[1] for x in nat_V_px])
    vxA = V_E[len(V_E)//2]; vyA = H_N[len(H_N)//2]
    CE, CN = ward_cen
    est_E = CE + M*(vxA - cx_d); est_N = CN + M*(vyA - cy_d)
    sE = round(est_E/500)*500; sN = round(est_N/500)*500
    E0 = sE - M*vxA; N0 = sN - M*vyA
    return E0, N0, abs(est_E - sE), abs(est_N - sN)

def transform_geom(geom, E0, N0, W_n, H_n, rot):
    name = geom.GetGeometryName()
    if name == "POINT":
        xn, yn = geom.GetX(), geom.GetY()
        xd, yd = n2d(xn, yn, W_n, H_n, rot)
        geom.SetPoint(0, E0 + M*xd, N0 + M*yd)
        return
    if geom.GetGeometryCount() > 0:
        for i in range(geom.GetGeometryCount()):
            transform_geom(geom.GetGeometryRef(i), E0, N0, W_n, H_n, rot)
    else:
        for i in range(geom.GetPointCount()):
            p = geom.GetPoint(i)
            xd, yd = n2d(p[0], p[1], W_n, H_n, rot)
            geom.SetPoint(i, E0 + M*xd, N0 + M*yd)

def write_shapefiles(pdf, out_dir, E0, N0, W_n, H_n, rot):
    src = ogr.Open(pdf)
    srs = osr.SpatialReference(); srs.ImportFromEPSG(UTM_EPSG)
    drv = ogr.GetDriverByName("ESRI Shapefile")
    wkb_map = {"POINT":ogr.wkbPoint,"MULTIPOINT":ogr.wkbMultiPoint,
               "LINESTRING":ogr.wkbLineString,"MULTILINESTRING":ogr.wkbMultiLineString,
               "POLYGON":ogr.wkbPolygon,"MULTIPOLYGON":ogr.wkbMultiPolygon}
    out = []
    for li in range(src.GetLayerCount()):
        in_lyr = src.GetLayerByIndex(li); name = in_lyr.GetName()
        buckets = defaultdict(list)
        in_lyr.ResetReading()
        for f in in_lyr:
            g = f.GetGeometryRef()
            if g is None: continue
            buckets[g.GetGeometryName()].append((f.Clone(), g.Clone()))
        for gt, items in buckets.items():
            suffix = "" if len(buckets)==1 else f"_{gt.lower()}"
            base = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") + suffix
            shp = os.path.join(out_dir, base + ".shp")
            for ext in (".shp",".shx",".dbf",".prj",".cpg"):
                p = shp[:-4]+ext
                if os.path.exists(p): os.remove(p)
            out_ds = drv.CreateDataSource(shp)
            out_lyr = out_ds.CreateLayer(base, srs, wkb_map.get(gt, ogr.wkbUnknown))
            src_defn = in_lyr.GetLayerDefn()
            for fi in range(src_defn.GetFieldCount()):
                out_lyr.CreateField(src_defn.GetFieldDefn(fi))
            out_defn = out_lyr.GetLayerDefn()
            for sf, g in items:
                transform_geom(g, E0, N0, W_n, H_n, rot)
                nf = ogr.Feature(out_defn)
                for fi in range(src_defn.GetFieldCount()):
                    nf.SetField(fi, sf.GetField(fi))
                nf.SetGeometry(g)
                out_lyr.CreateFeature(nf); nf = None
            out_ds = None
            out.append((base, len(items)))
    src = None
    return out

def write_geotiff(pdf, out_tif, E0, N0, W_n, H_n, rot):
    src = gdal.Open(pdf)
    gdal.Translate(out_tif, src,
        options=gdal.TranslateOptions(format="GTiff",
            creationOptions=["COMPRESS=LZW","TILED=YES","BIGTIFF=IF_SAFER"]))
    src = None
    ds = gdal.Open(out_tif, gdal.GA_Update)
    if rot == 0:
        gt = (E0, M, 0, N0 + M*(H_n-1), 0, -M)
    elif rot == 90:
        gt = (E0, 0, M, N0, M, 0)
    elif rot == 180:
        gt = (E0 + M*(W_n-1), -M, 0, N0, 0, M)
    elif rot == 270:
        gt = (E0 + M*(H_n-1), 0, -M, N0 + M*(W_n-1), -M, 0)
    ds.SetGeoTransform(gt)
    srs = osr.SpatialReference(); srs.ImportFromEPSG(UTM_EPSG)
    ds.SetProjection(srs.ExportToWkt())
    ds = None

def process(pdf, ward_info):
    fn = os.path.basename(pdf); n = None
    m = re.search(r"(\d+)", fn)
    if m: n = int(m.group(1))
    if n is None or n not in ward_info:
        return ("skip", fn, n, "no_ward")
    try:
        rot = WARD_ROTATION.get(n, DEFAULT_ROTATION)
        Vs, Hs, pw, ph = detect_grid(pdf)
        if len(Vs) < 2 or len(Hs) < 2:
            return ("skip", fn, n, "no_grid")
        bb = pdf_bbox_px(pdf)
        if bb is None: return ("skip", fn, n, "no_vec")
        W_n = pw*PT2PX; H_n = ph*PT2PX
        vcen_n = ((bb[0]+bb[2])/2, (bb[1]+bb[3])/2)
        E0, N0, eE, eN = compute_offsets(Vs, Hs, pw, ph, vcen_n, ward_info[n], rot)
        out_dir = os.path.join(OUT_DIR, f"ward_{n:03d}")
        os.makedirs(out_dir, exist_ok=True)
        out_tif = os.path.join(out_dir, f"ward_{n:03d}.tif")
        write_geotiff(pdf, out_tif, E0, N0, W_n, H_n, rot)
        write_shapefiles(pdf, out_dir, E0, N0, W_n, H_n, rot)
        return ("ok", fn, n, {"rot": rot, "E0": E0, "N0": N0, "eE": eE, "eN": eN})
    except Exception as e:
        return ("err", fn, n, f"{e}\n{traceback.format_exc()}")

def main():
    ward_info = load_wards()
    targets = []
    for fn in sorted(os.listdir(PDFS_DIR)):
        if not fn.lower().endswith(".pdf"): continue
        doc = fitz.open(os.path.join(PDFS_DIR, fn))
        if doc[0].rotation != 0: targets.append(fn)
        doc.close()
    print(f"Reprocessing {len(targets)} /Rotate-flagged PDFs with per-ward rotation:")
    for i, fn in enumerate(targets, 1):
        status, fn_, n, info = process(os.path.join(PDFS_DIR, fn), ward_info)
        if status == "ok":
            extra = f"rot={info['rot']:>3d} E0={info['E0']:.1f} N0={info['N0']:.1f} eE={info['eE']:.1f} eN={info['eN']:.1f}"
        else: extra = str(info)[:160]
        print(f"  [{i:2d}/{len(targets)}] {status:4s} {fn[:30]:30s} ward {n:>4d}  {extra}", flush=True)

if __name__ == "__main__":
    main()
