#!/usr/bin/env python3
"""
build_data.py — offline data pipeline for the Brain-Health Exposome Check-in
(/projects/dementia-exposome).

Produces the static assets consumed by the page, under public/data/:
  pm25/tw-county-pm25.json      county x year mean PM2.5 (1998-2024)  -> calculator
  pm25/tw-district-pm25.json    per-town recent PM2.5                 -> map layer
  geo/tw-districts.topo.json    368 town (鄉鎮市區) boundaries         -> map geometry
  dementia/tw-dementia-modelled.json   modelled prevalence            -> map layer
  manifest.json                 provenance / citation / licence

Data sources (all open):
  - PM2.5:  ACAG / WashU global surface PM2.5, V6.GL.03 (CNN), 0.1deg annual,
            AWS Open Data bucket s3://satpmdata/ (CC BY 4.0).
            Cite: Shen et al. 2024 (ACS ES&T Air); van Donkelaar et al. 2021 (ES&T).
  - Boundaries: taiwan-atlas (dkaoster) TopoJSON redistribution of MOI 鄉鎮市區界線
            (data.gov.tw #7441), OGDL-Taiwan-1.0 (~= CC BY 4.0). NLSC / 內政部.
  - Population for modelled prevalence: official source is MOI 內政部戶政司
            #77132 (single-year age by 村里). That API is not reachable from every
            build environment; this script tries it and falls back to a committed,
            cited COUNTY-level snapshot (see EMBEDDED_COUNTY_POP) — in which case the
            prevalence map is a COUNTY-level estimate. Re-run with gov access to
            regenerate the authoritative 鄉鎮市區-level version.
  - Dementia rates: NHRI national community survey 2020-2023 (age-band prevalence).

Deps (pip wheels, no system GDAL): netCDF4 numpy shapely requests.
Usage:  python3 scripts/build_data.py   (run from repo root)
"""
import io
import json
import os
import sys
import tarfile
import tempfile
import time

import numpy as np
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "public", "data")
DATA_IN = os.path.join(ROOT, "scripts", "_data_in")  # owner-downloaded inputs (gitignored)
BUILD_DATE = time.strftime("%Y-%m-%d")

# ---- config ----
YEAR0, YEAR1 = 1998, 2024
S3 = ("https://satpmdata.s3.us-east-1.amazonaws.com/V6GL03/CoarseResolution/GL/"
      "Annual/V6GL03.CNNPM25.0p10.GL.{y}01-{y}12.nc")
TW_BBOX = dict(lon_min=118.0, lon_max=122.5, lat_min=21.4, lat_max=26.5)  # incl. offshore
# Union bbox covering Taiwan + Japan + Korea, so the ACAG grid is loaded once and
# re-used (each unit is matched by point-in-polygon, so a wider grid is harmless).
ALL_BBOX = dict(lon_min=118.0, lon_max=154.0, lat_min=21.0, lat_max=46.5)
TATLAS_TGZ = "https://registry.npmjs.org/taiwan-atlas/-/taiwan-atlas-2021.9.20.tgz"
NE_ADMIN1 = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
             "master/geojson/ne_10m_admin_1_states_provinces.geojson")
NE_ADMIN0 = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
             "master/geojson/ne_110m_admin_0_countries.geojson")
# Other-country sub-national PM2.5 maps (boundaries: Natural Earth admin-1, ACAG zonal).
# bbox = the grid crop for that country (centroid-filtered so far-flung units — e.g. US
# Alaska/Hawaii — are dropped for a clean contiguous map). Signed lon (-180..180).
COUNTRY_MAPS = {
    "jp": {"admin": "Japan", "name": "Japan", "nameZh": "日本",
           "bbox": dict(lon_min=122.0, lon_max=154.0, lat_min=24.0, lat_max=46.5)},
    "kr": {"admin": "South Korea", "name": "South Korea", "nameZh": "南韓",
           "bbox": dict(lon_min=124.0, lon_max=132.0, lat_min=33.0, lat_max=39.0)},
    "cn": {"admin": "China", "name": "China", "nameZh": "中國",
           "bbox": dict(lon_min=73.0, lon_max=135.5, lat_min=17.0, lat_max=54.0)},
    "us": {"admin": "United States of America", "name": "United States (contiguous)", "nameZh": "美國（本土）",
           "bbox": dict(lon_min=-125.0, lon_max=-66.5, lat_min=24.0, lat_max=49.5)},
    "in": {"admin": "India", "name": "India", "nameZh": "印度",
           "bbox": dict(lon_min=68.0, lon_max=97.5, lat_min=6.0, lat_max=37.5)},
    # --- expansion batch (PM2.5 only; Natural Earth admin-1 + ACAG zonal) ---
    # SE / South Asia
    "th": {"admin": "Thailand", "name": "Thailand", "nameZh": "泰國",
           "bbox": dict(lon_min=97.3, lon_max=105.7, lat_min=5.5, lat_max=20.5)},
    "vn": {"admin": "Vietnam", "name": "Vietnam", "nameZh": "越南",
           "bbox": dict(lon_min=102.0, lon_max=109.5, lat_min=8.2, lat_max=23.4)},
    "id": {"admin": "Indonesia", "name": "Indonesia", "nameZh": "印尼",
           "bbox": dict(lon_min=95.0, lon_max=141.1, lat_min=-11.1, lat_max=6.1)},
    "ph": {"admin": "Philippines", "name": "Philippines", "nameZh": "菲律賓",
           "bbox": dict(lon_min=116.9, lon_max=126.7, lat_min=4.5, lat_max=19.6)},
    "my": {"admin": "Malaysia", "name": "Malaysia", "nameZh": "馬來西亞",
           "bbox": dict(lon_min=99.6, lon_max=119.3, lat_min=0.8, lat_max=7.4)},
    "pk": {"admin": "Pakistan", "name": "Pakistan", "nameZh": "巴基斯坦",
           "bbox": dict(lon_min=60.8, lon_max=77.9, lat_min=23.6, lat_max=37.1)},
    "bd": {"admin": "Bangladesh", "name": "Bangladesh", "nameZh": "孟加拉",
           "bbox": dict(lon_min=88.0, lon_max=92.7, lat_min=20.5, lat_max=26.7)},
    "mm": {"admin": "Myanmar", "name": "Myanmar", "nameZh": "緬甸",
           "bbox": dict(lon_min=92.1, lon_max=101.2, lat_min=9.5, lat_max=28.6)},
    # Europe (metropolitan; overseas territories dropped by centroid filter)
    "gb": {"admin": "United Kingdom", "name": "United Kingdom", "nameZh": "英國",
           "bbox": dict(lon_min=-8.7, lon_max=1.9, lat_min=49.8, lat_max=61.0)},
    "de": {"admin": "Germany", "name": "Germany", "nameZh": "德國",
           "bbox": dict(lon_min=5.8, lon_max=15.1, lat_min=47.2, lat_max=55.1)},
    "fr": {"admin": "France", "name": "France", "nameZh": "法國",
           "bbox": dict(lon_min=-5.3, lon_max=9.7, lat_min=41.3, lat_max=51.1)},
    "it": {"admin": "Italy", "name": "Italy", "nameZh": "義大利",
           "bbox": dict(lon_min=6.6, lon_max=18.6, lat_min=35.4, lat_max=47.1)},
    "es": {"admin": "Spain", "name": "Spain", "nameZh": "西班牙",
           "bbox": dict(lon_min=-9.4, lon_max=4.4, lat_min=35.9, lat_max=43.9)},
    "pl": {"admin": "Poland", "name": "Poland", "nameZh": "波蘭",
           "bbox": dict(lon_min=14.0, lon_max=24.2, lat_min=49.0, lat_max=54.9)},
    # Americas (negative lon — handled by crop_recent)
    "ca": {"admin": "Canada", "name": "Canada", "nameZh": "加拿大",
           "bbox": dict(lon_min=-141.0, lon_max=-52.0, lat_min=41.6, lat_max=70.0)},
    "br": {"admin": "Brazil", "name": "Brazil", "nameZh": "巴西",
           "bbox": dict(lon_min=-74.1, lon_max=-34.7, lat_min=-33.8, lat_max=5.3)},
    "mx": {"admin": "Mexico", "name": "Mexico", "nameZh": "墨西哥",
           "bbox": dict(lon_min=-118.5, lon_max=-86.7, lat_min=14.5, lat_max=32.8)},
    # Oceania (NZ capped <180; Chatham Is dropped by centroid filter)
    "au": {"admin": "Australia", "name": "Australia", "nameZh": "澳洲",
           "bbox": dict(lon_min=112.9, lon_max=153.7, lat_min=-43.7, lat_max=-10.5)},
    "nz": {"admin": "New Zealand", "name": "New Zealand", "nameZh": "紐西蘭",
           "bbox": dict(lon_min=166.0, lon_max=179.0, lat_min=-47.3, lat_max=-34.3)},
    # Middle East
    "tr": {"admin": "Turkey", "name": "Turkey", "nameZh": "土耳其",
           "bbox": dict(lon_min=25.6, lon_max=44.9, lat_min=35.8, lat_max=42.2)},
    "ir": {"admin": "Iran", "name": "Iran", "nameZh": "伊朗",
           "bbox": dict(lon_min=44.0, lon_max=63.4, lat_min=25.0, lat_max=39.9)},
}

# NHRI 2020-2023 age-band dementia prevalence (share of that age band)
NHRI_RATES = {"65-69": 0.0240, "70-74": 0.0516, "75-79": 0.0910,
              "80-84": 0.1600, "85-89": 0.2004, "90+": 0.2945}
# National distribution of the 65+ population across the six bands (approx, MOI ~2023).
# Used only when we lack per-area age structure (county fallback).
NAT_65_DIST = {"65-69": 0.30, "70-74": 0.24, "75-79": 0.17,
               "80-84": 0.13, "85-89": 0.09, "90+": 0.07}
# Weighted mean dementia rate among 65+ (national age structure) ~ 0.095.
RBAR = sum(NHRI_RATES[b] * NAT_65_DIST[b] for b in NHRI_RATES)

# Committed county snapshot fallback: (total population, share aged 65+), approx MOI 2023
# year-end. Keyed by COUNTYNAME as it appears in the taiwan-atlas boundary data (台 forms).
# Clearly a snapshot/estimate — the map is labelled accordingly.
EMBEDDED_COUNTY_POP = {
    "台北市": (2494000, 0.215), "新北市": (4004000, 0.170), "桃園市": (2320000, 0.140),
    "台中市": (2856000, 0.150), "台南市": (1862000, 0.180), "高雄市": (2738000, 0.180),
    "基隆市": (361000, 0.190), "新竹市": (452000, 0.135), "嘉義市": (261000, 0.175),
    "新竹縣": (585000, 0.140), "苗栗縣": (530000, 0.190), "彰化縣": (1247000, 0.180),
    "南投縣": (476000, 0.200), "雲林縣": (660000, 0.210), "嘉義縣": (484000, 0.220),
    "屏東縣": (795000, 0.195), "宜蘭縣": (449000, 0.185), "花蓮縣": (316000, 0.185),
    "台東縣": (211000, 0.190), "澎湖縣": (107000, 0.170), "金門縣": (143000, 0.130),
    "連江縣": (13900, 0.140),
}


def log(*a):
    msg = " ".join(str(x) for x in a)
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:      # Windows cp950 console can't encode e.g. Türkiye's 'ü'
        enc = sys.stdout.encoding or "utf-8"
        print(msg.encode(enc, "replace").decode(enc), flush=True)


def http_get(url, tries=4, timeout=120):
    for i in range(tries):
        try:
            r = requests.get(url, timeout=timeout,
                             headers={"User-Agent": "mattye-exposome-build/1.0"})
            if r.status_code == 200:
                return r.content
            log(f"  HTTP {r.status_code} for {url}")
        except Exception as e:
            log(f"  attempt {i+1} failed: {e}")
        time.sleep(2 ** i)
    raise RuntimeError(f"GET failed: {url}")


# ---------- TopoJSON decode (transform + delta arcs -> shapely) ----------
from shapely.geometry import Point, Polygon, MultiPolygon, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402


def _decode_arc(topo, i):
    rev = i < 0
    if rev:
        i = ~i
    x = y = 0
    pts = []
    for dx, dy in topo["arcs"][i]:
        x += dx
        y += dy
        pts.append((x, y))
    return pts[::-1] if rev else pts


def _ring(topo, arc_idxs):
    coords = []
    for k, ai in enumerate(arc_idxs):
        seg = _decode_arc(topo, ai)
        coords.extend(seg[1:] if k else seg)
    s = topo["transform"]["scale"]
    t = topo["transform"]["translate"]
    return [(x * s[0] + t[0], y * s[1] + t[1]) for x, y in coords]


def _geom(topo, g):
    ty = g["type"]
    if ty == "Polygon":
        rings = [_ring(topo, r) for r in g["arcs"]]
        return Polygon(rings[0], rings[1:]) if rings else None
    if ty == "MultiPolygon":
        polys = []
        for poly in g["arcs"]:
            rings = [_ring(topo, r) for r in poly]
            if rings:
                polys.append(Polygon(rings[0], rings[1:]))
        return MultiPolygon(polys) if polys else None
    return None


def load_boundaries():
    """Return (topo_dict, towns[list of {props, geom}], counties[{name:geom}])."""
    log("Fetching taiwan-atlas boundaries (npm tarball)…")
    raw = http_get(TATLAS_TGZ)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        towns_topo = json.load(tf.extractfile("package/towns-10t.json"))
    towns, counties = [], {}
    for g in towns_topo["objects"]["towns"]["geometries"]:
        geom = _geom(towns_topo, g)
        if geom and not geom.is_valid:
            geom = geom.buffer(0)
        towns.append({"props": g["properties"], "geom": geom})
    for g in towns_topo["objects"]["counties"]["geometries"]:
        geom = _geom(towns_topo, g)
        if geom and not geom.is_valid:
            geom = geom.buffer(0)
        counties[g["properties"]["COUNTYNAME"]] = {
            "props": g["properties"], "geom": geom}
    log(f"  {len(towns)} towns, {len(counties)} counties decoded")
    return towns_topo, towns, counties


# ---------- ACAG PM2.5 ----------
def load_pm25_years():
    """Stream each annual ACAG file, crop Taiwan bbox, return
       (years, lat[sub], lon[sub], grid[year][lat][lon])."""
    import netCDF4
    years = list(range(YEAR0, YEAR1 + 1))
    sub_lat = sub_lon = None
    li = None
    stack = []
    tmpdir = tempfile.mkdtemp(prefix="acag_")
    try:
        for y in years:
            url = S3.format(y=y)
            path = os.path.join(tmpdir, f"{y}.nc")
            log(f"  ACAG {y} …")
            with open(path, "wb") as fh:
                fh.write(http_get(url))
            ds = netCDF4.Dataset(path)
            lat = ds.variables["lat"][:]
            lon = ds.variables["lon"][:]
            if li is None:
                la = np.where((lat >= ALL_BBOX["lat_min"]) & (lat <= ALL_BBOX["lat_max"]))[0]
                lo = np.where((lon >= ALL_BBOX["lon_min"]) & (lon <= ALL_BBOX["lon_max"]))[0]
                li = (la.min(), la.max() + 1, lo.min(), lo.max() + 1)
                sub_lat = np.asarray(lat[li[0]:li[1]])
                sub_lon = np.asarray(lon[li[2]:li[3]])
            pm = ds.variables["PM25"][li[0]:li[1], li[2]:li[3]]
            arr = np.ma.filled(np.ma.masked_invalid(pm).astype("float64"), np.nan)
            arr[(arr < 0) | (arr > 1000)] = np.nan   # drop fill/NoData (e.g. -999 over ocean)
            stack.append(arr)
            ds.close()
            os.remove(path)  # keep peak disk tiny
    finally:
        try:
            os.rmdir(tmpdir)
        except OSError:
            pass
    log(f"  loaded {len(stack)} years, grid {stack[0].shape} (TW+JP+KR bbox)")
    return years, sub_lat, sub_lon, np.stack(stack)  # [year, lat, lon]


def load_ne_admin1():
    log("Fetching Natural Earth admin-1 (provinces/states)…")
    return json.loads(http_get(NE_ADMIN1).decode("utf-8", "replace"))


def load_pm25_recent_cached(recent=3):
    """Download the recent-N ACAG annual files once to a tmpdir (kept), so every
       country's admin-1 crop reuses them. Returns (years, tmpdir, {year: path})."""
    years = list(range(YEAR1 - recent + 1, YEAR1 + 1))
    tmpdir = tempfile.mkdtemp(prefix="acag_recent_")
    paths = {}
    for y in years:
        p = os.path.join(tmpdir, f"{y}.nc")
        log(f"  ACAG {y} (recent, global) …")
        with open(p, "wb") as fh:
            fh.write(http_get(S3.format(y=y)))
        paths[y] = p
    return years, tmpdir, paths


def crop_recent(years, paths, bbox):
    """Crop the cached recent files to bbox → (lat, lon, grid[recent, lat, lon]).
       Returns lon in signed -180..180 to match the Natural Earth polygons, whatever
       convention the source grid uses."""
    import netCDF4
    li = None
    sub_lat = sub_lon = None
    stack = []
    for y in years:
        ds = netCDF4.Dataset(paths[y])
        lat = np.asarray(ds.variables["lat"][:])
        lon = np.asarray(ds.variables["lon"][:])
        if li is None:
            is360 = float(lon.max()) > 180.5
            lo_min, lo_max = bbox["lon_min"], bbox["lon_max"]
            if is360 and lo_min < 0:            # grid is 0..360, bbox is signed
                lo_min += 360.0
                lo_max += 360.0
            la = np.where((lat >= bbox["lat_min"]) & (lat <= bbox["lat_max"]))[0]
            lo = np.where((lon >= lo_min) & (lon <= lo_max))[0]
            li = (la.min(), la.max() + 1, lo.min(), lo.max() + 1)
            sub_lat = np.asarray(lat[li[0]:li[1]])
            sub_lon = np.asarray(lon[li[2]:li[3]])
            sub_lon = np.where(sub_lon > 180.0, sub_lon - 360.0, sub_lon)  # → signed
        pm = ds.variables["PM25"][li[0]:li[1], li[2]:li[3]]
        arr = np.ma.filled(np.ma.masked_invalid(pm).astype("float64"), np.nan)
        arr[(arr < 0) | (arr > 1000)] = np.nan   # drop fill/NoData (e.g. -999 over ocean)
        stack.append(arr)
        ds.close()
    return sub_lat, sub_lon, np.stack(stack)


def ne_units(ne_geojson, admin_name, bbox=None):
    """Return [{code, name, nameLocal, geom}] for one country's admin-1 units.
       If bbox is given, keep only units whose centroid falls inside it (drops
       far-flung pieces we have no grid for, e.g. US Alaska/Hawaii)."""
    out = []
    for f in ne_geojson["features"]:
        p = f["properties"]
        if p.get("admin") != admin_name:
            continue
        geom = shape(f["geometry"])
        if not geom.is_valid:
            geom = geom.buffer(0)
        if bbox is not None:
            cen = geom.centroid
            if not (bbox["lon_min"] <= cen.x <= bbox["lon_max"] and
                    bbox["lat_min"] <= cen.y <= bbox["lat_max"]):
                continue
        code = p.get("iso_3166_2") or p.get("adm1_code") or p.get("name")
        # name_local may pack several scripts as "傳統|简体"; keep the first (traditional) form
        name_local = (p.get("name_local") or p.get("name") or "").split("|")[0].strip()
        out.append({"code": code, "name": p.get("name"),
                    "nameLocal": name_local or p.get("name"), "geom": geom})
    return out


def admin1_recent_pm(units, years, lat, lon, grid, recent=3):
    """Per admin-1 unit: mean of grid cells inside the polygon (recent-N-yr mean);
       tiny units fall back to the nearest cell to the centroid."""
    yi0 = len(years) - recent
    recent_mean = np.nanmean(grid[yi0:], axis=0)
    LON, LAT = np.meshgrid(lon, lat)
    out = {}
    for u in units:
        geom = u["geom"]
        minx, miny, maxx, maxy = geom.bounds
        m = (LON >= minx) & (LON <= maxx) & (LAT >= miny) & (LAT <= maxy)
        vals = []
        for r, c in np.argwhere(m):
            if geom.contains(Point(LON[r, c], LAT[r, c])):
                v = recent_mean[r, c]
                if not np.isnan(v):
                    vals.append(v)
        if not vals:
            cen = geom.centroid
            r = int(np.abs(lat - cen.y).argmin())
            c = int(np.abs(lon - cen.x).argmin())
            v = recent_mean[r, c]
            if not np.isnan(v):
                vals = [v]
        out[u["code"]] = round(float(np.mean(vals)), 1) if vals else None
    return out, f"{years[yi0]}-{years[-1]} mean"


def units_geojson(units, tol=0.01):
    from shapely.geometry import mapping
    feats = []
    for u in units:
        g = u["geom"].simplify(tol, preserve_topology=True)
        feats.append({"type": "Feature",
                      "properties": {"code": u["code"], "name": u["name"], "nameLocal": u["nameLocal"]},
                      "geometry": mapping(g)})
    return {"type": "FeatureCollection", "features": feats}


def county_year_series(counties, years, lat, lon, grid):
    """Mean of grid cells whose centre falls inside each county polygon."""
    LON, LAT = np.meshgrid(lon, lat)
    out = {}
    for name, c in counties.items():
        geom = c["geom"]
        minx, miny, maxx, maxy = geom.bounds
        m = (LON >= minx) & (LON <= maxx) & (LAT >= miny) & (LAT <= maxy)
        idx = np.argwhere(m)
        inside = [(r, cc) for r, cc in idx if geom.contains(Point(LON[r, cc], LAT[r, cc]))]
        if not inside:  # county smaller than a 0.1deg cell -> nearest to centroid
            cen = geom.centroid
            r = int(np.abs(lat - cen.y).argmin())
            cc = int(np.abs(lon - cen.x).argmin())
            inside = [(r, cc)]
        rows = [r for r, _ in inside]
        cols = [cc for _, cc in inside]
        series = []
        for yi in range(len(years)):
            vals = grid[yi, rows, cols]
            vals = vals[~np.isnan(vals)]
            series.append(round(float(vals.mean()), 1) if len(vals) else None)
        out[c["props"]["COUNTYCODE"]] = {
            "name": name, "nameEn": c["props"]["COUNTYENG"], "values": series}
    return out


def town_recent_pm(towns, years, lat, lon, grid, recent=3):
    """Per-town PM2.5 = nearest grid cell to town centroid, mean of last `recent` years."""
    yi0 = len(years) - recent
    recent_mean = np.nanmean(grid[yi0:], axis=0)  # [lat, lon]
    out = {}
    for t in towns:
        cen = t["geom"].centroid
        r = int(np.abs(lat - cen.y).argmin())
        cc = int(np.abs(lon - cen.x).argmin())
        v = recent_mean[r, cc]
        out[t["props"]["TOWNCODE"]] = {
            "name": t["props"]["TOWNNAME"],
            "pm25": round(float(v), 1) if not np.isnan(v) else None}
    return out, f"{years[yi0]}-{years[-1]} mean"


# ---------- population -> modelled prevalence ----------
def _rollup_77132_town(yyymm="11506"):
    """MOI #77132 (村里 single-year-age population) -> per-town (8-digit TOWNCODE, the
       taiwan-atlas boundary key) six NHRI age bands + total. Sums both sexes and every
       村里 within a 鄉鎮市區. Returns {code: {'total':N,'65-69':n,...,'90+':n}} or None."""
    base = "https://www.ris.gov.tw/rs-opendata/api/v1/datastore/ODRP014/%s?page=%d"

    def band(a):
        return ("65-69" if a <= 69 else "70-74" if a <= 74 else "75-79" if a <= 79
                else "80-84" if a <= 84 else "85-89" if a <= 89 else "90+")

    town = {}

    def ingest(d):
        for row in d.get("responseData", []):
            code = (row.get("district_code") or "")[:8]  # 村里 code -> 鄉鎮市區 (first 8)
            if len(code) < 8:
                continue
            t = town.setdefault(code, {"total": 0.0, "65-69": 0.0, "70-74": 0.0,
                                       "75-79": 0.0, "80-84": 0.0, "85-89": 0.0, "90+": 0.0})
            t["total"] += float(row.get("people_total") or 0)
            for a in range(65, 100):
                t[band(a)] += (float(row.get(f"people_age_{a:03d}_m") or 0)
                               + float(row.get(f"people_age_{a:03d}_f") or 0))
            t["90+"] += float(row.get("people_age_100up_m") or 0) + float(row.get("people_age_100up_f") or 0)

    try:
        first = json.loads(http_get(base % (yyymm, 1)).decode("utf-8"))
    except Exception as e:
        log(f"  MOI #77132 not reachable ({e})")
        return None
    ingest(first)
    for pg in range(2, int(first.get("totalPage", 1)) + 1):
        ingest(json.loads(http_get(base % (yyymm, pg)).decode("utf-8")))
    return town or None


def fetch_population(yyymm="11506"):
    """Authoritative town-level MOI #77132 rollup if the API is reachable (this session's
       job); else the committed county snapshot. Returns (granularity, pop)."""
    town = _rollup_77132_town(yyymm)
    if town:
        log(f"  MOI #77132 {yyymm}: single-year age rolled up -> {len(town)} 鄉鎮市區")
        return "town", town
    log("  using committed county snapshot (county-level estimate).")
    return "county", EMBEDDED_COUNTY_POP


def modelled_prevalence(towns, granularity, pop):
    """Town granularity (MOI #77132): each 鄉鎮市區 gets its OWN prevalence among 65+ from its
       real age structure — cases = Σ band_pop × NHRI band rate; prevalence% = cases / 65+ pop.
       County fallback: uniform RBAR (no within-65+ structure), inherited by its towns."""
    if granularity == "town":
        # Metric: dementia prevalence AMONG THE ELDERLY (65+) = modelled cases / 65+ population.
        # NHRI_RATES are age-band rates for 65+, so this is the exact population-weighted mean of
        # those rates for each town's real 65+ age structure — no extra assumption, and the
        # national value ≈ Taiwan's officially reported ~8% of 65+. (A 60+/55+ denominator would
        # additionally need a 60-64 / 55-59 dementia rate that the current inputs don't provide.)
        rows, by_town = [], {}
        for code, rec in pop.items():
            pop65 = sum(rec[b] for b in NHRI_RATES)
            if not pop65:
                continue
            cases = sum(rec[b] * NHRI_RATES[b] for b in NHRI_RATES)
            prev_pct = round(cases / pop65 * 100, 1)
            by_town[code] = prev_pct
            rows.append({"townCode": code, "pop_total": int(round(rec.get("total", 0.0))),
                         "pop_65plus": int(round(pop65)), "modelled_cases": int(round(cases)),
                         "prevalence_65plus_pct": prev_pct})
        return rows, by_town
    county_rate, county_rows = {}, []
    for name, (total, share65) in pop.items():
        pop65 = total * share65
        cases = pop65 * RBAR
        prev_pct = round(RBAR * 100, 1)  # county fallback lacks within-65+ structure -> uniform RBAR
        county_rate[name] = prev_pct
        county_rows.append({"countyName": name, "pop_total": total,
                            "pop_65plus": int(round(pop65)),
                            "modelled_cases": int(round(cases)),
                            "prevalence_65plus_pct": prev_pct})
    by_town = {}
    for t in towns:
        p = t["props"]
        by_town[p["TOWNCODE"]] = county_rate.get(p["COUNTYNAME"])
    return county_rows, by_town


def write_tw_modelled(rows, by_town, granularity):
    """Write dementia/tw-dementia-modelled.json — metric = prevalence among residents aged 65+ (%),
       plus the national overall and the NHRI age-band rate table (drives the on-page method table).
       Shared by main() and offline regeneration so the committed asset matches a full build."""
    nat_cases = sum(r["modelled_cases"] for r in rows)
    nat_pop65 = sum(r["pop_65plus"] for r in rows)
    nat_pct = round(nat_cases / nat_pop65 * 100, 1) if nat_pop65 else None
    age_bands = [{"band": b, "prevalence_pct": round(NHRI_RATES[b] * 100, 2)} for b in NHRI_RATES]
    if granularity == "town":
        meta = {"title": "Modelled dementia prevalence among residents aged 65+ (ESTIMATE)",
                "granularity": "town",
                "method": ("MOI #77132 single-year-age population per 鄉鎮市區 × NHRI 112年(2023) "
                           "age-band prevalence; each town = Σ(age-band pop × band rate) ÷ its 65+ "
                           "population = prevalence among residents aged 65+"),
                "disclaimer": ("MODELLED TOWN-LEVEL ESTIMATE, not measured. Taiwan publishes no "
                               "sub-national dementia prevalence; national age-band rates are "
                               "applied to each 鄉鎮市區's real single-year age structure."),
                "population_source": "MOI #77132 鄉鎮市區單一年齡人口 (內政部戶政司)"}
        rows_key = "towns"
    else:
        meta = {"title": "Modelled dementia prevalence among residents aged 65+ (ESTIMATE)",
                "granularity": "county",
                "method": ("national NHRI 2020-2023 age-band rates × county 65+ population; the "
                           "county fallback lacks within-65+ structure so every area ≈ the national rate"),
                "disclaimer": ("MODELLED COUNTY-LEVEL ESTIMATE, not measured. Taiwan publishes no "
                               "sub-national dementia prevalence. Re-run build_data.py with MOI "
                               "#77132 access for the authoritative town-level version."),
                "population_source": "MOI 2023 county snapshot (approx.)"}
        rows_key = "counties"
    meta.update({"rates_source": ("NHRI 112年(2023) 全國社區失智症流行病學調查 (age-band prevalence); "
                                  "method per Sun Y, et al. PLoS ONE 2014 (doi:10.1371/journal.pone.0100303)"),
                 "metric": "modelled dementia prevalence among residents aged 65+ (%)",
                 "age_group": "65+", "national_prevalence_65plus_pct": nat_pct, "age_bands": age_bands,
                 "license": "OGDL-Taiwan-1.0", "built": BUILD_DATE})
    write_json("dementia/tw-dementia-modelled.json", {"meta": meta, rows_key: rows, "byTown": by_town})


# ---------- generic modelled prevalence from owner-downloaded CSVs (_data_in) ----------
# Lets any country's prevalence layer be built once the owner drops the two CSVs
# documented in docs/.../data-download-points.md. Schema:
#   {cc}-admin1-pop.csv     unit_code,pop_total[,pop_65_69,pop_70_74,pop_75_79,pop_80_84,pop_85p]
#   {cc}-rates.csv          age_band,prevalence_pct        (age_band in POP_BANDS)
POP_BANDS = ["65_69", "70_74", "75_79", "80_84", "85p"]  # 00_64 contributes ~0 dementia


def _read_csv(path):
    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_admin_pop(cc):
    """_data_in/{cc}-admin1-pop.csv -> {unit_code: {band: n, ..., 'total': n}} or None."""
    path = os.path.join(DATA_IN, f"{cc}-admin1-pop.csv")
    if not os.path.exists(path):
        return None
    out = {}
    for row in _read_csv(path):
        code = (row.get("unit_code") or "").strip()
        if not code:
            continue
        rec = {}
        for b in POP_BANDS:
            v = row.get(f"pop_{b}")
            if v not in (None, ""):
                rec[b] = float(v)
        total = row.get("pop_total")
        rec["total"] = (float(total) if total not in (None, "")
                        else sum(rec.get(b, 0.0) for b in POP_BANDS))
        out[code] = rec
    return out or None


def load_rates(cc):
    """_data_in/{cc}-rates.csv (or {cc}-dementia-rates.csv): age_band,prevalence_pct
       -> {band: fraction} or None."""
    for fn in (f"{cc}-rates.csv", f"{cc}-dementia-rates.csv"):
        path = os.path.join(DATA_IN, fn)
        if not os.path.exists(path):
            continue
        out = {}
        for row in _read_csv(path):
            band = (row.get("age_band") or "").strip()
            pct = row.get("prevalence_pct")
            if band and pct not in (None, ""):
                out[band] = float(pct) / 100.0
        return out or None
    return None


def modelled_from_csv(pop, rates):
    """cases = Σ_band pop_band × rate_band (or 65+ × RBAR fallback);
       rate_per_1000 = cases / pop_total × 1000. -> (by_unit{code: rate}, rows)."""
    by_unit, rows = {}, []
    have_bandrates = bool(rates) and any(b in rates for b in POP_BANDS)
    for code, rec in pop.items():
        total = rec.get("total", 0.0)
        if not total:
            continue
        pop65 = sum(rec.get(b, 0.0) for b in POP_BANDS)
        if have_bandrates and any(b in rec for b in POP_BANDS):
            cases = sum(rec.get(b, 0.0) * rates.get(b, 0.0) for b in POP_BANDS)
        else:
            cases = pop65 * RBAR  # only 65+ known, or no age-band rates: single crude rate
        rate = round(cases / total * 1000, 1)
        by_unit[code] = rate
        rows.append({"code": code, "pop_total": int(round(total)),
                     "pop_65plus": int(round(pop65)),
                     "modelled_cases": int(round(cases)), "rate_per_1000": rate})
    return by_unit, rows


def build_admin_prevalence(cc, name):
    """If the owner dropped {cc}-admin1-pop.csv (+ optional rates) in _data_in/,
       emit dementia/{cc}-modelled.json and return True; else False (layer pending)."""
    pop = load_admin_pop(cc)
    if not pop:
        return False
    rates = load_rates(cc)
    by_unit, rows = modelled_from_csv(pop, rates)
    write_json(f"dementia/{cc}-modelled.json", {
        "meta": {"metric": "modelled dementia cases per 1,000 residents (all ages)",
                 "method": ("Σ age-band population × age-band prevalence"
                            if rates else "65+ population × crude 65+ rate (RBAR)"),
                 "note": "MODELLED estimate, not measured",
                 "rates_source": "downloaded" if rates else "RBAR fallback",
                 "built": BUILD_DATE},
        "byTown": by_unit, "rows": rows})
    log(f"  {name}: modelled prevalence for {len(by_unit)} units "
        f"({'age-band rates' if rates else 'RBAR fallback'})")
    return True


def _norm_name(s):
    return (s or "").strip().lower().replace(".", "").replace("&", "and").replace("-", " ")


# ---------- GBD 2023 direct admin-1 prevalence (owner's login-export in _data_in/GBD/) ----------
# The GBD Results tool is login-gated, so the owner downloads the CSV once (Alzheimer's & other
# dementias, Prevalence, by location/age/sex) into scripts/_data_in/GBD/ (gitignored). For the
# countries GBD covers sub-nationally that match our Natural Earth admin-1 names, we can read the
# per-unit "% among 60+" directly — no population×rate modelling. Aliases bridge accent/long-name
# differences (GBD "Piaui" vs boundary "Piauí", etc.).
GBD_DIR = os.path.join(DATA_IN, "GBD")
GBD_DIRECT = {
    "us": {},
    "br": {"Piauí": "Piaui"},
    "mx": {"Veracruz": "Veracruz de Ignacio de la Llave", "Michoacán": "Michoacán de Ocampo"},
    # Iran (GBD has province-level values); aliases bridge transliteration. The two Azarbaijan
    # provinces stay grey — GBD only has "Azerbaijan" (the country), not the Iranian provinces.
    "ir": {"Esfahan": "Isfahan", "Kordestan": "Kurdistan", "Razavi Khorasan": "Khorasan-e-Razavi",
           "Sistan and Baluchestan": "Sistan and Baluchistan",
           "Chahar Mahall and Bakhtiari": "Chahar Mahaal and Bakhtiari",
           "Kohgiluyeh and Buyer Ahmad": "Kohgiluyeh and Boyer-Ahmad"},
}
_GBD_PREV60 = None


def load_gbd_prev60():
    """{location_name: prevalence fraction among 60+} from GBD 2023 export (Prevalence, Percent,
       age '60+ years'). Cached. Returns {} if no CSV present."""
    global _GBD_PREV60
    if _GBD_PREV60 is not None:
        return _GBD_PREV60
    import csv
    import glob
    out = {}
    files = sorted(glob.glob(os.path.join(GBD_DIR, "*.csv")))
    if files:
        with open(files[0], newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if (row.get("measure_name") == "Prevalence" and row.get("metric_name") == "Percent"
                        and row.get("age_name") == "60+ years"):
                    try:
                        out[row["location_name"]] = float(row["val"])
                    except (TypeError, ValueError, KeyError):
                        pass
    _GBD_PREV60 = out
    return out


def build_gbd_direct(cc, name, units, aliases=None):
    """Admin-1 dementia prevalence among 60+ (%) straight from the GBD 2023 export: match each
       boundary name (or alias) to a GBD location -> dementia/{cc}-modelled.json. True if built."""
    prev = load_gbd_prev60()
    if not prev:
        return False
    aliases = aliases or {}
    by_unit, unmatched = {}, []
    for u in units:
        nm = u.get("name")
        loc = nm if nm in prev else aliases.get(nm) or aliases.get(u.get("code"))
        if loc and loc in prev:
            by_unit[u["code"]] = round(prev[loc] * 100, 2)
        elif nm:
            unmatched.append(nm)
    if not by_unit:
        return False
    write_json(f"dementia/{cc}-modelled.json", {
        "meta": {"metric": "modelled dementia prevalence among residents aged 60+ (%)",
                 "age_group": "60+", "source": "GBD 2023 (IHME), Alzheimer's disease & other dementias",
                 "note": "MODELLED estimate from GBD 2023; IHME Free-of-Charge Non-Commercial licence",
                 "built": BUILD_DATE},
        "byTown": by_unit})
    log(f"  {name}: GBD-direct prevalence (60+) for {len(by_unit)} units"
        + (f"  (UNMATCHED: {unmatched})" if unmatched else ""))
    return True


# ---------- Japan / Korea admin-1 prevalence: national e-Stat/KOSIS pop-by-age × GBD rates ----------
# GBD has no sub-national data for JP/KR, so we model each prefecture/province's % among 60+ from its
# OWN age structure: prevalence = Σ(age-band pop × GBD national age-band rate) / 60+ population.
POP60_BANDS = ["60_64", "65_69", "70_74", "75_79", "80_84", "85p"]
# band -> GBD age_name. Includes both an 85+ split (85p, used by JP/KR e-Stat/KOSIS) and an 80+ top
# band (80p, used by WorldPop whose oldest band is 80+); build_pop_rate_prev60 uses whichever the
# population dict actually carries.
GBD_AGE_NAME = {"60_64": "60-64 years", "65_69": "65-69 years", "70_74": "70-74 years",
                "75_79": "75-79 years", "80_84": "80-84 years", "85p": "85+ years",
                "80p": "80+ years"}


def load_gbd_rates(country):
    """{band: fraction} GBD 2023 dementia Prevalence(Percent) for `country`, bands 60-64..85+."""
    import csv
    import glob
    files = sorted(glob.glob(os.path.join(GBD_DIR, "*.csv")))
    if not files:
        return {}
    want = {v: k for k, v in GBD_AGE_NAME.items()}
    out = {}
    with open(files[0], newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if (row.get("location_name") == country and row.get("measure_name") == "Prevalence"
                    and row.get("metric_name") == "Percent" and row.get("age_name") in want):
                try:
                    out[want[row["age_name"]]] = float(row["val"])
                except (TypeError, ValueError):
                    pass
    return out


def load_estat_jp_pop():
    """Japan e-Stat 社会・人口統計体系 xlsx in _data_in/Japan Population/ -> {JP-NN: {total, bands}}.
       80-84 = A1418(80+) − A1420(85+); 85p = A1420(85+); 60-64..75-79 = A1213..A1216."""
    import glob
    try:
        import pandas as pd
    except ImportError:
        return None
    files = glob.glob(os.path.join(DATA_IN, "Japan Population", "*.xlsx"))
    if not files:
        return None
    df = pd.ExcelFile(files[0]).parse("1", header=None)
    codes = df.iloc[7]
    col = {str(codes[c]).strip(): c for c in range(3, df.shape[1])}

    def num(row, code):
        v = str(row[col[code]]).replace(",", "")
        return float(v) if v not in ("nan", "-", "") else 0.0

    out = {}
    for r in range(10, df.shape[0]):
        area = str(df.iloc[r, 0]).strip()
        if not area.isdigit() or area == "00000":
            continue
        row = df.iloc[r]
        out["JP-%02d" % int(area[:2])] = {
            "total": num(row, "A1101"), "60_64": num(row, "A1213"), "65_69": num(row, "A1214"),
            "70_74": num(row, "A1215"), "75_79": num(row, "A1216"),
            "80_84": num(row, "A1418") - num(row, "A1420"), "85p": num(row, "A1420")}
    return out or None


def load_kosis_kr_pop():
    """Korea KOSIS DT_1B04005N xlsx in _data_in/Korea Population/ -> {KR-NN: {total, bands}}.
       Uses the latest month column block; 85p = 85-89+90-94+95-99+100+."""
    import glob
    try:
        import pandas as pd
    except ImportError:
        return None
    files = glob.glob(os.path.join(DATA_IN, "Korea Population", "*.xlsx"))
    if not files:
        return None
    df = pd.ExcelFile(files[0]).parse("Data", header=None)
    labels = df.iloc[1]

    def kcol(label):
        idxs = [c for c in range(df.shape[1]) if str(labels[c]).strip() == label]
        return idxs[-1] if idxs else None

    age = {"60_64": "60-64 Years old", "65_69": "65-69 Years old", "70_74": "70-74 Years old",
           "75_79": "75-79 Years old", "80_84": "80-84 Years old", "85_89": "85-89 Years old",
           "90_94": "90-94 Years old", "95_99": "95-99 Years old", "100p": "100 Years old & over"}
    kc = {k: kcol(v) for k, v in age.items()}
    kmap = [("Seoul", "KR-11"), ("Busan", "KR-26"), ("Daegu", "KR-27"), ("Incheon", "KR-28"),
            ("Gwangju", "KR-29"), ("Daejeon", "KR-30"), ("Ulsan", "KR-31"), ("Sejong", "KR-50"),
            ("Gyeonggi", "KR-41"), ("Gangwon", "KR-42"), ("Chungcheongbuk", "KR-43"),
            ("Chungcheongnam", "KR-44"), ("Jeollabuk", "KR-45"), ("Jeollanam", "KR-46"),
            ("Gyeongsangbuk", "KR-47"), ("Gyeongsangnam", "KR-48"), ("Jeju", "KR-49")]

    def num(row, c):
        v = str(row[c]).replace(",", "")
        return float(v) if v not in ("nan", "-", "") else 0.0

    out = {}
    for r in range(2, df.shape[0]):
        if str(df.iloc[r, 1]).strip() != "Population (Person)":
            continue
        name = str(df.iloc[r, 0]).strip()
        code = next((c for key, c in kmap if key.lower() in name.lower()), None)
        if not code:
            continue
        row = df.iloc[r]
        out[code] = {"60_64": num(row, kc["60_64"]), "65_69": num(row, kc["65_69"]),
                     "70_74": num(row, kc["70_74"]), "75_79": num(row, kc["75_79"]),
                     "80_84": num(row, kc["80_84"]),
                     "85p": num(row, kc["85_89"]) + num(row, kc["90_94"]) + num(row, kc["95_99"]) + num(row, kc["100p"])}
        out[code]["total"] = sum(out[code][b] for b in POP60_BANDS)
    return out or None


# Korea's own official estimate replaces GBD×KOSIS for the KR layer: 보건복지부/중앙치매센터
# 「시군구별 치매현황」 (data.go.kr #15073342, KOGL Type 1 = attribution only). Its yearly figure is
# the 2023 national dementia epidemiological survey's sex×age rates × that year's mid-year resident
# population, so it is still an estimate, but on Korean survey rates rather than GBD's.
KR_NID_URL = "https://www.data.go.kr/data/15073342/fileData.do"
KR_SIDO = {"서울특별시": "KR-11", "부산광역시": "KR-26", "대구광역시": "KR-27", "인천광역시": "KR-28",
           "광주광역시": "KR-29", "대전광역시": "KR-30", "울산광역시": "KR-31", "세종특별자치시": "KR-50",
           "경기도": "KR-41", "강원도": "KR-42", "강원특별자치도": "KR-42", "충청북도": "KR-43",
           "충청남도": "KR-44", "전라북도": "KR-45", "전북특별자치도": "KR-45", "전라남도": "KR-46",
           "경상북도": "KR-47", "경상남도": "KR-48", "제주특별자치도": "KR-49"}
KR_NID_BAND = {"60-64세": "60_64", "65-69세": "65_69", "70-74세": "70_74", "75-79세": "75_79",
               "80-84세": "80_84", "85세이상": "85p"}


def build_kr_nid_prev60(units):
    """_data_in/kr_nid/*.csv (cp949; one row per year×시도×시군구×sex×age band) -> kr-modelled.json.
       Uses the latest year, sex=전체, and each 시도's own total row (시군구 == 시도), so 구 sub-rows
       inside 시 are never double-counted. Labels drift between years ('60세 이상' vs '60세이상',
       '6.83%' vs '6.83'), hence the space/percent stripping. Returns True if built."""
    import csv
    import glob
    files = sorted(glob.glob(os.path.join(DATA_IN, "kr_nid", "*.csv")))
    if not files:
        return False
    with open(files[-1], encoding="cp949", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["성별"] == "전체" and r["시군구"] == r["시도"]]
    if not rows:
        return False
    year = max(r["연도"] for r in rows)
    pct = lambda r: float(r["추정치매환자유병률"].replace("%", ""))
    age = lambda r: r["연령별"].replace(" ", "")
    codes = {u["code"] for u in units}
    by_unit, national, bands = {}, None, []
    for r in rows:
        if r["연도"] != year:
            continue
        if r["시도"] == "전국":
            if age(r) == "60세이상":
                national = round(pct(r), 1)
            elif age(r) in KR_NID_BAND:
                bands.append({"band": KR_NID_BAND[age(r)], "prevalence_pct": round(pct(r), 2)})
        elif age(r) == "60세이상" and KR_SIDO.get(r["시도"]) in codes:
            by_unit[KR_SIDO[r["시도"]]] = round(pct(r), 2)
    if len(by_unit) < 17 or national is None:
        log(f"  South Korea: kr_nid CSV gave {len(by_unit)}/17 provinces for {year} -> fallback")
        return False
    bands.sort(key=lambda b: POP60_BANDS.index(b["band"]))
    src = f"보건복지부·중앙치매센터 시군구별 치매현황 {year} (data.go.kr #15073342)"
    write_json("dementia/kr-modelled.json", {
        "meta": {"metric": "estimated dementia prevalence among residents aged 60+ (%)",
                 "age_group": "60+", "resolution": "admin1", "year": int(year),
                 "source": src, "url": KR_NID_URL,
                 "note": (f"ESTIMATE by Korea MOHW/National Institute of Dementia: 2023 national "
                          f"dementia epidemiological survey sex×age rates × {year} mid-year resident "
                          "population; KOGL Type 1 (attribution)"),
                 "national_prevalence_60plus_pct": national,
                 "age_bands": bands, "built": BUILD_DATE},
        "byTown": by_unit})
    log(f"  South Korea: NID official estimate {year} (60+) for {len(by_unit)} provinces, national {national}%")
    return True


def build_pop_rate_prev60(cc, name, gbd_country, units, pop, pop_source=None):
    """Model admin-1 dementia prevalence among 60+ (%) = Σ(band pop × GBD band rate) / 60+ pop.
       Age bands are read from the pop dict (keys minus 'total'), so this serves both the JP/KR
       e-Stat/KOSIS path (…80_84, 85p) and the WorldPop path (…75_79, 80p). -> {cc}-modelled.json."""
    rates = load_gbd_rates(gbd_country)
    if not pop:
        return False
    bands = [b for b in next(iter(pop.values())) if b != "total"]
    if not all(b in rates for b in bands):
        return False
    src = pop_source or f"{name} official population by age"
    codes = {u["code"] for u in units}
    by_unit, nat_cases, nat_pop = {}, 0.0, 0.0
    for code, p in pop.items():
        if code not in codes:
            continue
        pop60 = sum(p[b] for b in bands)
        if not pop60:
            continue
        cases = sum(p[b] * rates[b] for b in bands)
        by_unit[code] = round(cases / pop60 * 100, 2)
        nat_cases += cases
        nat_pop += pop60
    if not by_unit:
        return False
    national = round(nat_cases / nat_pop * 100, 1) if nat_pop else None
    # Some population products (WorldPop for most high-income countries) apply a single national
    # age-proportion surface, so every admin-1 unit resolves to the same age composition and the
    # "% among 60+" is spatially flat. When the sub-national range is negligible we honestly present
    # one national value rather than a fake choropleth. Real sub-national data (JP/KR census, and
    # WorldPop for countries with sub-national age inputs) keeps its per-unit spread.
    vals = list(by_unit.values())
    resolution = "admin1"
    if national is not None and max(vals) - min(vals) < 0.15:
        resolution = "national"
        by_unit = {c: national for c in by_unit}
    note = f"MODELLED estimate; GBD 2023 (IHME, non-commercial) × {src}"
    if resolution == "national":
        note += ("; sub-national age structure not resolved by this population product "
                 "— single national estimate shown")
    age_bands = [{"band": b, "prevalence_pct": round(rates[b] * 100, 2)} for b in bands]
    write_json(f"dementia/{cc}-modelled.json", {
        "meta": {"metric": "modelled dementia prevalence among residents aged 60+ (%)",
                 "age_group": "60+",
                 "resolution": resolution,
                 "source": f"GBD 2023 age-specific rates × {src}",
                 "note": note,
                 "national_prevalence_60plus_pct": national,
                 "age_bands": age_bands, "built": BUILD_DATE},
        "byTown": by_unit})
    log(f"  {name}: pop×rate prevalence (60+, {len(bands)} bands, {resolution}) for {len(by_unit)} units")
    return True


# 18 remaining countries with no sub-national GBD data: GBD national rate × WorldPop admin-1 pop.
# cc -> (WorldPop/ISO3, GBD national location name).
WORLDPOP = {
    "th": ("THA", "Thailand"), "vn": ("VNM", "Viet Nam"), "id": ("IDN", "Indonesia"),
    "ph": ("PHL", "Philippines"), "my": ("MYS", "Malaysia"), "pk": ("PAK", "Pakistan"),
    "bd": ("BGD", "Bangladesh"), "mm": ("MMR", "Myanmar"), "gb": ("GBR", "United Kingdom"),
    "de": ("DEU", "Germany"), "fr": ("FRA", "France"), "it": ("ITA", "Italy"),
    "es": ("ESP", "Spain"), "pl": ("POL", "Poland"), "ca": ("CAN", "Canada"),
    "au": ("AUS", "Australia"), "nz": ("NZL", "New Zealand"), "tr": ("TUR", "Türkiye"),
}
WORLDPOP_DIR = os.path.join(DATA_IN, "WorldPop")
WP_BANDS_AGE = {"60_64": "60", "65_69": "65", "70_74": "70", "75_79": "75", "80p": "80"}  # band->age code


def load_worldpop_pop(cc, iso3):
    """WorldPop 1km unconstrained 2020 age/sex rasters (cached in _data_in/WorldPop/{ISO3}/) zonal-
       summed onto the committed {cc}-admin1.geojson -> {unit_code: {band: pop, 'total': pop}} (m+f).
       80p = WorldPop's 80+ top band. Downloads any missing raster; None if rasterstats absent or a
       fetch fails."""
    try:
        from rasterstats import zonal_stats
    except ImportError:
        log("  rasterstats not installed; WorldPop path skipped")
        return None
    geo_path = os.path.join(OUT, "geo", f"{cc}-admin1.geojson")
    if not os.path.exists(geo_path):
        return None
    feats = json.load(open(geo_path, encoding="utf-8"))["features"]
    codes = [f["properties"]["code"] for f in feats]
    wp_dir = os.path.join(WORLDPOP_DIR, iso3)
    os.makedirs(wp_dir, exist_ok=True)
    base = ("https://data.worldpop.org/GIS/AgeSex_structures/Global_2000_2020_1km/"
            f"unconstrained/2020/{iso3}")
    pop = {c: {b: 0.0 for b in WP_BANDS_AGE} for c in codes}
    for band, age in WP_BANDS_AGE.items():
        for sex in ("m", "f"):
            fn = f"{iso3.lower()}_{sex}_{age}_2020_1km.tif"
            fp = os.path.join(wp_dir, fn)
            if not os.path.exists(fp):
                data = http_get(f"{base}/{fn}")
                if not data:
                    log(f"  WorldPop fetch failed: {fn}")
                    return None
                with open(fp, "wb") as f:
                    f.write(data)
            # all_touched: count any pixel intersecting the polygon so small islands / tiny units
            # (e.g. Philippine island provinces) aren't dropped for having no pixel centre inside.
            for c, z in zip(codes, zonal_stats(feats, fp, stats="sum", all_touched=True)):
                pop[c][band] += (z["sum"] or 0.0)
    for c in pop:
        pop[c]["total"] = sum(pop[c][b] for b in WP_BANDS_AGE)
    return pop


def build_direct_prevalence(cc, name, units, meta_extra=None):
    """Published per-admin prevalence (e.g. China Liu/Gao 2024 provinces, India Lee 2023
       states): the study already gives a final value per unit — no rates×pop modelling.
       Reads _data_in/{cc}-admin1-prevalence.csv with columns:
         unit_code (ISO like CN-XJ / IN-RJ)  OR  unit_name (matched to the boundary name),
         prevalence (or prevalence_pct)  [, age_group, note]
       -> dementia/{cc}-modelled.json (byTown: code -> value). Returns True if built."""
    path = os.path.join(DATA_IN, f"{cc}-admin1-prevalence.csv")
    if not os.path.exists(path):
        return False
    codes = {u["code"] for u in units}
    by_name = {_norm_name(u["name"]): u["code"] for u in units}
    by_local = {_norm_name(u["nameLocal"]): u["code"] for u in units}
    by_unit, unmatched = {}, []
    for row in _read_csv(path):
        code = (row.get("unit_code") or "").strip()
        nm = (row.get("unit_name") or "").strip()
        pv = row.get("prevalence") if row.get("prevalence") not in (None, "") else row.get("prevalence_pct")
        if pv in (None, ""):
            continue
        c = code if code in codes else by_name.get(_norm_name(nm)) or by_local.get(_norm_name(nm))
        if not c:
            unmatched.append(nm or code)
            continue
        by_unit[c] = round(float(pv), 2)
    if not by_unit:
        log(f"  {name}: prevalence CSV present but 0 rows matched (unmatched: {unmatched})")
        return False
    meta = {"metric": "published per-admin dementia prevalence",
            "note": "MODELLED estimate from a published study, not measured",
            "built": BUILD_DATE}
    if meta_extra:
        meta.update(meta_extra)
    write_json(f"dementia/{cc}-modelled.json", {"meta": meta, "byTown": by_unit})
    log(f"  {name}: DIRECT prevalence for {len(by_unit)} units"
        + (f"  (UNMATCHED, add to boundary aliases: {unmatched})" if unmatched else ""))
    return True


# ---------- world country-level PM2.5 (for the residence country dropdown) ----------
WORLD_CSV = ("https://satpmdata.s3.us-east-1.amazonaws.com/V6GL03/RegionSummaries/"
             "GlobalPM25-V6GL03-Annual-1998-2024-wThresFrac.csv")


def build_world_country_pm25():
    """Parse ACAG global country summary CSV -> {country: {values:[YEAR0..YEAR1]}}
       using the Population-Weighted PM2.5 column."""
    import csv
    log("Fetching ACAG global country PM2.5 summary…")
    raw = http_get(WORLD_CSV).decode("utf-8", "replace")
    rd = csv.reader(io.StringIO(raw))
    header = next(rd)  # Region, Year, Population-Weighted PM2.5 [ug/m3], ...
    ri, yi, pi = 0, 1, 2
    tmp = {}
    for row in rd:
        if len(row) <= pi:
            continue
        region = row[ri].strip()
        try:
            year, pm = int(row[yi]), float(row[pi])
        except ValueError:
            continue
        if YEAR0 <= year <= YEAR1:
            tmp.setdefault(region, {})[year] = round(pm, 1)
    out = {}
    for region, ys in tmp.items():
        vals = [ys.get(y) for y in range(YEAR0, YEAR1 + 1)]
        if any(v is not None for v in vals):
            out[region] = {"values": vals}
    log(f"  {len(out)} countries")
    return out


# ---------- write ----------
def write_json(rel, obj):
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    log(f"  wrote {rel}  ({os.path.getsize(path)//1024} KB)")


# ---------- Exposome: modifiable risk factors + composite PAF (national, adult) ----------
# Livingston 2024 Lancet Commission relative risks (verified from Table 1). Per factor
# PAF = P(RR-1)/(1+P(RR-1)); composite = 1 - Π(1-PAF) over factors present (independence-assumed — a
# documented modest over-estimate). HTN/diabetes/obesity from NCD-RisC (adult age-std, incl. Taiwan
# natively); smoking/inactivity from WHO GHO + Taiwan 2021 NHIS override (GHO has no Taiwan row).
EXPO_RR = {"hypertension": 1.2, "diabetes": 1.7, "obesity": 1.3, "smoking": 1.3, "physical_inactivity": 1.2}
# ISO alpha-3 -> alpha-2 for the 200 NCD-RisC countries (World Bank iso2 + Taiwan/Pacific, which WB omits).
# The exposome is built globally off the NCD-RisC ISO column; this maps to the iso2 keys the frontend uses.
ISO3_ISO2 = {
    "AFG":"AF","AGO":"AO","ALB":"AL","AND":"AD","ARE":"AE","ARG":"AR","ARM":"AM","ASM":"AS","ATG":"AG",
    "AUS":"AU","AUT":"AT","AZE":"AZ","BDI":"BI","BEL":"BE","BEN":"BJ","BFA":"BF","BGD":"BD","BGR":"BG",
    "BHR":"BH","BHS":"BS","BIH":"BA","BLR":"BY","BLZ":"BZ","BMU":"BM","BOL":"BO","BRA":"BR","BRB":"BB",
    "BRN":"BN","BTN":"BT","BWA":"BW","CAF":"CF","CAN":"CA","CHE":"CH","CHL":"CL","CHN":"CN","CIV":"CI",
    "CMR":"CM","COD":"CD","COG":"CG","COK":"CK","COL":"CO","COM":"KM","CPV":"CV","CRI":"CR","CUB":"CU",
    "CYP":"CY","CZE":"CZ","DEU":"DE","DJI":"DJ","DMA":"DM","DNK":"DK","DOM":"DO","DZA":"DZ","ECU":"EC",
    "EGY":"EG","ERI":"ER","ESP":"ES","EST":"EE","ETH":"ET","FIN":"FI","FJI":"FJ","FRA":"FR","FSM":"FM",
    "GAB":"GA","GBR":"GB","GEO":"GE","GHA":"GH","GIN":"GN","GMB":"GM","GNB":"GW","GNQ":"GQ","GRC":"GR",
    "GRD":"GD","GRL":"GL","GTM":"GT","GUY":"GY","HND":"HN","HRV":"HR","HTI":"HT","HUN":"HU","IDN":"ID",
    "IND":"IN","IRL":"IE","IRN":"IR","IRQ":"IQ","ISL":"IS","ISR":"IL","ITA":"IT","JAM":"JM","JOR":"JO",
    "JPN":"JP","KAZ":"KZ","KEN":"KE","KGZ":"KG","KHM":"KH","KIR":"KI","KNA":"KN","KOR":"KR","KWT":"KW",
    "LAO":"LA","LBN":"LB","LBR":"LR","LBY":"LY","LCA":"LC","LKA":"LK","LSO":"LS","LTU":"LT","LUX":"LU",
    "LVA":"LV","MAR":"MA","MDA":"MD","MDG":"MG","MDV":"MV","MEX":"MX","MHL":"MH","MKD":"MK","MLI":"ML",
    "MLT":"MT","MMR":"MM","MNE":"ME","MNG":"MN","MOZ":"MZ","MRT":"MR","MUS":"MU","MWI":"MW","MYS":"MY",
    "NAM":"NA","NER":"NE","NGA":"NG","NIC":"NI","NIU":"NU","NLD":"NL","NOR":"NO","NPL":"NP","NRU":"NR",
    "NZL":"NZ","OMN":"OM","PAK":"PK","PAN":"PA","PER":"PE","PHL":"PH","PLW":"PW","PNG":"PG","POL":"PL",
    "PRI":"PR","PRK":"KP","PRT":"PT","PRY":"PY","PSE":"PS","PYF":"PF","QAT":"QA","ROU":"RO","RUS":"RU",
    "RWA":"RW","SAU":"SA","SDN":"SD","SEN":"SN","SGP":"SG","SLB":"SB","SLE":"SL","SLV":"SV","SOM":"SO",
    "SRB":"RS","SSD":"SS","STP":"ST","SUR":"SR","SVK":"SK","SVN":"SI","SWE":"SE","SWZ":"SZ","SYC":"SC",
    "SYR":"SY","TCD":"TD","TGO":"TG","THA":"TH","TJK":"TJ","TKL":"TK","TKM":"TM","TLS":"TL","TON":"TO",
    "TTO":"TT","TUN":"TN","TUR":"TR","TUV":"TV","TWN":"TW","TZA":"TZ","UGA":"UG","UKR":"UA","URY":"UY",
    "USA":"US","UZB":"UZ","VCT":"VC","VEN":"VE","VNM":"VN","VUT":"VU","WSM":"WS","YEM":"YE","ZAF":"ZA",
    "ZMB":"ZM","ZWE":"ZW",
}


def _paf(p_frac, rr):
    return p_frac * (rr - 1) / (1 + p_frac * (rr - 1))


def _ncdrisc_latest(fname, field_substr):
    """{iso3: (year, both-sexes fraction)} at the latest year, mean of the Men/Women rows, for EVERY
       country in the file. field_substr picks the point-estimate column (first header containing it,
       before its CIs); ISO/Sex/Year columns are found by header name (files differ in column order)."""
    import csv
    with io.open(os.path.join(DATA_IN, "ncdrisc", fname), encoding="utf-8-sig") as f:
        r = csv.reader(f); hdr = next(r)
        pi = next(i for i, h in enumerate(hdr) if field_substr in h)
        ii = next(i for i, h in enumerate(hdr) if h.strip().lower() == "iso")
        si = next(i for i, h in enumerate(hdr) if h.strip().lower() == "sex")
        yi = next(i for i, h in enumerate(hdr) if h.strip().lower() == "year")
        acc = {}   # iso3 -> {year: {sex: value}}
        for row in r:
            iso = row[ii].strip('"')
            acc.setdefault(iso, {}).setdefault(int(float(row[yi])), {})[row[si].strip('"')] = float(row[pi])
    return {iso: (max(byyr), sum(byyr[max(byyr)].values()) / len(byyr[max(byyr)])) for iso, byyr in acc.items()}


def _who_gho_latest(indicator, iso3s):
    """{iso3: (year, percent)} latest OBSERVED both-sexes value; drops projections beyond this year
       (e.g. the tobacco feed's 2030 forecast)."""
    rows = json.loads(http_get(f"https://ghoapi.azureedge.net/api/{indicator}").decode("utf-8"))["value"]
    cur = int(BUILD_DATE[:4]); best = {}
    for x in rows:
        iso = x.get("SpatialDim"); v = x.get("NumericValue"); y = x.get("TimeDim")
        if iso not in iso3s or x.get("Dim1") not in ("SEX_BTSX", None) or v is None or y is None:
            continue
        y = int(y)
        if y <= cur and (iso not in best or y > best[iso][0]):
            best[iso] = (y, float(v))
    return best


def build_exposome():
    """National modifiable-risk-factor prevalence + PAF + composite for EVERY NCD-RisC country (~200) ->
       public/data/exposome/exposome.json (one national value per country, keyed by ISO alpha-2).
       HTN/diabetes/obesity from NCD-RisC (all 200 natively, incl. Taiwan); smoking/inactivity from
       WHO GHO where available (+ Taiwan 2021 NHIS, since GHO has no Taiwan row)."""
    htn = _ncdrisc_latest("NCD_RisC_Lancet_2017_BP_age_standardised_countries.csv", "raised blood pressure")
    dm  = _ncdrisc_latest("NCD_RisC_Lancet_2024_Diabetes_age_standardised_countries.csv", "diabetes (18+")
    obe = _ncdrisc_latest("NCD_RisC_Nature_2026_BMI_age_standardised_country.csv", "BMI>=30")
    universe = set(htn) | set(dm) | set(obe)                    # ~200 ISO alpha-3
    smk = _who_gho_latest("M_Est_tob_curr", universe)
    pac = _who_gho_latest("NCD_PAC", universe)
    twf = json.load(io.open(os.path.join(DATA_IN, "tw-riskfactors.json"), encoding="utf-8"))["factors"]
    SRC = {"hypertension": "NCD-RisC raised BP (adult, age-std)", "diabetes": "NCD-RisC diabetes 18+ (age-std)",
           "obesity": "NCD-RisC BMI≥30 (adult, age-std)", "smoking": "WHO GHO current tobacco (age-std)",
           "physical_inactivity": "WHO GHO insufficient activity"}
    out = {}
    for iso3 in sorted(universe):
        cc = ISO3_ISO2.get(iso3)
        if not cc:
            continue                                            # no known iso2 mapping — skip
        cc = cc.lower(); pv = {}                                # factor -> (percent, year, source)
        if iso3 in htn: pv["hypertension"] = (round(htn[iso3][1] * 100, 1), htn[iso3][0], SRC["hypertension"])
        if iso3 in dm:  pv["diabetes"]     = (round(dm[iso3][1] * 100, 1),  dm[iso3][0],  SRC["diabetes"])
        if iso3 in obe: pv["obesity"]      = (round(obe[iso3][1] * 100, 1), obe[iso3][0], SRC["obesity"])
        if iso3 == "TWN":
            for f in ("smoking", "physical_inactivity"):
                pv[f] = (twf[f]["value_pct"], twf[f]["year"], "2021 Taiwan NHIS")
        else:
            if iso3 in smk: pv["smoking"] = (round(smk[iso3][1], 1), smk[iso3][0], SRC["smoking"])
            if iso3 in pac: pv["physical_inactivity"] = (round(pac[iso3][1], 1), pac[iso3][0], SRC["physical_inactivity"])
        factors, keep = {}, 1.0
        for f, rr in EXPO_RR.items():
            if f not in pv:
                continue
            p, yr, src = pv[f]; paf = _paf(p / 100.0, rr)
            factors[f] = {"prev_pct": p, "rr": rr, "paf_pct": round(paf * 100, 1), "year": yr, "source": src}
            keep *= (1 - paf)
        out[cc] = {"factors": factors, "composite_paf_pct": round((1 - keep) * 100, 1), "n_factors": len(factors)}
    meta = {"metric": "modifiable dementia risk factors — national adult prevalence + PAF",
            "note": ("PAF=P(RR-1)/(1+P(RR-1)); composite=1-Π(1-PAF) over factors present, independence-assumed. "
                     "Up to 5 of the Livingston 2024 modifiable factors at national level — a floor, not the full ~45%. "
                     "n_factors varies: NCD-RisC covers all ~200 for BP/diabetes/obesity; WHO GHO smoking/inactivity may be absent for some."),
            "rr_source": "Livingston et al., Lancet 2024 Commission (Table 1)", "built": BUILD_DATE}
    write_json("exposome/exposome.json", {"meta": meta, "countries": out})
    log(f"  exposome: {len(out)} countries × up to 5 factors + composite PAF")
    return True


# MCI (mild cognitive impairment) prevalence. GBD does not produce MCI, so there is no single global
# per-country dataset; instead Bai et al. 2022 (Age & Ageing, afac173) is a systematic review +
# meta-analysis giving pooled MCI prevalence by WORLD BANK REGION (adults 50+, community-dwelling). We
# use that as a harmonised regional baseline for every country, and override with a NATIONAL study where
# a strong one exists. MCI criteria/age bands still vary → a modelled estimate, indicative only.
BAI_MCI = {"EAS": 19.0, "ECS": 10.9, "LCN": 18.3, "MEA": 13.0, "NAC": 15.5, "SAS": 13.9, "SSF": 3.8}
BAI_SRC = "Bai et al. 2022, Age & Ageing (WB-region meta-analysis, 50+)"
BAI_URL = "https://pubmed.ncbi.nlm.nih.gov/35977150/"
WB_REGION = {   # iso2 -> World Bank region id (from the World Bank country metadata)
    "AD":"ECS","AE":"MEA","AF":"MEA","AG":"LCN","AL":"ECS","AM":"ECS","AO":"SSF","AR":"LCN","AS":"EAS",
    "AT":"ECS","AU":"EAS","AW":"LCN","AZ":"ECS","BA":"ECS","BB":"LCN","BD":"SAS","BE":"ECS","BF":"SSF",
    "BG":"ECS","BH":"MEA","BI":"SSF","BJ":"SSF","BM":"NAC","BN":"EAS","BO":"LCN","BR":"LCN","BS":"LCN",
    "BT":"SAS","BW":"SSF","BY":"ECS","BZ":"LCN","CA":"NAC","CD":"SSF","CF":"SSF","CG":"SSF","CH":"ECS",
    "CI":"SSF","CL":"LCN","CM":"SSF","CN":"EAS","CO":"LCN","CR":"LCN","CU":"LCN","CV":"SSF","CW":"LCN",
    "CY":"ECS","CZ":"ECS","DE":"ECS","DJ":"MEA","DK":"ECS","DM":"LCN","DO":"LCN","DZ":"MEA","EC":"LCN",
    "EE":"ECS","EG":"MEA","ER":"SSF","ES":"ECS","ET":"SSF","FI":"ECS","FJ":"EAS","FM":"EAS","FO":"ECS",
    "FR":"ECS","GA":"SSF","GB":"ECS","GD":"LCN","GE":"ECS","GH":"SSF","GI":"ECS","GL":"ECS","GM":"SSF",
    "GN":"SSF","GQ":"SSF","GR":"ECS","GT":"LCN","GU":"EAS","GW":"SSF","GY":"LCN","HK":"EAS","HN":"LCN",
    "HR":"ECS","HT":"LCN","HU":"ECS","ID":"EAS","IE":"ECS","IL":"MEA","IM":"ECS","IN":"SAS","IQ":"MEA",
    "IR":"MEA","IS":"ECS","IT":"ECS","JG":"ECS","JM":"LCN","JO":"MEA","JP":"EAS","KE":"SSF","KG":"ECS",
    "KH":"EAS","KI":"EAS","KM":"SSF","KN":"LCN","KP":"EAS","KR":"EAS","KW":"MEA","KY":"LCN","KZ":"ECS",
    "LA":"EAS","LB":"MEA","LC":"LCN","LI":"ECS","LK":"SAS","LR":"SSF","LS":"SSF","LT":"ECS","LU":"ECS",
    "LV":"ECS","LY":"MEA","MA":"MEA","MC":"ECS","MD":"ECS","ME":"ECS","MF":"LCN","MG":"SSF","MH":"EAS",
    "MK":"ECS","ML":"SSF","MM":"EAS","MN":"EAS","MO":"EAS","MP":"EAS","MR":"SSF","MT":"MEA","MU":"SSF",
    "MV":"SAS","MW":"SSF","MX":"LCN","MY":"EAS","MZ":"SSF","NA":"SSF","NC":"EAS","NE":"SSF","NG":"SSF",
    "NI":"LCN","NL":"ECS","NO":"ECS","NP":"SAS","NR":"EAS","NZ":"EAS","OM":"MEA","PA":"LCN","PE":"LCN",
    "PF":"EAS","PG":"EAS","PH":"EAS","PK":"MEA","PL":"ECS","PR":"LCN","PS":"MEA","PT":"ECS","PW":"EAS",
    "PY":"LCN","QA":"MEA","RO":"ECS","RS":"ECS","RU":"ECS","RW":"SSF","SA":"MEA","SB":"EAS","SC":"SSF",
    "SD":"SSF","SE":"ECS","SG":"EAS","SI":"ECS","SK":"ECS","SL":"SSF","SM":"ECS","SN":"SSF","SO":"SSF",
    "SR":"LCN","SS":"SSF","ST":"SSF","SV":"LCN","SX":"LCN","SY":"MEA","SZ":"SSF","TC":"LCN","TD":"SSF",
    "TG":"SSF","TH":"EAS","TJ":"ECS","TL":"EAS","TM":"ECS","TN":"MEA","TO":"EAS","TR":"ECS","TT":"LCN",
    "TV":"EAS","TZ":"SSF","UA":"ECS","UG":"SSF","US":"NAC","UY":"LCN","UZ":"ECS","VC":"LCN","VE":"LCN",
    "VG":"LCN","VI":"LCN","VN":"EAS","VU":"EAS","WS":"EAS","XK":"ECS","YE":"MEA","ZA":"SSF","ZM":"SSF",
    "ZW":"SSF", "TW":"EAS",
}
# National-study overrides (higher confidence than the regional baseline; methods differ per country).
MCI_NATIONAL = {
    "tw": {"prev_pct": 18.7, "year": 2013, "age": "65+", "crit": "門到門全國調查 / door-to-door",
           "src": "Sun et al. 2014 / 2017 全國調查", "url": "https://doi.org/10.1371/journal.pone.0175475"},
    "jp": {"prev_pct": 15.5, "year": 2022, "age": "65+", "crit": "厚労省推計 / MHLW estimate",
           "src": "MHLW 2024（二宮研究班）", "url": "https://www.mhlw.go.jp/content/001279920.pdf"},
    "kr": {"prev_pct": 22.7, "year": 2020, "age": "65+", "crit": "全國調查 NaSDEK / KDO",
           "src": "Korea Dementia Observatory 2020", "url": "https://www.nid.or.kr"},
    "in": {"prev_pct": 17.6, "year": 2024, "age": "60+", "crit": "DSM-5 mild NCD",
           "src": "LASI-DAD (Lee et al.)", "url": "https://doi.org/10.1002/alz.12928"},
    "cn": {"prev_pct": 15.4, "year": 2021, "age": "55+", "crit": "全國統合分析 / national meta",
           "src": "Xue et al. 2021, Neuroepidemiology (China meta)", "url": "https://doi.org/10.1159/000512597"},
}


SOURCES_JSON = os.path.join(ROOT, "scripts/mci-scd-sources.json")   # cloud deep-research (97 countries)
MCI_PLAUSIBLE = (3.0, 40.0)   # sane MCI window; values outside (MoCA "any impairment" 40–93%) → regional


def _cog_sources():
    return json.load(io.open(SOURCES_JSON, encoding="utf-8"))["countries"] if os.path.exists(SOURCES_JSON) else {}


def _short_cite(c):
    import re
    c = (c or "").strip()
    m = re.match(r"(.{5,70}?\b(?:19|20)\d\d)", c)   # first author … year
    return (m.group(1) if m else c.split(".")[0])[:70]


def _mci_dx(crit):
    """clinical (formal diagnosis) vs screening (MoCA/MMSE cut-off — NOT a diagnosis) — for the UI marker."""
    c = (crit or "").lower()
    if any(k in c for k in ("petersen", "dsm", "iwg", "winblad", "clinical", "neuropsych", "consensus", "diagnos")):
        return "clinical"
    if any(k in c for k in ("moca", "mmse", "screen", "hds", "mini-cog", "hasegawa", "cut-off", "cutoff")):
        return "screening"
    return "unknown"


def build_mci_national():
    """public/data/mci/mci-national.json — Bai 2022 WB-region baseline (kind=regional) overlaid with
       NATIONAL values from scripts/mci-scd-sources.json where the estimate is a plausible MCI figure
       (3–40%); implausible screening artifacts (MoCA 'any impairment' 40–93%) revert to the regional
       baseline to keep the layer comparable. Criteria/confidence kept per country for the UI caveat."""
    src = _cog_sources()
    out = {}
    for i2, region in WB_REGION.items():
        if region in BAI_MCI:
            out[i2.lower()] = {"prev_pct": BAI_MCI[region], "year": "2022", "age": "50+",
                               "crit": f"WB {region} 區域統合 / regional pool", "src": BAI_SRC,
                               "url": BAI_URL, "conf": "regional", "dx": "regional", "kind": "regional"}
    lo, hi = MCI_PLAUSIBLE
    for cc, v in src.items():
        m = v.get("mci")
        if not (isinstance(m, dict) and isinstance(m.get("prev_pct"), (int, float))):
            continue
        if lo <= float(m["prev_pct"]) <= hi:
            out[cc.lower()] = {"prev_pct": round(float(m["prev_pct"]), 1), "year": str(m.get("year", ""))[:9],
                               "age": m.get("age") or "", "crit": (m.get("criteria") or "")[:70],
                               "src": _short_cite(m.get("citation")), "url": m.get("doi_url"),
                               "conf": m.get("confidence") or "", "dx": _mci_dx(m.get("criteria")), "ci": m.get("ci"), "kind": "national"}
    for cc, m in MCI_NATIONAL.items():        # curated 5 (TW/JP/KR/IN/CN) — hand-verified, always win
        out[cc] = {**m, "conf": "high", "dx": _mci_dx(m.get("crit")), "kind": "national"}
    nnat = sum(1 for v in out.values() if v["kind"] == "national")
    excl = sorted(cc for cc, v in src.items() if isinstance(v.get("mci"), dict)
                  and isinstance(v["mci"].get("prev_pct"), (int, float)) and not (lo <= v["mci"]["prev_pct"] <= hi))
    meta = {"metric": "MCI prevalence — Bai 2022 WB-region baseline + national values (mci-scd-sources.json)",
            "note": ("Baseline: Bai et al. 2022 (Age & Ageing) pooled MCI by World Bank region (50+, community). "
                     "National values from the cloud deep-research set where plausible (3–40%); screening "
                     "artifacts (MoCA 'any impairment' >40%) revert to the regional baseline. Criteria/age/"
                     "confidence vary → modelled, indicative only, NOT comparable across countries."),
            "regionalSrc": BAI_SRC, "regionalUrl": BAI_URL, "excludedOutliers": excl, "built": BUILD_DATE}
    write_json("mci/mci-national.json", {"meta": meta, "countries": out})
    log(f"  mci: {len(out)} countries ({nnat} national, rest regional; {len(excl)} outliers→regional)")
    return True


def build_scd_national():
    """public/data/scd/scd-national.json — SCD (subjective cognitive decline) national values from
       scripts/mci-scd-sources.json (~38 countries). ALL heterogeneous self-report with NO global-meta
       baseline → sparse, own scale, each flagged self-report vs structured. Indicative, NOT comparable
       (single-item self-report inflates to 40–76%, varies with question wording)."""
    src = _cog_sources()
    out = {}
    for cc, v in src.items():
        s = v.get("scd")
        if not (isinstance(s, dict) and isinstance(s.get("prev_pct"), (int, float))):
            continue
        crit = s.get("criteria") or ""
        out[cc.lower()] = {"prev_pct": round(float(s["prev_pct"]), 1), "year": str(s.get("year", ""))[:9],
                           "age": s.get("age") or "", "crit": crit[:70], "src": _short_cite(s.get("citation")),
                           "url": s.get("doi_url"), "conf": s.get("confidence") or "",
                           "ci": s.get("ci"), "instrument": "self-report" if "self-report" in crit.lower() else "structured"}
    meta = {"metric": "SCD prevalence — national values (self-report; no global baseline)",
            "note": ("Subjective cognitive decline. There is NO harmonised global SCD dataset, and estimates "
                     "are dominated by single-item self-report (memory complaints) that inflates to 40–76% and "
                     "varies with question wording → shown on its own scale, per-country criteria displayed, "
                     "indicative only and NOT comparable across countries."),
            "built": BUILD_DATE}
    write_json("scd/scd-national.json", {"meta": meta, "countries": out})
    log(f"  scd: {len(out)} countries (self-report/heterogeneous — indicative only)")
    return True


# ---------- World globe asset: admin-0 boundaries + national PM2.5 + composite PAF ----------
# For the 3D/2.5D globe prototypes. Natural Earth admin-0 (110m) polygons, with two national
# metrics baked per country: PM2.5 (world-country-pm25.json, 246 countries, latest year) and
# composite modifiable-risk PAF (exposome.json, ~200 countries). Standalone — reads the two
# already-built JSONs, so it can run without re-fetching ACAG.
def _norm_country(s):
    import unicodedata as _u, re as _re
    s = _u.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return _re.sub(r"[^a-z0-9]", "", s.lower())


# Natural-Earth name (normalised) → the key world-country-pm25.json actually uses.
WORLD_PM_ALIAS = {
    "unitedstatesofamerica": "United States", "czechia": "Czech Republic",
    "republicofserbia": "Serbia", "unitedrepublicoftanzania": "Tanzania",
    "democraticrepublicofthecongo": "Democratic Republic of the Congo",
    "congo": "Republic of Congo", "republicofthecongo": "Republic of Congo",
    "palestine": "Palestina", "guineabissau": "Guinea-Bissau",
    "ivorycoast": "Cote d'Ivoire", "eswatini": "Swaziland", "northmacedonia": "Macedonia",
    "bosniaandherzegovina": "Bosnia and Herzegovina", "southsudan": "South Sudan",
    "falklandislands": "Falkland Islands", "timorleste": "Timor-Leste",
}


def _round_coords(obj, nd=2):
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(obj[0]), nd), round(float(obj[1]), nd)]
        return [_round_coords(x, nd) for x in obj]
    return obj


# Population aged 65+ (% of total), by ISO alpha-2. SEED = rounded latest-available World Bank
# (SP.POP.65UP.TO.ZS) / UN WPP estimates (Taiwan: 內政部戶政司) for the map's countries + majors — a
# partial seed so the "aging society" layer ships visibly. The full ~200-country pull is a one-call
# global sweep for a local session (World Bank API is blocked in the build sandbox); drop its output
# as scripts/_data_in/world-65plus.csv (iso2,pct) and it overrides/extends this seed. Modelled estimate.
# NB: for every World-Bank country the csv (currently WB 2025) wins, so only Taiwan — which the World
# Bank excludes — actually reads from this seed. TW = 內政部戶政司 2025 年底 20.06%（正式邁入超高齡社會）.
SEED_POP65 = {
    "JP": 29.9, "IT": 24.1, "PT": 24.0, "FI": 23.3, "GR": 22.7, "DE": 22.4, "FR": 21.7,
    "NL": 20.5, "SE": 20.3, "DK": 20.3, "ES": 20.3, "AT": 19.6, "PL": 19.4, "BE": 19.4,
    "GB": 19.2, "CA": 19.0, "TW": 20.1, "KR": 20.3, "UA": 17.4, "AU": 17.2, "US": 17.1,
    "NZ": 16.6, "RU": 15.6, "TH": 15.2, "CN": 13.7, "CL": 12.8, "AR": 12.4, "BR": 10.5,
    "CO": 9.6, "TR": 9.5, "VN": 9.0, "MX": 8.3, "MY": 7.4, "IR": 7.0, "ID": 6.9, "IN": 6.9,
    "MM": 6.9, "ZA": 6.0, "BD": 6.0, "PH": 5.7, "EG": 4.5, "PK": 4.4, "ET": 3.5, "NG": 3.0,
    "KE": 2.5, "SA": 3.7,
}


def _load_pop65():
    """SEED_POP65 overlaid by scripts/_data_in/world-65plus.csv (iso2,pct) if present."""
    out = dict(SEED_POP65)
    path = os.path.join(DATA_IN, "world-65plus.csv")
    if os.path.exists(path):
        for row in _read_csv(path):
            iso2 = (row.get("iso2") or row.get("iso_a2") or row.get("code") or "").strip().upper()
            val = row.get("pct") or row.get("pct65") or row.get("value")
            if iso2 and val not in (None, ""):
                try:
                    out[iso2] = round(float(val), 1)
                except ValueError:
                    pass
        log(f"  pop65: overlaid _data_in/world-65plus.csv → {len(out)} countries")
    return out


def build_world_globe_geojson():
    """geo/world-globe.geojson — admin-0 polygons + national pm25 + composite paf + 65+ % per country."""
    from shapely.geometry import shape, mapping
    log("World globe geojson (admin-0 + national PM2.5 + PAF + 65+) …")
    world_pm = json.load(open(os.path.join(OUT, "pm25/world-country-pm25.json"), encoding="utf-8"))["countries"]
    expo = json.load(open(os.path.join(OUT, "exposome/exposome.json"), encoding="utf-8"))["countries"]
    _mci_path = os.path.join(OUT, "mci/mci-national.json")   # existence-guarded
    mci = json.load(open(_mci_path, encoding="utf-8"))["countries"] if os.path.exists(_mci_path) else {}
    _scd_path = os.path.join(OUT, "scd/scd-national.json")
    scd = json.load(open(_scd_path, encoding="utf-8"))["countries"] if os.path.exists(_scd_path) else {}
    pop65 = _load_pop65()
    pm_norm = {_norm_country(k): v["values"][-1] for k, v in world_pm.items()}
    prev_norm = {_norm_country(k): v for k, v in load_gbd_prev60().items()}   # GBD dementia % among 60+ (national + subnat)
    ne = json.loads(http_get(NE_ADMIN0).decode("utf-8", "replace"))
    feats, unmatched = [], []
    for f in ne["features"]:
        p = f["properties"]
        name = p.get("NAME") or p.get("NAME_LONG") or p.get("ADMIN")
        iso2 = p.get("ISO_A2_EH") or p.get("ISO_A2") or ""
        pm = None
        for fld in ("NAME", "NAME_LONG", "ADMIN", "GEOUNIT", "BRK_NAME", "NAME_EN", "FORMAL_EN"):
            nm = p.get(fld)
            if not nm:
                continue
            key = _norm_country(nm)
            key = _norm_country(WORLD_PM_ALIAS.get(key, "")) or key
            if key in pm_norm:
                pm = round(float(pm_norm[key]), 1)
                break
        if pm is None and _norm_country(name) != "antarctica":
            unmatched.append(name)
        iso2c = "" if iso2 == "-99" else iso2
        _ex = expo.get(iso2c.lower(), {}) if iso2c else {}
        paf = _ex.get("composite_paf_pct")   # composite PAF, matched by iso2
        # per-factor {prev, paf} so the globe can shade by a single risk factor (compact keys p/f)
        fac = {k: {"p": v["prev_pct"], "f": v["paf_pct"]} for k, v in _ex.get("factors", {}).items()} or None
        pop = pop65.get(iso2c)
        mci_v = mci.get(iso2c.lower(), {}).get("prev_pct") if iso2c else None            # MCI (Bai regional + national)
        scd_v = scd.get(iso2c.lower(), {}).get("prev_pct") if iso2c else None            # SCD (national self-report, ~38 countries)
        prev = None   # national dementia prevalence among 60+ (%) from GBD, matched by country name
        for fld in ("NAME", "NAME_LONG", "ADMIN", "GEOUNIT", "BRK_NAME", "NAME_EN", "FORMAL_EN"):
            nm = p.get(fld)
            if nm and _norm_country(nm) in prev_norm:
                prev = round(prev_norm[_norm_country(nm)] * 100, 2)
                break
        g = shape(f["geometry"]).simplify(0.15, preserve_topology=True)
        gm = mapping(g)
        gm = {"type": gm["type"], "coordinates": _round_coords(gm["coordinates"], 2)}
        feats.append({"type": "Feature",
                      "properties": {"name": name, "iso_a2": iso2c,
                                     "pm25": pm, "paf": paf, "fac": fac, "pop65": pop, "prev": prev, "mci": mci_v, "scd": scd_v},
                      "geometry": gm})
    write_json("geo/world-globe.geojson", {
        "type": "FeatureCollection",
        "meta": {"pm25": "ACAG SatPM2.5 V6.GL.03 national latest-year (CC BY 4.0)",
                 "paf": "composite modifiable-risk PAF, ~200 countries (NCD-RisC + WHO GHO × Livingston 2024 RRs) — modelled",
                 "fac": "per-factor {p: national prevalence %, f: PAF %} for the 5 chronic-disease factors (hypertension/diabetes/obesity/smoking/physical_inactivity) — lets the globe shade a single factor; modelled",
                 "pop65": "population aged 65+ (% of total) — World Bank 2025 (SP.POP.65UP.TO.ZS), 217 economies + Taiwan 內政部戶政司 2025; modelled estimate",
                 "prev": "GBD 2023 dementia prevalence among 60+ (%), national — modelled (IHME non-commercial)",
                 "mci": "MCI prevalence — Bai 2022 WB-region baseline + national values (mci-scd-sources.json); modelled, NOT cross-comparable",
                 "scd": "SCD prevalence — national self-report (~38 countries, scd-national.json); heterogeneous, NOT cross-comparable",
                 "boundaries": "Natural Earth admin-0 110m (public domain)", "built": BUILD_DATE},
        "features": feats})
    npaf = sum(1 for x in feats if x["properties"]["paf"] is not None)
    n65 = sum(1 for x in feats if x["properties"]["pop65"] is not None)
    nprev = sum(1 for x in feats if x["properties"]["prev"] is not None)
    log(f"  world-globe: {len(feats)} countries · PM2.5 matched {len(feats) - len(unmatched)} · PAF {npaf} · 65+ {n65} · prev {nprev}")
    if unmatched:
        log("  PM2.5 unmatched (" + str(len(unmatched)) + "): " + ", ".join(sorted(unmatched)[:50]))


def main():
    log("== BUILD DATA ==  RBAR(65+ dementia rate) = %.4f" % RBAR)
    towns_topo, towns, counties = load_boundaries()

    years, lat, lon, grid = load_pm25_years()

    log("Computing county x year PM2.5 …")
    county_pm = county_year_series(counties, years, lat, lon, grid)
    write_json("pm25/tw-county-pm25.json", {
        "meta": {"source": "ACAG SatPM2.5 V6.GL.03 (0.1deg annual)",
                 "method": "mean of grid cells within each county polygon",
                 "units": "ug/m3", "note": "years < 1998 clamp to 1998 in the app",
                 "citation": "Shen et al. 2024, ACS ES&T Air; van Donkelaar et al. 2021, ES&T",
                 "license": "CC BY 4.0", "built": BUILD_DATE},
        "year0": years[0], "year1": years[-1], "counties": county_pm})

    log("Computing per-town recent PM2.5 …")
    town_pm, pm_period = town_recent_pm(towns, years, lat, lon, grid)
    write_json("pm25/tw-district-pm25.json", {
        "meta": {"source": "ACAG SatPM2.5 V6.GL.03", "period": pm_period,
                 "method": "nearest 0.1deg cell to town centroid",
                 "units": "ug/m3", "citation": "Shen et al. 2024; van Donkelaar et al. 2021",
                 "license": "CC BY 4.0", "built": BUILD_DATE},
        "districts": town_pm})

    log("Modelled prevalence …")
    granularity, pop = fetch_population()
    rows, by_town = modelled_prevalence(towns, granularity, pop)
    write_tw_modelled(rows, by_town, granularity)

    log("World country PM2.5 (residence dropdown) …")
    world_pm = build_world_country_pm25()
    write_json("pm25/world-country-pm25.json", {
        "meta": {"source": "ACAG SatPM2.5 V6.GL.03 global region summary (population-weighted)",
                 "units": "ug/m3", "note": "years < 1998 clamp to 1998 in the app",
                 "citation": "Shen et al. 2024, ACS ES&T Air; van Donkelaar et al. 2021, ES&T",
                 "license": "CC BY 4.0", "built": BUILD_DATE},
        "year0": YEAR0, "year1": YEAR1, "countries": world_pm})

    log("Boundaries -> geo/tw-districts.topo.json …")
    write_json("geo/tw-districts.topo.json", towns_topo)

    log("Sub-national PM2.5 maps (Natural Earth admin-1 + ACAG zonal): JP/KR/CN/US/IN …")
    ne = load_ne_admin1()
    ryears, rtmp, rpaths = load_pm25_recent_cached(recent=3)
    try:
        for key, cfg in COUNTRY_MAPS.items():
            units = ne_units(ne, cfg["admin"], bbox=cfg["bbox"])
            rlat, rlon, rgrid = crop_recent(ryears, rpaths, cfg["bbox"])
            pm, period = admin1_recent_pm(units, ryears, rlat, rlon, rgrid, recent=len(ryears))
            write_json(f"pm25/{key}-admin1-pm25.json", {
                "meta": {"source": "ACAG SatPM2.5 V6.GL.03 (0.1deg annual)", "period": period,
                         "method": "mean of grid cells within each admin-1 polygon",
                         "units": "ug/m3", "country": cfg["name"],
                         "citation": "Shen et al. 2024; van Donkelaar et al. 2021",
                         "license": "CC BY 4.0", "built": BUILD_DATE},
                "units": pm})
            write_json(f"geo/{key}-admin1.geojson", units_geojson(units))
            vv = [v for v in pm.values() if v is not None]
            log(f"  {cfg['name']}: {len(units)} units, PM2.5 "
                f"{min(vv):.1f}–{max(vv):.1f} ug/m3" if vv else f"  {cfg['name']}: {len(units)} units, no data")
            # Prevalence layer (all "modelled estimate"): prefer a published per-admin table
            # (direct), else rates×population; else pending (owner downloads — see the doc).
            prev_meta = {"cn": {"age_group": "50+", "source": "Liu/Gao 2024 (CHARLS 2018)"},
                         "in": {"age_group": "60+", "source": "Lee et al. 2023 (LASI-DAD)"}}.get(key)
            pop_rate = {"jp": ("Japan", load_estat_jp_pop),
                        "kr": ("Republic of Korea", load_kosis_kr_pop)}.get(key)
            done = key in GBD_DIRECT and build_gbd_direct(key, cfg["name"], units, GBD_DIRECT[key])
            if not done and key == "kr":
                done = build_kr_nid_prev60(units)
            if not done and pop_rate:
                done = build_pop_rate_prev60(key, cfg["name"], pop_rate[0], units, pop_rate[1]())
            if not done and key in WORLDPOP:
                iso3, gbd_name = WORLDPOP[key]
                done = build_pop_rate_prev60(key, cfg["name"], gbd_name, units,
                                             load_worldpop_pop(key, iso3),
                                             pop_source="WorldPop 2020 1km age/sex population")
            if not done and not build_direct_prevalence(key, cfg["name"], units, prev_meta) \
                    and not build_admin_prevalence(key, cfg["name"]):
                log(f"  {cfg['name']}: no _data_in/{key}-admin1-prevalence.csv or -pop.csv -> "
                    f"prevalence layer pending (see data-download-points.md)")
    finally:
        for p in rpaths.values():
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(rtmp)
        except OSError:
            pass

    assets = {
        "pm25/tw-county-pm25.json": "ACAG V6.GL.03 county x year PM2.5 (CC BY 4.0)",
        "pm25/tw-district-pm25.json": "ACAG V6.GL.03 per-town recent PM2.5 (CC BY 4.0)",
        "pm25/world-country-pm25.json": "ACAG V6.GL.03 country x year PM2.5, pop-weighted (CC BY 4.0)",
        "geo/tw-districts.topo.json": "taiwan-atlas / MOI #7441 boundaries (OGDL-Taiwan-1.0)",
        "dementia/tw-dementia-modelled.json": "modelled prevalence, NHRI 2020-23 x MOI pop",
        "dementia/cn-modelled.json": "China provincial dementia prevalence, Liu/Gao 2024 (CHARLS 2018) — modelled estimate",
        "dementia/in-modelled.json": "India state dementia prevalence, Lee et al. 2023 (LASI-DAD) — modelled estimate",
        "dementia/us-modelled.json": "US state dementia prevalence 60+, GBD 2023 (IHME) — modelled estimate",
        "dementia/br-modelled.json": "Brazil state dementia prevalence 60+, GBD 2023 (IHME) — modelled estimate",
        "dementia/mx-modelled.json": "Mexico state dementia prevalence 60+, GBD 2023 (IHME) — modelled estimate",
        "dementia/ir-modelled.json": "Iran province dementia prevalence 60+, GBD 2023 (IHME) — modelled estimate",
        "dementia/jp-modelled.json": "Japan prefecture dementia prevalence 60+, GBD 2023 rates × e-Stat pop — modelled estimate",
        "dementia/kr-modelled.json": "Korea province dementia prevalence 60+, MOHW/NID 시군구별 치매현황 (KOGL Type 1) — official estimate"}
    for key, cfg in COUNTRY_MAPS.items():   # admin-1 PM2.5 (+ boundaries) per country
        assets[f"pm25/{key}-admin1-pm25.json"] = f"ACAG V6.GL.03 {cfg['name']} admin-1 PM2.5 (CC BY 4.0)"
        assets[f"geo/{key}-admin1.geojson"] = f"Natural Earth admin-1 ({cfg['name']}), public domain"
    for key, (iso3, gname) in WORLDPOP.items():   # admin-1 prevalence via GBD rate × WorldPop pop
        if not os.path.exists(os.path.join(OUT, "dementia", f"{key}-modelled.json")):
            continue   # rasters not yet downloaded for this country → JSON absent, don't list it
        assets[f"dementia/{key}-modelled.json"] = (f"{gname} admin-1 dementia prevalence 60+, "
                                                   "GBD 2023 rates × WorldPop 2020 pop — modelled estimate")
    build_exposome()
    assets["exposome/exposome.json"] = ("national modifiable risk factors + composite PAF (~200 countries): "
                                        "NCD-RisC (BP/diabetes/BMI) + WHO GHO (smoking/inactivity) + Taiwan NHIS × Livingston 2024 RRs")
    build_mci_national()
    assets["mci/mci-national.json"] = ("MCI prevalence — Bai 2022 (Age & Ageing) WB-region baseline + "
                                       "national values (cloud deep-research, plausible 3–40%); modelled estimate")
    build_scd_national()
    assets["scd/scd-national.json"] = ("SCD prevalence — national self-report values (~38 countries, cloud "
                                       "deep-research); heterogeneous, indicative only, not comparable")
    build_world_globe_geojson()
    assets["geo/world-globe.geojson"] = ("Natural Earth admin-0 (public domain) + national PM2.5 "
                                         "(ACAG V6.GL.03, CC BY 4.0) + composite PAF (Livingston 2024) "
                                         "+ 65+ share (World Bank/UN, partial seed) — for the globe view")
    write_json("manifest.json", {
        "built": BUILD_DATE, "assets": assets,
        "attribution": ["PM2.5 © ACAG/WashU (Shen 2024; van Donkelaar 2021), CC BY 4.0",
                        "台灣界線 © 內政部NLSC / taiwan-atlas, OGDL-Taiwan-1.0",
                        "其他國家界線: Natural Earth (public domain)",
                        "人口 © 內政部 (MOI)", "失智盛行率為模型估計值 (NHRI 2020-2023)",
                        "中國省級失智盛行率 © Liu/Gao et al. 2024, Lancet Reg Health – W Pac (CHARLS 2018) — 模型估計",
                        "印度邦級失智盛行率 © Lee et al. 2023, Alzheimer's & Dementia (LASI-DAD) — 模型估計",
                        "美國／巴西／墨西哥／伊朗各州省失智盛行率 © GBD 2023, IHME — 模型估計，非商業授權 (attribution)",
                        "日本都道府県失智盛行率 © GBD 2023 (IHME) 年齡別率 × e-Stat 都道府県人口 — 模型估計",
                        "韓國市道失智盛行率 © GBD 2023 (IHME) 年齡別率 × KOSIS 시도人口 — 模型估計",
                        "其餘各國 admin-1 失智盛行率 © GBD 2023 (IHME) 年齡別率 × WorldPop 2020 人口 (CC BY 4.0) — 模型估計",
                        "可調控風險因子盛行率 © NCD-RisC (血壓/糖尿病/BMI) + WHO Global Health Observatory (吸菸/身體活動) + 2021 台灣國民健康訪問調查；PAF 相對風險 © Livingston et al. 2024 Lancet Commission — 模型估計"]})
    log("== DONE ==")


if __name__ == "__main__":
    sys.exit(main())
