#!/usr/bin/env python3
"""
Re-georeference the 19 PDFs that declare /Rotate 270.

Native page coordinates need to be rotated 90° CCW before the standard UTM affine
is applied — otherwise the map ends up rotated on the ground.

Mapping native pixel (xn, yn)  [yn measured bottom-up]
        →  display pixel (xd, yd) = (yn, W_n_px - 1 - xn)

GeoTransform for the rotated raster:
    GT = (E0 + M*(H_n_px-1),  0, -M,
          N0 + M*(W_n_px-1), -M,  0)
"""

import os, re, math, json, sys, traceback
from collections import defaultdict
import fitz
from osgeo import gdal, ogr, osr
from pyproj import Transformer

PDFS_DIR = "/Users/admin/Desktop/projects/swd/GeoPDF"
OUT_DIR  = "/Users/admin/Desktop/projects/swd/georeferenced"
WARD_GEOJSON = "/Users/admin/Desktop/projects/cumta/repos/GCC_Flood_Inter_dept/data/GCCWardBoundary_2.js"

DPI = 150
PT2PX = DPI / 72.0
M_PER_PX = 0.84676
GRID_PT = 283.5
TOL = 2.0
UTM_EPSG = 32644

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
            for ring in poly:
                pts.extend(ring)
        if not pts: continue
        xs, ys = zip(*[tf.transform(x, y) for x, y in pts])
        out[n] = (sum(xs)/len(xs), sum(ys)/len(ys))
    return out

def detect_grid_native(pdf_path):
    """Return (Vs_pt, Hs_pt, page_w_pt, page_h_pt, rotation)."""
    doc = fitz.open(pdf_path)
    try:
        page = doc[0]
        ph = page.rect.height; pw = page.rect.width
        rot = page.rotation
        Vs, Hs = defaultdict(float), defaultdict(float)
        for dr in page.get_drawings():
            for item in dr.get("items", []):
                if item[0] != "l": continue
                p1, p2 = item[1], item[2]
                dx, dy = p2.x - p1.x, p2.y - p1.y
                L = math.hypot(dx, dy)
                if L < 200: continue
                if abs(dx) < 1.0: Vs[round(p1.x,2)] = max(Vs[round(p1.x,2)], L)
                elif abs(dy) < 1.0: Hs[round(p1.y,2)] = max(Hs[round(p1.y,2)], L)
        def chain(d):
            ps = sorted(d.keys()); best = []
            for i in range(len(ps)):
                c = [ps[i]]; last = ps[i]
                while True:
                    exp = last + GRID_PT
                    close = [(abs(p-exp), p) for p in ps if abs(p-exp)<TOL]
                    if not close: break
                    close.sort(); c.append(close[0][1]); last = close[0][1]
                if len(c) > len(best): best = c
            return best
        return chain(Vs), chain(Hs), pw, ph, rot
    finally:
        doc.close()

def pdf_bbox_native_px(pdf_path):
    """OGR bbox in native pixel space (x right, y up). Returns (minx, miny, maxx, maxy)."""
    ds = ogr.Open(pdf_path)
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

def native_to_display(xn, yn, W_n_px, H_n_px, rotation):
    """Apply rotation to native bottom-up pixel coords (origin bottom-left).
       Empirically calibrated against ward 28: the AutoCAD plotter writes
       /Rotate 270 but the content actually needs a 90° CW (not CCW) transform
       to land in the conventional north-up display orientation.
    """
    if rotation == 0:
        return xn, yn
    elif rotation == 90:
        return H_n_px - 1 - yn, xn
    elif rotation == 180:
        return W_n_px - 1 - xn, H_n_px - 1 - yn
    elif rotation == 270:
        return yn, W_n_px - 1 - xn
    raise ValueError(f"Unsupported rotation: {rotation}")

def transform_grid_to_display(Vs_pt, Hs_pt, page_w_pt, page_h_pt, rotation):
    """Convert grid lines from native PDF-pt to display-pixel space, returning
       (V_disp_px, H_disp_px) where V_disp are constant-X (Eastings) and H_disp are constant-Y (Northings)."""
    W_n_px = page_w_pt * PT2PX
    H_n_px = page_h_pt * PT2PX
    # Native vertical line (constant x_n_pt) → constant x_n_px line in native
    # Native horizontal line (constant y_n_top_pt) → constant y_n_bottom_px line in native
    nat_V_px = [v * PT2PX for v in Vs_pt]                         # constant x_native_px
    nat_H_px = sorted([(page_h_pt - h) * PT2PX for h in Hs_pt])   # constant y_native_px (bottom-up)
    if rotation == 0:
        return nat_V_px, nat_H_px, W_n_px, H_n_px
    elif rotation == 270:
        # (xd, yd) = (yn, W_n - 1 - xn). Native V line → constant xd → display Easting line.
        V_disp = sorted([yn for yn in nat_H_px])
        H_disp = sorted([W_n_px - 1 - xn for xn in nat_V_px])
        return V_disp, H_disp, H_n_px, W_n_px
    elif rotation == 180:
        V_disp = sorted([W_n_px - 1 - x for x in nat_V_px])
        H_disp = sorted([H_n_px - 1 - y for y in nat_H_px])
        return V_disp, H_disp, W_n_px, H_n_px
    elif rotation == 90:
        V_disp = sorted([H_n_px - 1 - yn for yn in nat_H_px])
        H_disp = sorted([xn for xn in nat_V_px])
        return V_disp, H_disp, H_n_px, W_n_px
    raise ValueError(rotation)

def compute_offsets(V_disp, H_disp, vcen_disp, ward_cen):
    vxA = V_disp[len(V_disp)//2]
    vyA = H_disp[len(H_disp)//2]
    CE, CN = ward_cen
    vx_c, vy_c = vcen_disp
    est_E = CE + M_PER_PX * (vxA - vx_c)
    est_N = CN + M_PER_PX * (vyA - vy_c)
    snap_E = round(est_E / 500) * 500
    snap_N = round(est_N / 500) * 500
    E0 = snap_E - M_PER_PX * vxA
    N0 = snap_N - M_PER_PX * vyA
    return E0, N0, abs(est_E - snap_E), abs(est_N - snap_N)

def transform_geom(geom, E0, N0, W_n_px, H_n_px, rotation):
    """Apply native-to-display rotation, then UTM affine."""
    name = geom.GetGeometryName()
    if name == "POINT":
        xn, yn = geom.GetX(), geom.GetY()
        xd, yd = native_to_display(xn, yn, W_n_px, H_n_px, rotation)
        geom.SetPoint(0, E0 + M_PER_PX * xd, N0 + M_PER_PX * yd)
        return
    if geom.GetGeometryCount() > 0:
        for i in range(geom.GetGeometryCount()):
            transform_geom(geom.GetGeometryRef(i), E0, N0, W_n_px, H_n_px, rotation)
    else:
        for i in range(geom.GetPointCount()):
            p = geom.GetPoint(i)
            xd, yd = native_to_display(p[0], p[1], W_n_px, H_n_px, rotation)
            geom.SetPoint(i, E0 + M_PER_PX * xd, N0 + M_PER_PX * yd)

def write_shapefiles(pdf_path, out_dir, E0, N0, W_n_px, H_n_px, rotation):
    src = ogr.Open(pdf_path)
    if src is None: return []
    srs = osr.SpatialReference(); srs.ImportFromEPSG(UTM_EPSG)
    drv = ogr.GetDriverByName("ESRI Shapefile")
    out = []
    for li in range(src.GetLayerCount()):
        in_lyr = src.GetLayerByIndex(li)
        name = in_lyr.GetName()
        buckets = defaultdict(list)
        in_lyr.ResetReading()
        for feat in in_lyr:
            g = feat.GetGeometryRef()
            if g is None: continue
            buckets[g.GetGeometryName()].append((feat.Clone(), g.Clone()))
        wkb_map = {"POINT": ogr.wkbPoint, "MULTIPOINT": ogr.wkbMultiPoint,
                   "LINESTRING": ogr.wkbLineString, "MULTILINESTRING": ogr.wkbMultiLineString,
                   "POLYGON": ogr.wkbPolygon, "MULTIPOLYGON": ogr.wkbMultiPolygon}
        for gt_name, items in buckets.items():
            suffix = "" if len(buckets) == 1 else f"_{gt_name.lower()}"
            shp_base = re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_") + suffix
            shp_path = os.path.join(out_dir, shp_base + ".shp")
            for ext in (".shp",".shx",".dbf",".prj",".cpg"):
                p = shp_path[:-4] + ext
                if os.path.exists(p): os.remove(p)
            out_ds = drv.CreateDataSource(shp_path)
            out_lyr = out_ds.CreateLayer(shp_base, srs, wkb_map.get(gt_name, ogr.wkbUnknown))
            src_defn = in_lyr.GetLayerDefn()
            for fi in range(src_defn.GetFieldCount()):
                out_lyr.CreateField(src_defn.GetFieldDefn(fi))
            out_defn = out_lyr.GetLayerDefn()
            for src_feat, g in items:
                transform_geom(g, E0, N0, W_n_px, H_n_px, rotation)
                nf = ogr.Feature(out_defn)
                for fi in range(src_defn.GetFieldCount()):
                    nf.SetField(fi, src_feat.GetField(fi))
                nf.SetGeometry(g)
                out_lyr.CreateFeature(nf)
                nf = None
            out_ds = None
            out.append((shp_base, len(items)))
    src = None
    return out

def write_geotiff(pdf_path, out_tif, E0, N0, W_n_px, H_n_px, rotation):
    """Render PDF to GeoTIFF and assign a rotation-aware GeoTransform."""
    src = gdal.Open(pdf_path)
    if src is None: return False
    # First Translate to a plain GeoTIFF (no georef), then overwrite the geotransform.
    gdal.Translate(out_tif, src,
                   options=gdal.TranslateOptions(
                       format="GTiff",
                       creationOptions=["COMPRESS=LZW", "TILED=YES", "BIGTIFF=IF_SAFER"]))
    src = None
    ds = gdal.Open(out_tif, gdal.GA_Update)
    M = M_PER_PX
    # GDAL geotransform: X = GT0 + col*GT1 + row*GT2,  Y = GT3 + col*GT4 + row*GT5
    # Native raster: col = xn (0=left), row = H_n_px - 1 - yn (0=top, so yn = H_n_px - 1 - row).
    # Display coords from native:  xd = native_to_display(xn, yn).x, yd = .y
    # UTM:  X = E0 + M*xd,  Y = N0 + M*yd
    # col = xn,  row = H_n_px - 1 - yn,  so yn = H_n_px - 1 - row.
    if rotation == 0:
        gt = (E0, M, 0,
              N0 + M*(H_n_px - 1), 0, -M)
    elif rotation == 90:
        # xd = H_n-1-yn = row,  yd = xn = col
        gt = (E0, 0, M,
              N0, M, 0)
    elif rotation == 180:
        gt = (E0 + M*(W_n_px - 1), -M, 0,
              N0, 0, M)
    elif rotation == 270:
        # xd = yn = H_n-1 - row,  yd = W_n-1 - xn = W_n-1 - col
        gt = (E0 + M*(H_n_px - 1), 0, -M,
              N0 + M*(W_n_px - 1), -M, 0)
    else:
        raise ValueError(rotation)
    ds.SetGeoTransform(gt)
    srs = osr.SpatialReference(); srs.ImportFromEPSG(UTM_EPSG)
    ds.SetProjection(srs.ExportToWkt())
    ds = None
    return True

def wnum(fn):
    m = re.search(r"(\d+)", fn)
    return int(m.group(1)) if m else None

def process(pdf_path, ward_info):
    fn = os.path.basename(pdf_path)
    n = wnum(fn)
    if n is None or n not in ward_info:
        return ("skip", fn, n, "no_ward")
    try:
        Vs, Hs, pw, ph, rot = detect_grid_native(pdf_path)
        if len(Vs) < 2 or len(Hs) < 2:
            return ("skip", fn, n, f"grid_V{len(Vs)}_H{len(Hs)}")
        bb = pdf_bbox_native_px(pdf_path)
        if bb is None:
            return ("skip", fn, n, "no_vec")
        W_n_px = pw * PT2PX; H_n_px = ph * PT2PX
        # Transform bbox center to display
        cx_n = (bb[0]+bb[2])/2; cy_n = (bb[1]+bb[3])/2
        cx_d, cy_d = native_to_display(cx_n, cy_n, W_n_px, H_n_px, rot)
        V_disp, H_disp, W_d, H_d = transform_grid_to_display(Vs, Hs, pw, ph, rot)
        E0, N0, eE, eN = compute_offsets(V_disp, H_disp, (cx_d, cy_d), ward_info[n])
        out_dir = os.path.join(OUT_DIR, f"ward_{n:03d}")
        os.makedirs(out_dir, exist_ok=True)
        out_tif = os.path.join(out_dir, f"ward_{n:03d}.tif")
        write_geotiff(pdf_path, out_tif, E0, N0, W_n_px, H_n_px, rot)
        shps = write_shapefiles(pdf_path, out_dir, E0, N0, W_n_px, H_n_px, rot)
        return ("ok", fn, n, {"E0":E0,"N0":N0,"eE":eE,"eN":eN,"rotation":rot,"shps":len(shps)})
    except Exception as e:
        return ("err", fn, n, f"{e}\n{traceback.format_exc()}")

def process_force_rot0(pdf_path, ward_info):
    """Treat the PDF as if it had no rotation (ignore /Rotate metadata)."""
    fn = os.path.basename(pdf_path)
    n = wnum(fn)
    if n is None or n not in ward_info:
        return ("skip", fn, n, "no_ward")
    try:
        Vs, Hs, pw, ph, _ = detect_grid_native(pdf_path)
        if len(Vs) < 2 or len(Hs) < 2:
            return ("skip", fn, n, f"grid_V{len(Vs)}_H{len(Hs)}")
        bb = pdf_bbox_native_px(pdf_path)
        if bb is None:
            return ("skip", fn, n, "no_vec")
        W_n_px = pw * PT2PX; H_n_px = ph * PT2PX
        cx_n = (bb[0]+bb[2])/2; cy_n = (bb[1]+bb[3])/2
        V_disp, H_disp, _, _ = transform_grid_to_display(Vs, Hs, pw, ph, 0)
        E0, N0, eE, eN = compute_offsets(V_disp, H_disp, (cx_n, cy_n), ward_info[n])
        out_dir = os.path.join(OUT_DIR, f"ward_{n:03d}")
        os.makedirs(out_dir, exist_ok=True)
        out_tif = os.path.join(out_dir, f"ward_{n:03d}.tif")
        write_geotiff(pdf_path, out_tif, E0, N0, W_n_px, H_n_px, 0)
        shps = write_shapefiles(pdf_path, out_dir, E0, N0, W_n_px, H_n_px, 0)
        return ("ok", fn, n, {"E0":E0,"N0":N0,"eE":eE,"eN":eN,"rotation":0,"shps":len(shps)})
    except Exception as e:
        return ("err", fn, n, f"{e}\n{traceback.format_exc()}")

def main():
    ward_info = load_wards()
    # Only the PDFs that have /Rotate 270 metadata (which we now know to IGNORE).
    targets = []
    for fn in sorted(os.listdir(PDFS_DIR)):
        if not fn.lower().endswith(".pdf"): continue
        doc = fitz.open(os.path.join(PDFS_DIR, fn))
        if doc[0].rotation != 0:
            targets.append(fn)
        doc.close()
    print(f"Reprocessing {len(targets)} /Rotate-flagged PDFs with rotation IGNORED", flush=True)
    for i, fn in enumerate(targets, 1):
        status, fn_, n, info = process_force_rot0(os.path.join(PDFS_DIR, fn), ward_info)
        if status == "ok":
            extra = f"rot=0(forced) E0={info['E0']:.1f} N0={info['N0']:.1f} eE={info['eE']:.1f} eN={info['eN']:.1f}"
        else:
            extra = str(info)[:160]
        print(f"  [{i:2d}/{len(targets)}] {status:4s} {fn[:30]:30s} ward {n:>4d}  {extra}", flush=True)

if __name__ == "__main__":
    main()
