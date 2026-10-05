#!/usr/bin/env python3
"""Build an engine-ready attempt package for one LA County tile.

Same conventions as the accepted Tarzana pilot, so results stay comparable:
EPSG:26911, receiver/source Z relative to imported terrain, uniform ground
G = 0.5, CNOSSOS-EU 2020 road emission on pavement NL08, day hourly flow =
AADT / 16, evening = 0.6 x day, night = 0.2 x day.

Roads: Census TIGER 2025 EDGES (ROADFLG=Y, motor-vehicle MTFCC) inside the
tile plus a source halo. Traffic: the FHWA HPMS 2024 record aligned with the
edge (within 20 m, 60 m for S1100; tangent within 25 degrees; at least half of
the edge inside the buffer; freeway edges only take freeway records and vice
versa). Two-way AADT is split 50/50 across divided carriageways: every S1100
edge, and any other edge whose HPMS record also carries an overlapping,
laterally offset edge. Unmatched edges use CLASS_DEFAULTS. Every source keeps
its traffic basis, so defaults are never presented as counts.

Buildings: LA County DPW footprints (CODE = Building) inside the halo, height
feet -> metres; missing heights get the median of buildings within 100 m
(else 4 m) and HEIGHT_IMPUTED = true.

Terrain: USGS 1/3 arc-second DEM mosaic, bilinear to 10 m UTM, ESRI ASCII.

Receivers: open-space grid at 1.5 m outside buildings + 2 m, and facade
receivers at 4 m height, 2 m outside walls, at most --facade-spacing m apart
(minimum 4 per ring), for buildings whose centroid is in the tile.

Run with the GIS interpreter:
  PYTHONPATH=implementation/work/.geo_python \\
  ~/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 \\
  build_county_tile.py --name koreatown --lonlat -118.3090 34.0617
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import struct
import subprocess
import time
import urllib.parse
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import shapely
from pyproj import Transformer
from rasterio.merge import merge
from rasterio.warp import Resampling, reproject, transform_bounds
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box, shape
from shapely.ops import nearest_points, transform as shp_transform
from shapely.strtree import STRtree

PROJECT = Path(__file__).resolve().parents[5]
FOUNDATION = PROJECT / "implementation/work/autonomous_delivery_2026_10_02/county_foundation"
EDGES = FOUNDATION / "snapshots/tl_2025_06037_edges.zip"
HPMS = FOUNDATION / "snapshots/hpms_2024_06037/rows.jsonl"
DEM_DIR = FOUNDATION / "source_cache/usgs_dem13arcsec"
CAMPAIGN = PROJECT / "implementation/work/campaign/county_v1"
BUILDINGS_URL = "https://dpw.gis.lacounty.gov/dpw/rest/services/buildingfootprints/MapServer/0/query"

TO_UTM_WGS = Transformer.from_crs("EPSG:4326", "EPSG:26911", always_xy=True)
TO_UTM_NAD = Transformer.from_crs("EPSG:4269", "EPSG:26911", always_xy=True)
TO_WGS = Transformer.from_crs("EPSG:26911", "EPSG:4326", always_xy=True)

# MTFCC: (two-way AADT, medium-truck share, heavy-truck share, LV km/h, truck km/h, junction distance m)
CLASS_DEFAULTS = {
    "S1100": (60000, 0.03, 0.04, 105, 88, 200.0),
    "S1630": (6000, 0.015, 0.015, 65, 55, 200.0),
    "S1200": (12000, 0.02, 0.01, 56, 50, 50.0),
    "S1400": (800, 0.01, 0.0, 40, 40, 50.0),
    "S1640": (1500, 0.02, 0.0, 40, 40, 50.0),
    "S1730": (150, 0.01, 0.0, 20, 20, 50.0),
    "S1740": (300, 0.01, 0.0, 25, 25, 50.0),
}
# HPMS functional system -> (LV km/h, truck km/h) when the record has no speed limit
HPMS_SPEEDS = {"1": (105, 88), "2": (105, 88), "3": (64, 56), "4": (56, 50), "5": (56, 50), "6": (48, 48), "7": (40, 40)}
FREEWAY_F_SYSTEMS = {"1", "2"}
EVENING, NIGHT = 0.6, 0.2


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def dbf_rows(raw: bytes):
    count = struct.unpack_from("<I", raw, 4)[0]
    hlen, rlen = struct.unpack_from("<HH", raw, 8)
    fields, off = [], 32
    while raw[off] != 13:
        d = raw[off:off + 32]
        fields.append((d[:11].split(b"\0", 1)[0].decode("ascii"), d[16]))
        off += 32
    for i in range(count):
        rec = raw[hlen + i * rlen:hlen + (i + 1) * rlen]
        if rec[:1] == b"*":
            yield None
            continue
        cur, out = 1, {}
        for name, width in fields:
            out[name] = rec[cur:cur + width].decode("cp1252", "replace").strip() or None
            cur += width
        yield out


def shp_rows(raw: bytes):
    off = 100
    while off < len(raw):
        _, words = struct.unpack_from(">II", raw, off)
        start = off + 8
        body = raw[start:start + words * 2]
        kind = struct.unpack_from("<I", body, 0)[0]
        if kind == 0:
            yield None
        else:
            n_parts, n_points = struct.unpack_from("<II", body, 36)
            part_ix = list(struct.unpack_from("<" + "I" * n_parts, body, 44))
            pts_off = 44 + 4 * n_parts
            pts = [struct.unpack_from("<dd", body, pts_off + i * 16) for i in range(n_points)]
            ends = part_ix[1:] + [n_points]
            parts = [LineString(pts[a:b]) for a, b in zip(part_ix, ends) if b - a >= 2]
            yield parts[0] if len(parts) == 1 else (MultiLineString(parts) if parts else None)
        off = start + words * 2


def tangent_angle(a: LineString, b: LineString) -> float:
    pa, pb = nearest_points(a, b)

    def direction(g, p):
        d, total = g.project(p), g.length
        lo, hi = max(0.0, d - 5.0), min(total, d + 5.0)
        p0, p1 = g.interpolate(lo), g.interpolate(hi)
        return math.atan2(p1.y - p0.y, p1.x - p0.x)
    angle = abs(math.degrees(direction(a, pa) - direction(b, pb))) % 180
    return min(angle, 180 - angle)


def load_edges(halo: Polygon):
    halo_nad = shp_transform(Transformer.from_crs("EPSG:26911", "EPSG:4269", always_xy=True).transform, halo)
    with zipfile.ZipFile(EDGES) as z:
        attrs = list(dbf_rows(z.read("tl_2025_06037_edges.dbf")))
        geoms = list(shp_rows(z.read("tl_2025_06037_edges.shp")))
    edges = []
    for a, g in zip(attrs, geoms):
        if not a or g is None or a.get("ROADFLG") != "Y" or a.get("MTFCC") not in CLASS_DEFAULTS:
            continue
        if not g.intersects(halo_nad):
            continue
        utm = shp_transform(TO_UTM_NAD.transform, g).intersection(halo)
        for part in getattr(utm, "geoms", [utm]):
            if isinstance(part, LineString) and part.length >= 1.0:
                edges.append({"tlid": a.get("TLID"), "mtfcc": a["MTFCC"], "name": a.get("FULLNAME") or "", "geom": part})
    return edges


def load_hpms(halo: Polygon):
    rows = []
    for line in HPMS.open():
        r = json.loads(line)
        g = shp_transform(TO_UTM_WGS.transform, shape(r["line"]))
        if g.intersects(halo):
            r["_geom"] = g
            rows.append(r)
    return rows


def number(value, default=None):
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def assign_traffic(edges, hpms):
    tree = STRtree([r["_geom"] for r in hpms]) if hpms else None
    for e in edges:
        e["hpms"] = None
        if tree is None:
            continue
        tol = 60.0 if e["mtfcc"] == "S1100" else 20.0
        best = None
        for j in tree.query(e["geom"].buffer(tol)):
            r = hpms[int(j)]
            freeway_record = r.get("f_system") in FREEWAY_F_SYSTEMS and r.get("facility_type") != "4"
            if (e["mtfcc"] == "S1100") != freeway_record:
                continue
            if (e["mtfcc"] == "S1630") != (r.get("facility_type") == "4"):
                continue
            inside = e["geom"].intersection(r["_geom"].buffer(tol)).length / e["geom"].length
            if inside < 0.5 or tangent_angle(e["geom"], r["_geom"]) > 25:
                continue
            key = (inside, number(r.get("aadt"), 0))
            if best is None or key > best[0]:
                best = (key, r)
        if best:
            e["hpms"] = best[1]
    by_record: dict[str, list] = {}
    for e in edges:
        if e["hpms"] is not None:
            by_record.setdefault(e["hpms"][":id"], []).append(e)
    for e in edges:
        e["split"] = 1.0
        if e["mtfcc"] == "S1100":
            e["split"] = 0.5
        elif e["hpms"] is not None:
            line = e["hpms"]["_geom"]
            a0, a1 = sorted((line.project(Point(e["geom"].coords[0])), line.project(Point(e["geom"].coords[-1]))))
            for other in by_record[e["hpms"][":id"]]:
                if other is e:
                    continue
                b0, b1 = sorted((line.project(Point(other["geom"].coords[0])), line.project(Point(other["geom"].coords[-1]))))
                overlap = min(a1, b1) - max(a0, b0)
                if a1 - a0 > 1 and overlap > 0.5 * (a1 - a0) and e["geom"].distance(other["geom"]) > 5:
                    e["split"] = 0.5
                    break


def traffic_for(e):
    aadt0, mv_share, hgv_share, lv_spd, trk_spd, junc = CLASS_DEFAULTS[e["mtfcc"]]
    r = e["hpms"]
    if r is not None:
        aadt = number(r.get("aadt"), aadt0)
        su, comb = number(r.get("aadt_single_unit")), number(r.get("aadt_combination"))
        if su is not None and comb is not None and aadt > 0:
            mv_share, hgv_share = min(su / aadt, 0.5), min(comb / aadt, 0.5)
        limit = number(r.get("speed_limit"))
        lv_spd, trk_spd = (limit * 1.609344, min(limit * 1.609344, 88.0)) if limit else HPMS_SPEEDS.get(r.get("f_system"), (lv_spd, trk_spd))
        basis = f"hpms_2024:{r[':id']}"
    else:
        aadt, basis = aadt0, f"class_default:{e['mtfcc']}"
    aadt *= e["split"]
    day = aadt / 16.0
    return {"aadt_assigned": aadt, "basis": basis, "day": day, "mv": mv_share, "hgv": hgv_share,
            "lv_spd": lv_spd, "trk_spd": trk_spd, "junc": junc}


def fetch_buildings(halo: Polygon, cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    xmin, ymin, xmax, ymax = halo.bounds
    features, offset = [], 0
    while True:
        page = cache / f"buildings_{offset:07d}.json"
        if not page.exists():
            params = {"where": "CODE='Building'", "geometry": f"{xmin},{ymin},{xmax},{ymax}", "geometryType": "esriGeometryEnvelope",
                      "inSR": "26911", "spatialRel": "esriSpatialRelIntersects", "outFields": "OBJECTID,BLD_ID,HEIGHT,ELEV,DATE_",
                      "returnGeometry": "true", "outSR": "26911", "orderByFields": "OBJECTID", "resultOffset": str(offset),
                      "resultRecordCount": "1000", "f": "geojson"}
            for attempt in range(5):
                # curl uses the system trust store (Python's bundle rejects the local TLS proxy).
                result = subprocess.run(["curl", "-sS", "--fail", "--max-time", "180", BUILDINGS_URL + "?" + urllib.parse.urlencode(params)],
                                        capture_output=True)
                try:
                    data = result.stdout
                    json.loads(data)
                    break
                except Exception:
                    if attempt == 4:
                        raise RuntimeError(f"building query failed at offset {offset}: {result.stderr[-300:]!r}")
                    time.sleep(5 * (attempt + 1))
            page.write_bytes(data)
        doc = json.loads(page.read_text())
        batch = doc.get("features", [])
        features.extend(batch)
        if len(batch) < 1000 and not doc.get("exceededTransferLimit") and not (doc.get("properties") or {}).get("exceededTransferLimit"):
            break
        offset += len(batch)
    return features


def prepare_buildings(raw):
    polys, props = [], []
    for f in raw:
        g = f.get("geometry")
        if not g:
            continue
        geom = shape(g)
        if not geom.is_valid:
            geom = geom.buffer(0)
        for part in getattr(geom, "geoms", [geom]):
            if isinstance(part, Polygon) and part.area >= 4.0:
                h = number((f.get("properties") or {}).get("HEIGHT"))
                polys.append(Polygon(part.exterior.coords))
                props.append({"bld_id": str((f.get("properties") or {}).get("BLD_ID") or ""), "height_m": h * 0.3048 if h and h > 0 else None})
    if not polys:
        return [], []
    tree = STRtree(polys)
    known = np.array([p["height_m"] if p["height_m"] else np.nan for p in props])
    for i, p in enumerate(props):
        p["imputed"] = p["height_m"] is None
        if p["imputed"]:
            near = [known[int(j)] for j in tree.query(polys[i].centroid.buffer(100.0)) if not np.isnan(known[int(j)])]
            p["height_m"] = float(statistics.median(near)) if near else 4.0
    return polys, props


WALL_DEFAULT_HEIGHT_M = 4.3  # typical Caltrans sound wall (14 ft) when the source has no height


def wall_height(tags: dict) -> tuple[float, bool]:
    """OSM height tag in metres (accepts '4', '4.5 m', "14'", '14 ft'); default otherwise."""
    raw = str(tags.get("height", "")).strip().lower()
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*(m|ft|'|feet)?$", raw)
    if not match:
        return WALL_DEFAULT_HEIGHT_M, True
    value = float(match.group(1)) * (0.3048 if match.group(2) in ("ft", "'", "feet") else 1.0)
    return (value, False) if 1.0 <= value <= 12.0 else (WALL_DEFAULT_HEIGHT_M, True)


def load_walls(halo: Polygon, path: Path) -> list[dict]:
    """Sound walls (WGS84 GeoJSON lines or thin polygons) inside the halo, as 0.3 m wide obstacles."""
    to_utm = Transformer.from_crs("OGC:CRS84", "EPSG:26911", always_xy=True)
    walls = []
    for feature in json.loads(path.read_text())["features"]:
        geom = shape(feature["geometry"])
        if geom.geom_type not in ("LineString", "MultiLineString", "Polygon", "MultiPolygon"):
            continue
        utm = shp_transform(lambda x, y, z=None: to_utm.transform(x, y), geom)
        if not utm.intersects(halo):
            continue
        line = utm.boundary if utm.geom_type in ("Polygon", "MultiPolygon") else utm
        footprint = line.intersection(halo).buffer(0.15, cap_style=2, join_style=2)
        height, imputed = wall_height(feature.get("properties", {}))
        for part in getattr(footprint, "geoms", [footprint]):
            if part.area > 0.05:
                walls.append({"polygon": part, "height": height, "imputed": imputed, "id": str(feature.get("properties", {}).get("@id") or feature.get("id") or "")})
    return walls


def dem_grid(halo: Polygon, dem_dir: Path, cell: float) -> np.ndarray:
    """The DEMs in dem_dir resampled onto the halo's cell grid; -9999 where they have no data."""
    xmin, ymin, xmax, ymax = halo.bounds
    width, height = int(round((xmax - xmin) / cell)), int(round((ymax - ymin) / cell))
    terrain = np.full((height, width), -9999.0, dtype=np.float32)
    sources = []
    for tif in sorted(dem_dir.glob("*.tif")):
        src = rasterio.open(tif)
        b = transform_bounds("EPSG:26911", src.crs, xmin, ymin, xmax, ymax)
        if not (b[2] < src.bounds.left or b[0] > src.bounds.right or b[3] < src.bounds.bottom or b[1] > src.bounds.top):
            sources.append(src)
        else:
            src.close()
    if not sources:
        return terrain
    crs = sources[0].crs
    mosaic, mosaic_transform = merge(sources, bounds=transform_bounds("EPSG:26911", crs, xmin - 100, ymin - 100, xmax + 100, ymax + 100))
    nodata = sources[0].nodata if sources[0].nodata is not None else -999999.0
    native = abs(sources[0].res[0])
    for s in sources:
        s.close()
    dst_transform = rasterio.transform.from_origin(xmin, ymax, cell, cell)
    reproject(mosaic[0], terrain, src_transform=mosaic_transform, src_crs=crs, dst_transform=dst_transform, dst_crs="EPSG:26911",
              src_nodata=nodata, dst_nodata=-9999.0, resampling=Resampling.average if cell > 2 * native else Resampling.bilinear)
    return terrain


def build_terrain(halo: Polygon, out: Path, dem_dir: Path = DEM_DIR, cell: float = 10.0):
    """Terrain from dem_dir; cells it leaves empty (lidar gaps, Catalina) are filled from the USGS 1/3 arc-second DEM.

    Returns the grid, its origin and the terrain source label recorded in the manifest."""
    xmin, ymin, xmax, ymax = halo.bounds
    terrain = dem_grid(halo, dem_dir, cell)
    source = dem_dir.name
    empty = terrain == -9999.0
    if empty.any() and dem_dir.resolve() != DEM_DIR.resolve():
        fallback = dem_grid(halo, DEM_DIR, cell)
        terrain[empty] = fallback[empty]
        filled = float((empty & (fallback != -9999.0)).mean())
        source = DEM_DIR.name if empty.all() else f"{dem_dir.name}+{DEM_DIR.name}_fill_{filled:.1%}"
    if (terrain == -9999.0).mean() > 0.01:
        raise ValueError("DEM leaves more than 1% of the tile halo without data")
    height, width = terrain.shape
    lines = [f"ncols {width}", f"nrows {height}", f"xllcorner {xmin:.3f}", f"yllcorner {ymin:.3f}", f"cellsize {cell:.3f}", "NODATA_value -9999"]
    lines += [" ".join(f"{v:.3f}" if v != -9999.0 else "-9999" for v in row) for row in terrain]
    out.write_text("\n".join(lines) + "\n")
    return terrain, (xmin, ymax, cell), source


def ground_z(terrain, origin, x, y):
    xmin, ymax, cell = origin
    col = np.clip(((np.asarray(x) - xmin) / cell).astype(int), 0, terrain.shape[1] - 1)
    row = np.clip(((ymax - np.asarray(y)) / cell).astype(int), 0, terrain.shape[0] - 1)
    return terrain[row, col]


def build_receivers(name: str, tile: Polygon, polys, props, terrain, origin, grid: float, facade_spacing: float, dense_zone=None):
    receivers = []
    tree = STRtree(polys) if polys else None
    near_tile = [i for i in (tree.query(tile.buffer(5.0)) if tree else [])]
    blocked = shapely.union_all([polys[int(i)].buffer(2.0) for i in near_tile]) if near_tile else Polygon()
    xmin, ymin, xmax, ymax = tile.bounds
    xs, ys = np.meshgrid(np.arange(xmin + grid / 2, xmax, grid), np.arange(ymin + grid / 2, ymax, grid))
    xs, ys = xs.ravel(), ys.ravel()
    spacing = np.full(len(xs), grid)
    if dense_zone is not None and not dense_zone.is_empty and grid > 10:
        # Adaptive grid: add the 10 m lattice inside the near-road zone, where levels change fastest.
        dx, dy = np.meshgrid(np.arange(xmin + 5, xmax, 10.0), np.arange(ymin + 5, ymax, 10.0))
        dx, dy = dx.ravel(), dy.ravel()
        near = shapely.contains_xy(dense_zone, dx, dy)
        xs, ys, spacing = np.concatenate([xs, dx[near]]), np.concatenate([ys, dy[near]]), np.concatenate([spacing, np.full(int(near.sum()), 10.0)])
    keep = ~shapely.contains_xy(blocked, xs, ys) if not blocked.is_empty else np.ones(len(xs), bool)
    zs = ground_z(terrain, origin, xs[keep], ys[keep])
    for x, y, z, g in zip(xs[keep], ys[keep], zs, spacing[keep]):
        receivers.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(x), float(y), 1.5]},
                          "properties": {"RECEIVER_KEY": f"{name}:grid{int(g)}:{x:.1f}:{y:.1f}", "RECEIVER_FAMILY": "open_space_metric_lattice",
                                         "HEIGHT_ABOVE_GROUND_M": 1.5, "GROUND_ELEVATION_M": round(float(z), 3), "GRID_SPACING_M": float(g),
                                         "VERTICAL_CONVENTION": "relative_to_imported_terrain"}})
    for i in near_tile:
        i = int(i)
        poly = polys[i]
        if not tile.contains(poly.centroid):
            continue
        ring = LineString(poly.exterior.coords)
        n = max(4, int(math.ceil(ring.length / facade_spacing)))
        for k in range(n):
            d = (k + 0.5) * ring.length / n
            p, q = ring.interpolate(max(0.0, d - 0.5)), ring.interpolate(min(ring.length, d + 0.5))
            tx, ty = q.x - p.x, q.y - p.y
            norm = math.hypot(tx, ty)
            if norm == 0:
                continue
            c = ring.interpolate(d)
            for sign in (1, -1):
                cand = Point(c.x + sign * ty / norm * 2.0, c.y - sign * tx / norm * 2.0)
                if poly.contains(cand):
                    continue
                if any(polys[int(j)].distance(cand) < 1.0 for j in tree.query(cand.buffer(1.0))):
                    break
                z = float(ground_z(terrain, origin, [cand.x], [cand.y])[0])
                receivers.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [cand.x, cand.y, 4.0]},
                                  "properties": {"RECEIVER_KEY": f"{name}:facade:{props[i]['bld_id']}:{k}", "RECEIVER_FAMILY": "building_facade_exterior",
                                                 "HEIGHT_ABOVE_GROUND_M": 4.0, "GROUND_ELEVATION_M": round(z, 3), "BUILDING_PK": i + 1,
                                                 "SOURCE_BLD_ID": props[i]["bld_id"], "FACADE_TARGET_MAX_SPACING_M": facade_spacing,
                                                 "FACADE_LOCAL_NORMAL_OFFSET_M": 2.0, "VERTICAL_CONVENTION": "relative_to_imported_terrain"}})
                break
    for pk, r in enumerate(receivers, 1):
        r["properties"] = {"PK": pk, **r["properties"]}
    return receivers


def write_json(path: Path, value) -> dict:
    data = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    path.write_bytes(data)
    return {"path": f"input/{path.name}", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", required=True, help="lowercase slug, e.g. koreatown")
    parser.add_argument("--lonlat", type=float, nargs=2, help="any point in the tile; snapped to the 1 km UTM grid")
    parser.add_argument("--x0", type=float)
    parser.add_argument("--y0", type=float)
    parser.add_argument("--size", type=float, default=1000.0)
    parser.add_argument("--halo", type=float, default=1500.0)
    parser.add_argument("--grid", type=float, default=10.0)
    parser.add_argument("--facade-spacing", type=float, default=4.0)
    parser.add_argument("--dense-near-roads", type=float, default=0.0,
                        help="add a 10 m grid within this many metres of roads with two-way AADT >= --dense-aadt (0 = off)")
    parser.add_argument("--dense-aadt", type=float, default=5000.0)
    parser.add_argument("--dem-dir", type=Path, default=None, help="GeoTIFF DEM directory (default: USGS 1/3 arc-second mosaic)")
    parser.add_argument("--dem-cell", type=float, default=10.0, help="terrain grid in metres; non-default DEMs add t<cell> to the layout")
    parser.add_argument("--walls", type=Path, default=None, help="sound walls GeoJSON (WGS84); adds w to the layout")
    args = parser.parse_args()
    if args.lonlat:
        x, y = TO_UTM_WGS.transform(*args.lonlat)
        x0, y0 = math.floor(x / args.size) * args.size, math.floor(y / args.size) * args.size
    else:
        x0, y0 = args.x0, args.y0
    tile = box(x0, y0, x0 + args.size, y0 + args.size)
    halo = tile.buffer(args.halo, join_style=2)
    layout = f"g{int(args.grid)}{'a' + str(int(args.dense_near_roads)) if args.dense_near_roads else ''}f{int(args.facade_spacing)}"
    if args.dem_dir or args.dem_cell != 10.0:
        layout += f"t{args.dem_cell:g}"
    if args.walls:
        layout += "w"
    attempt_id = f"phase1-county-{args.name}-{layout}-v1"
    out = CAMPAIGN / "attempts" / attempt_id
    if (out / "attempt_manifest.json").exists():
        raise FileExistsError(f"tile package already exists: {out}")
    (out / "input").mkdir(parents=True, exist_ok=True)
    timings, started = {}, time.monotonic()

    edges = load_edges(halo)
    hpms = load_hpms(halo)
    assign_traffic(edges, hpms)
    timings["roads_s"] = round(time.monotonic() - started, 1)

    raw = fetch_buildings(halo, out / "cache")
    polys, props = prepare_buildings(raw)
    timings["buildings_s"] = round(time.monotonic() - started - timings["roads_s"], 1)

    terrain, origin, terrain_source = build_terrain(halo, out / "input/terrain.asc", args.dem_dir or DEM_DIR, args.dem_cell)
    dense_zone = None
    if args.dense_near_roads:
        busy = [e["geom"] for e in edges if traffic_for(e)["aadt_assigned"] / e["split"] >= args.dense_aadt and e["geom"].distance(tile) <= args.dense_near_roads]
        dense_zone = shapely.union_all([g.buffer(args.dense_near_roads) for g in busy]).intersection(tile) if busy else None
    receivers = build_receivers(args.name, tile, polys, props, terrain, origin, args.grid, args.facade_spacing, dense_zone)
    timings["terrain_receivers_s"] = round(time.monotonic() - started - timings["roads_s"] - timings["buildings_s"], 1)

    sources, periods, basis_counts = [], [], {}
    for pk, e in enumerate(edges, 1):
        t = traffic_for(e)
        kind = t["basis"].split(":")[0]
        basis_counts[f"{e['mtfcc']}:{kind}"] = basis_counts.get(f"{e['mtfcc']}:{kind}", 0) + 1
        coords = [[x, y, 0.05] for x, y in e["geom"].coords]
        meta = {"TLID": e["tlid"], "MTFCC": e["mtfcc"], "FULLNAME": e["name"], "TRAFFIC_BASIS": t["basis"],
                "CARRIAGEWAY_SPLIT": e["split"], "AADT_ASSIGNED": round(t["aadt_assigned"], 1), "OBSERVED": False, "LIVE": False}
        sources.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {"PK": pk, "IDSOURCE": pk, **meta}})
        for p_ix, (period, factor) in enumerate((("D", 1.0), ("E", EVENING), ("N", NIGHT))):
            total = t["day"] * factor
            periods.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": {
                "PK": 3 * (pk - 1) + p_ix + 1, "IDSOURCE": pk, "PERIOD": period,
                "LV": total * (1 - t["mv"] - t["hgv"]), "MV": total * t["mv"], "HGV": total * t["hgv"], "WAV": 0.0, "WBV": 0.0,
                "LV_SPD": t["lv_spd"], "MV_SPD": t["trk_spd"], "HGV_SPD": t["trk_spd"], "WAV_SPD": 0.0, "WBV_SPD": 0.0,
                "PVMT": "NL08", "TS_STUD": 0.0, "PM_STUD": 0.0, "JUNC_DIST": t["junc"], "JUNC_TYPE": 0, "WAY": 1, **meta}})
    buildings = [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [list(map(list, p.exterior.coords))]},
                  "properties": {"PK": i + 1, "HEIGHT": round(props[i]["height_m"], 3), "SOURCE_BLD_ID": props[i]["bld_id"],
                                 "HEIGHT_IMPUTED": props[i]["imputed"]}} for i, p in enumerate(polys)]
    # Sound walls are obstacles only: no facade receivers, BARRIER=1 keeps them out of public building assets.
    walls = load_walls(halo, args.walls) if args.walls else []
    for wall in walls:
        buildings.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [list(map(list, wall["polygon"].exterior.coords))]},
                          "properties": {"PK": len(buildings) + 1, "HEIGHT": round(wall["height"], 2), "SOURCE_BLD_ID": f"wall:{wall['id']}",
                                         "HEIGHT_IMPUTED": wall["imputed"], "BARRIER": 1}})
    crs = {"type": "name", "properties": {"name": "EPSG:26911"}}
    ground = {"type": "FeatureCollection", "crs": crs, "features": [{"type": "Feature", "geometry": halo.__geo_interface__, "properties": {"PK": 1, "G": 0.5, "SOURCE": "uniform G sensitivity assumption (pilot convention)"}}]}
    inputs = {
        "buildings.geojson": write_json(out / "input/buildings.geojson", {"type": "FeatureCollection", "crs": crs, "features": buildings}),
        "ground.geojson": write_json(out / "input/ground.geojson", ground),
        "periods.geojson": write_json(out / "input/periods.geojson", {"type": "FeatureCollection", "crs": crs, "features": periods}),
        "receivers.geojson": write_json(out / "input/receivers.geojson", {"type": "FeatureCollection", "crs": crs, "features": receivers}),
        "sources.geojson": write_json(out / "input/sources.geojson", {"type": "FeatureCollection", "crs": crs, "features": sources}),
    }
    terrain_path = out / "input/terrain.asc"
    inputs["terrain.asc"] = {"path": "input/terrain.asc", "sha256": sha(terrain_path), "bytes": terrain_path.stat().st_size}
    prefix = ("CTY_" + args.name.upper().replace("-", "_") + "_" + layout.upper())[:56]
    families = {}
    for r in receivers:
        families[r["properties"]["RECEIVER_FAMILY"]] = families.get(r["properties"]["RECEIVER_FAMILY"], 0) + 1
    manifest = {
        "schema_version": 1, "attempt_id": attempt_id, "region_key": f"county_{args.name.replace('-', '_')}_{layout}",
        "table_prefix": prefix, "counts": {"receivers": len(receivers), "sources": len(sources), "period_rows": len(periods)},
        "inputs": inputs, "write_scope": {"public_or_dev_writes": False, "raw_export_path_reserved_only": "export/receivers_level_d_e_n.csv"},
        "propagation_authorized": False, "public_output_writes_authorized": False,
        "tile": {"x0": x0, "y0": y0, "size_m": args.size, "halo_m": args.halo, "center_wgs84": list(TO_WGS.transform(x0 + args.size / 2, y0 + args.size / 2))},
        "receiver_design": {"grid_m": args.grid, "facade_spacing_m": args.facade_spacing, "dense_near_roads_m": args.dense_near_roads,
                            "dense_aadt": args.dense_aadt, "families": families},
        "buildings": {"count": len(buildings), "height_imputed": sum(p["imputed"] for p in props), "source": BUILDINGS_URL, "height_units": "metres from LARIAC feet"},
        "traffic": {"basis_counts": dict(sorted(basis_counts.items())), "hpms_records_in_halo": len(hpms), "class_defaults": CLASS_DEFAULTS,
                    "diurnal": "day hourly = AADT/16; evening = 0.6 x day; night = 0.2 x day (pilot convention)",
                    "split": "two-way AADT x 0.5 on divided carriageways"},
        "physics_contract": {"engine": "NoiseModelling 6.0.0", "pavement": "NL08", "ground_G": 0.5, "terrain_cell_m": args.dem_cell, "terrain_source": terrain_source, "sound_walls": len(walls), "sound_wall_source": args.walls.name if args.walls else None,
                             "vertical_convention": "receiver and source Z relative to imported terrain"},
        "input_hashes": {"tiger_edges_zip": sha(EDGES), "hpms_rows_jsonl": sha(HPMS)},
        "build_timings_s": timings,
        "claim_boundary": "Modeled, uncalibrated exterior road noise from documented traffic estimates and defaults; not measured or current traffic.",
    }
    (out / "attempt_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"attempt": str(out), "receivers": len(receivers), "families": families, "sources": len(sources),
                      "buildings": len(buildings), "imputed_heights": manifest["buildings"]["height_imputed"],
                      "traffic": manifest["traffic"]["basis_counts"], "timings": timings}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
