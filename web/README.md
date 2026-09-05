# Chennai SWD — 3D viewer

Static. No build step, no server-side code. Everything is `index.html` plus two
JSON files.

```
index.html            the whole app
data/network.json     5,941 manholes + 6,370 conduits, WGS84   (4.0 MB)
data/wards.json       200 GCC ward outlines                    (0.5 MB)
data/rainfall.json    the Dec 2015 event summary               (small)
```

## Serving it

Anything that serves static files will do:

```bash
python3 -m http.server 8779 --bind 127.0.0.1 --directory .
```

For a real deployment, two things are worth getting right:

**1. Enable gzip or brotli.** `network.json` is 4 MB raw and compresses to about
900 KB — that is the difference between a fast first load and a slow one on
mobile data. nginx:

```nginx
gzip on;
gzip_types application/json application/javascript text/css text/html;
gzip_min_length 1024;
```

**2. Serve over HTTPS.** Browsers refuse the Geolocation API outside a secure
context, so over plain HTTP the "locate me" button is dead — the page detects
this and says so, but the feature simply cannot work. Everything else is fine
over HTTP.

## What it needs from the network

Three external origins, all over HTTPS:

| origin | for |
|---|---|
| `unpkg.com` | MapLibre GL JS 4.7.1, Three.js 0.160.0 |
| `fonts.googleapis.com` / `fonts.gstatic.com` | IBM Plex Mono, Barlow Condensed |
| `server.arcgisonline.com`, `mt1.google.com`, `basemaps.cartocdn.com` | basemap tiles |

If the deployment has to work offline or behind a strict CSP, vendor the two
libraries and the fonts locally and pick one tile source.

## Regenerating the data

From the project root:

```bash
python run_web_export.py
```

That reads `derived/network/*.json` and the TNGIS ward boundary GeoJSON. The
ward source path is hard-coded near the top of `run_web_export.py` — point it
somewhere else if you move things.

## Browser support

Needs WebGL 2 and ES modules — any browser from the last few years. It draws
about 1.3 M vertices in a single mesh; mid-range phones handle it, but the first
load parses a 4 MB JSON so expect a few seconds on a slow connection.
