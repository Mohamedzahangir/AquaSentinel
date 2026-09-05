#!/usr/bin/env python3
"""
Build a SWMM rainfall series for a Chennai storm.

No free source gives both the right volume and an hourly profile:

* reanalysis (ERA5, NASA POWER) and satellite (CHIRPS) products **miss the
  2 Dec 2015 peak by roughly six times** — 48-58 mm against 345 mm observed.
  Driving a 2015 run off them would silently under-predict flooding.
* the IMD 0.25 deg gridded analysis and the Meenambakkam gauge both reproduce
  it (349.6 and 344.9 mm), but are daily only.

So volume comes from the gauge/grid and the *shape* from ERA5, each day's hourly
profile rescaled to the observed daily total. That is standard practice when a
sub-daily record does not exist.

The two disagree on what a "day" is: an Indian rain-day runs 08:30 IST to 08:30
IST, ERA5 runs midnight to midnight. Ignoring that smears the peak across two
days, so the hourly window is shifted accordingly.

    python run_rainfall.py 2015-11-30 2015-12-06 --name michaung
"""
import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

ROOT = "/Users/admin/Desktop/projects/swd"
OUT = f"{ROOT}/derived/rainfall"

GHCN = "https://www.ncei.noaa.gov/access/services/data/v1"
STATION = "IN020040900"           # MADRAS MINAMBAKKAM, PRCP 1901 -> present
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
LAT, LON = 13.0827, 80.2707
RAIN_DAY_START_H = 9              # 08:30 IST, rounded to the hourly stamp


def get_json(url: str, params: dict):
    q = f"{url}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(q, timeout=120) as r:
        return json.loads(r.read().decode())


def daily_gauge(start: str, end: str) -> dict[str, float]:
    """Observed daily totals, mm, from the Meenambakkam gauge."""
    rows = get_json(GHCN, {
        "dataset": "daily-summaries", "stations": STATION,
        "startDate": start, "endDate": end, "dataTypes": "PRCP",
        "format": "json", "units": "metric"})
    out = {}
    for r in rows:
        if r.get("PRCP") not in (None, ""):
            out[r["DATE"]] = float(r["PRCP"])
    return out


def hourly_shape(start: str, end: str) -> dict[str, float]:
    """ERA5 hourly precipitation, mm, keyed by local ISO hour."""
    d0 = date.fromisoformat(start) - timedelta(days=1)
    d1 = date.fromisoformat(end) + timedelta(days=1)
    js = get_json(OPEN_METEO, {
        "latitude": LAT, "longitude": LON,
        "start_date": d0.isoformat(), "end_date": d1.isoformat(),
        "hourly": "precipitation", "timezone": "Asia/Kolkata"})
    h = js["hourly"]
    return dict(zip(h["time"], [v or 0.0 for v in h["precipitation"]]))


def imd_grid_daily(year: int, days: list[str]) -> dict[str, float]:
    """IMD 0.25 deg gridded totals, if the NetCDF for that year is present.

    The core city cell is nodata (the analysis is a land-station interpolation),
    so the nearest valid cell — 13.00 N, 80.00 E — is used.
    """
    path = None
    for cand in (f"{OUT}/imd{year}.grd",
                 f"/private/tmp/claude-501/-Users-admin-Desktop-projects-cumta/"
                 f"ed11a022-7c66-48c2-aa7b-a14355d8f16f/scratchpad/imd{year}.grd"):
        if os.path.exists(cand):
            path = cand
            break
    if path is None:
        return {}
    try:
        from osgeo import gdal
        gdal.UseExceptions()
        ds = gdal.Open(f"NETCDF:{path}:RAINFALL")
        gt = ds.GetGeoTransform()
        col = int((80.00 - gt[0]) / gt[1])
        row = int((13.00 - gt[3]) / gt[5])
        out = {}
        for d in days:
            dt = date.fromisoformat(d)
            band = (dt - date(year, 1, 1)).days + 1
            if 1 <= band <= ds.RasterCount:
                v = float(ds.GetRasterBand(band).ReadAsArray(col, row, 1, 1)[0][0])
                if v > -900:
                    out[d] = round(v, 2)
        return out
    except Exception as e:
        print(f"  (IMD grid unavailable: {e})")
        return {}


def build(start: str, end: str, name: str):
    print(f"event {name}: {start} .. {end}")
    gauge = daily_gauge(start, end)
    print(f"  gauge {STATION}: {len(gauge)} days, total {sum(gauge.values()):.1f} mm, "
          f"peak {max(gauge.values(), default=0):.1f} mm")
    imd = imd_grid_daily(date.fromisoformat(start).year, sorted(gauge))
    if imd:
        common = sorted(set(imd) & set(gauge))
        print(f"  IMD grid cross-check on {len(common)} days: "
              + ", ".join(f"{d[5:]} {imd[d]:.0f}/{gauge[d]:.0f}" for d in common[:6]))

    hourly = hourly_shape(start, end)
    rows, diag = [], []
    for d in sorted(gauge):
        total = gauge[d]
        dt = date.fromisoformat(d)
        # Indian rain-day: 08:30 IST on d -> 08:30 IST on d+1
        window = [(datetime.combine(dt, datetime.min.time())
                   + timedelta(hours=RAIN_DAY_START_H + i)) for i in range(24)]
        vals = [hourly.get(t.strftime("%Y-%m-%dT%H:%M"), 0.0) for t in window]
        s = sum(vals)
        if total <= 0:
            continue
        # How much of the observed volume did ERA5 actually see? Where it saw
        # almost none, its profile is a sliver that rescaling would amplify into
        # a fictitious cloudburst — on 5 Dec 2023 that turned 1.2 mm into a
        # 94 mm/h spike. So the profile is blended toward uniform in proportion
        # to how much of the day it explains.
        trust = min(1.0, s / total) if total > 0 else 0.0
        if s > 0:
            shape = [v / s for v in vals]
        else:
            shape = [1.0 / 24.0] * 24
        blended = [trust * f + (1.0 - trust) * (1.0 / 24.0) for f in shape]
        norm = sum(blended) or 1.0
        vals = [total * b / norm for b in blended]
        diag.append((d, total, round(s, 1), round(max(vals), 1), round(trust, 2)))
        for t, v in zip(window, vals):
            if v > 0.0005:
                rows.append(f"RAIN  {t.strftime('%m/%d/%Y')}  {t.strftime('%H:%M')}  {v:.3f}")

    print("  day        gauge_mm  era5_mm  peak_mm/h  shape_trust")
    for d, tot, s, pk, tr in diag:
        print(f"  {d}   {tot:7.1f}  {s:7.1f}  {pk:7.1f}      {tr:.2f}")

    first = date.fromisoformat(min(gauge))
    last = date.fromisoformat(max(gauge)) + timedelta(days=2)
    series = {
        "series": "RAIN", "interval": "1:00", "format": "VOLUME",
        "start": first.strftime("%m/%d/%Y"), "end": last.strftime("%m/%d/%Y"),
        "rows": rows, "event": name,
        "daily_mm": gauge, "imd_grid_mm": imd,
        "source": ("daily volume: NOAA GHCN-Daily station " + STATION +
                   " (IMD Meenambakkam); hourly shape: Open-Meteo ERA5 archive, "
                   "rescaled per Indian rain-day 08:30-08:30 IST"),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/{name}.json", "w") as f:
        json.dump(series, f, indent=1)
    with open(f"{OUT}/swmm_series.json", "w") as f:
        json.dump(series, f, indent=1)
    print(f"  -> {OUT}/{name}.json  ({len(rows)} hourly rows, "
          f"{sum(gauge.values()):.0f} mm total)")
    return series


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("start")
    ap.add_argument("end")
    ap.add_argument("--name", default="event")
    a = ap.parse_args()
    build(a.start, a.end, a.name)


if __name__ == "__main__":
    main()
