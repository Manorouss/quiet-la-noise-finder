#!/usr/bin/env python3
"""Turn released county tiles into PMTiles map layers for the county map.

Inputs are portal tile directories written by build_tile_assets.py (benchmark.geojson,
buildings.geojson, build-manifest.json), already QA-flagged (physical-ceiling masks,
on-road labels). Later roots win when a tile appears twice. Outputs, in --out:

  receivers.pmtiles  points: k integer id (selection only), d/e/n LAeq per period (absent when masked
                     or unavailable), q 24 h CNEL of roads plus aircraft, a aircraft CNEL (only
                     inside the estimated 55 CNEL line; aircraft.py), m masked, o on road, f facade (1)
                     or open space (0). All points from z16; thinned below.
  buildings.pmtiles  footprints: k integer id (selection only), h height (m), d/e/n/q highest facade
                     LAeq / CNEL, dl/el/nl/ql the quietest facade, a highest facade aircraft CNEL,
                     c count. layers.json carries percentiles of the loudest facade per period
                     (r: roads-only CNEL of the loudest d/e/n, as the map computes it with aircraft off).
  roads.pmtiles      modeled road sources clipped to each tile core: a AADT, c MTFCC,
                     nm name, t traffic basis (hpms | default). Minor roads appear later.
  field_{d,e,n,q,r}.pmtiles  raster-dem per period (q = 24 h CNEL, roads + aircraft; r = 24 h CNEL, roads only,
                     for the map with aircraft switched off) in Terrarium encoding with elevation = LAeq
                     (dB, 0.1 dB steps); elevation 150 means not modeled (one pixel of padding past the edge). Built by normalized
                     Gaussian convolution of the receiver values (dB) on a 5 m grid per
                     tile, using neighbours' receivers for seamless edges; gaps inside
                     large buildings are filled from a wider kernel within modeled tiles.
                     Web tiles sample that grid bilinearly.
  glow_{d,e,n,q,r}.pmtiles the same surface with a 24 m kernel, for the soft Glow style.
  coverage.geojson   modeled 1 km tiles, plus coverage_outline.geojson (dissolved edge).
  basemap/           Protomaps LA County basemap (hard link to the downloaded extract).
  context/           fire stations, heliports and airport noise contours (GeoJSON).
  layers.json        tile list, counts, encodings, file hashes and build provenance.

Usage:
  build_county_layers.py --tiles-root <dir> [--tiles-root <dir> ...] --out <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import Transformer
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as shp_transform, unary_union

from aircraft import MIN_YEAR, FLOOR_DB, aircraft_cnel, energy_sum, load_airports, road_cnel

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[4]
ATTEMPTS = PROJECT / "implementation/work/campaign/county_v1/attempts"
BASEMAP = PROJECT / "implementation/work/county_layers/basemap/la_county_20261004.pmtiles"
CONTEXT = PROJECT / "implementation/apps/quiet-la-web/public/_local-data/context"
TO_UTM = Transformer.from_crs("OGC:CRS84", "EPSG:26911", always_xy=True)
TO_LONLAT = Transformer.from_crs("EPSG:26911", "OGC:CRS84", always_xy=True)
PERIODS = ("D", "E", "N")
BANDS = "denqr"         # field rasters: the three periods, the 24 h CNEL (roads + aircraft) and roads-only CNEL
AIRPORT_CONTOURS = CONTEXT / "la_county_airport_noise_contours.geojson"
BUILDING_PERCENTILES: dict[str, list[float]] = {}  # filled by building_features, written to layers.json
CELL = 5.0              # field grid (m)
MARGIN = 100.0          # neighbour receivers used around each tile (m)
SIGMA, SIGMA_FILL, SIGMA_GLOW = 9.0, 30.0, 24.0
FIELD_MAX_ZOOM, FIELD_MIN_ZOOM = 15, 10
ROAD_MINZOOM = {"S1100": 10, "S1200": 11, "S1630": 12}  # other classes: 13


def laeq(props: dict, period: str) -> float | None:
    value = (props.get(period) or {}).get("laeq")
    if props.get("masked") or not isinstance(value, (int, float)) or value == -99 or not math.isfinite(value):
        return None
    return float(value)


def collect_tiles(roots: list[Path]) -> dict[str, Path]:
    tiles: dict[str, Path] = {}
    for root in roots:
        for manifest in sorted(root.glob("cty-*/build-manifest.json")):
            tiles[manifest.parent.name] = manifest.parent
    return tiles


def tile_origin(manifest: dict) -> tuple[int, int]:
    attempt = json.loads((ATTEMPTS / manifest["attempt_id"] / "attempt_manifest.json").read_text())
    return int(attempt["tile"]["x0"]), int(attempt["tile"]["y0"])


def write_ndjson(path: Path, features) -> int:
    n = 0
    with path.open("w") as handle:
        for feature in features:
            handle.write(json.dumps(feature, separators=(",", ":")) + "\n")
            n += 1
    return n


def tippecanoe(ndjson: Path, out: Path, layer: str, args: list[str]) -> None:
    subprocess.run(["tippecanoe", "-q", "-f", "-o", str(out), "-l", layer, "-P", *args, str(ndjson)], check=True)


# ---------------------------------------------------------------- vector layers

def receiver_features(tiles: dict[str, Path], points: dict[str, np.ndarray], airports, building_loudest: dict):
    # k is a small integer id (unique within one build, used only to highlight the selection): the full
    # receiver keys were most of the file size.
    k = 0
    for tile_id, path in tiles.items():
        data = json.loads((path / "benchmark.geojson").read_text())
        rows = []
        lonlat = np.array([f["geometry"]["coordinates"][:2] for f in data["features"]] or np.zeros((0, 2)))
        ux, uy = TO_UTM.transform(lonlat[:, 0], lonlat[:, 1]) if len(lonlat) else (np.zeros(0), np.zeros(0))
        air = aircraft_cnel(airports, np.asarray(ux), np.asarray(uy)) if len(lonlat) else np.zeros(0)
        for i, feature in enumerate(data["features"]):
            p = feature["properties"]
            lon, lat = feature["geometry"]["coordinates"][:2]
            k += 1
            out = {"k": k, "f": 1 if p["receiver_family"] == "building_facade_exterior" else 0}
            values = [laeq(p, period) for period in PERIODS]
            for name, value in zip("den", values):
                if value is not None:
                    out[name] = round(value, 1)
            cnel = road = None
            if all(v is not None for v in values):
                cnel = road = road_cnel(*values)
                if math.isfinite(air[i]):
                    out["a"] = round(float(air[i]), 1)
                    cnel = energy_sum(cnel, float(air[i]))
                out["q"] = round(cnel, 1)
                if p.get("building_key"):
                    best = building_loudest.setdefault(p["building_key"], {})
                    best["q"] = max(best.get("q", -1.0), out["q"])
                    best["ql"] = min(best.get("ql", 999.0), out["q"])
                    if "a" in out:
                        best["a"] = max(best.get("a", -1.0), out["a"])
            values += [cnel, road]
            if p.get("masked"):
                out["m"] = 1
            if p.get("on_road"):
                out["o"] = 1
            yield {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]}, "properties": out}
            if not p.get("masked") and all(v is not None for v in values):
                rows.append((lon, lat, *values))
        array = np.array(rows, dtype=np.float64) if rows else np.zeros((0, 2 + len(BANDS)))
        if len(array):
            x, y = TO_UTM.transform(array[:, 0], array[:, 1])
            array[:, 0], array[:, 1] = x, y
        points[tile_id] = array


def building_features(tiles: dict[str, Path], building_loudest: dict):
    merged: dict[str, dict] = {}
    for path in tiles.values():
        for feature in json.loads((path / "buildings.geojson").read_text())["features"]:
            p = feature["properties"]
            key = p["building_key"]
            out = {"k": key, "h": round(float(p.get("height_m") or 4.0), 1), "c": int(p.get("receiver_count") or 0)}
            for name, period in zip("den", PERIODS):
                stats = (p.get("periods") or {}).get(period) or {}
                for field, suffix in (("max", ""), ("min", "l")):
                    value = stats.get(field)
                    if isinstance(value, (int, float)) and math.isfinite(value) and value != -99:
                        out[name + suffix] = round(float(value), 1)
            previous = merged.get(key)
            if previous:  # same footprint released by two tiles: loudest and quietest facade over both
                prev = previous["properties"]
                for name in "den":
                    if name in prev and (name not in out or prev[name] > out[name]):
                        out[name] = prev[name]
                    if name + "l" in prev and (name + "l" not in out or prev[name + "l"] < out[name + "l"]):
                        out[name + "l"] = prev[name + "l"]
                out["c"] += prev["c"]
            merged[key] = {"type": "Feature", "geometry": feature["geometry"], "properties": out}
    for k, (key, feature) in enumerate(merged.items(), 1):  # small integer ids, as for receivers
        feature["properties"]["k"] = k
        feature["properties"].update(building_loudest.get(key, {}))
    # Percentiles of the loudest wall per period, so the map can say how a building compares.
    for name in "denqr":
        if name == "r":
            values = np.array([road_cnel(*(f["properties"][b] for b in "den")) for f in merged.values() if all(b in f["properties"] for b in "den")])
        else:
            values = np.array([f["properties"][name] for f in merged.values() if name in f["properties"]])
        if len(values):
            BUILDING_PERCENTILES[name] = [round(float(v), 1) for v in np.percentile(values, np.arange(101))]
    return merged.values()


def road_features(tiles: dict[str, Path]):
    to_lonlat = lambda x, y, z=None: TO_LONLAT.transform(x, y)
    for path in tiles.values():
        manifest = json.loads((path / "build-manifest.json").read_text())
        x0, y0 = tile_origin(manifest)
        core = box(x0, y0, x0 + 1000, y0 + 1000)
        sources = json.loads((ATTEMPTS / manifest["attempt_id"] / "input/sources.geojson").read_text())
        for feature in sources["features"]:
            p = feature["properties"]
            clipped = shape(feature["geometry"]).intersection(core)
            if clipped.is_empty or clipped.length < 0.5:
                continue
            basis = str(p.get("TRAFFIC_BASIS", ""))
            props = {"a": int(round(float(p.get("AADT_ASSIGNED") or 0))), "c": p.get("MTFCC", ""), "nm": p.get("FULLNAME") or "",
                     "t": "default" if basis.startswith("class_default") else "hpms"}
            yield {"type": "Feature", "tippecanoe": {"minzoom": ROAD_MINZOOM.get(props["c"], 13)},
                   "geometry": mapping(shp_transform(to_lonlat, clipped)), "properties": props}


# ---------------------------------------------------------------- field raster

def smooth(array: np.ndarray, sigma_px: float) -> np.ndarray:
    radius = int(math.ceil(3 * sigma_px))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma_px) ** 2)
    out = np.apply_along_axis(lambda v: np.convolve(v, kernel, mode="same"), 0, array)
    return np.apply_along_axis(lambda v: np.convolve(v, kernel, mode="same"), 1, out)


def tile_field(origin: tuple[int, int], pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(field, glow): values (bands, H, W) on the 5 m grid of the 1 km tile core, NaN where unknown."""
    x0, y0 = origin
    lo_x, lo_y = x0 - MARGIN, y0 - MARGIN
    size = int((1000 + 2 * MARGIN) / CELL)
    ix = np.floor((pts[:, 0] - lo_x) / CELL).astype(int)
    iy = np.floor((pts[:, 1] - lo_y) / CELL).astype(int)
    inside = (ix >= 0) & (ix < size) & (iy >= 0) & (iy < size)
    ix, iy, values = ix[inside], iy[inside], pts[inside, 2:2 + len(BANDS)]
    count = np.zeros((size, size))
    np.add.at(count, (iy, ix), 1.0)
    result = np.full((len(BANDS), size, size), np.nan)
    glow = np.full((len(BANDS), size, size), np.nan)
    den_small, den_fill, den_glow = smooth(count, SIGMA / CELL), smooth(count, SIGMA_FILL / CELL), smooth(count, SIGMA_GLOW / CELL)
    for band in range(len(BANDS)):
        total = np.zeros((size, size))
        np.add.at(total, (iy, ix), values[:, band])
        small, fill, wide = smooth(total, SIGMA / CELL), smooth(total, SIGMA_FILL / CELL), smooth(total, SIGMA_GLOW / CELL)
        with np.errstate(invalid="ignore", divide="ignore"):
            result[band] = np.where(den_small > 0.05, small / den_small, np.where(den_fill > 0.02, fill / den_fill, np.nan))
            glow[band] = np.where(den_glow > 0.02, wide / den_glow, np.nan)
    core = slice(int(MARGIN / CELL), int((MARGIN + 1000) / CELL))
    return result[:, core, core], glow[:, core, core]


def lonlat_of_pixels(z: int, tx: int, ty: int, size: int = 256) -> tuple[np.ndarray, np.ndarray]:
    n = 2 ** z
    px = (tx + (np.arange(size) + 0.5) / size) / n
    py = (ty + (np.arange(size) + 0.5) / size) / n
    lon = px * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1 - 2 * py))))
    return np.meshgrid(lon, lat)


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


NOT_MODELED_DB = 150.0  # far above any modeled level; the map renders >= 100 dB as clear


def pad_one_pixel(values: np.ndarray) -> np.ndarray:
    """Fill NaN pixels next to modeled ones with the mean of their modeled neighbours.

    The map samples the surface linearly, so a pixel blends with its neighbours; with one pixel of
    padding the blend towards the not-modeled value happens outside the coverage edge.
    """
    valid = np.isfinite(values)
    padded = np.pad(np.where(valid, values, 0.0), 1)
    weight = np.pad(valid.astype(float), 1)
    total = np.zeros_like(values, dtype=float)
    count = np.zeros_like(values, dtype=float)
    rows, cols = values.shape
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            if dy == 1 and dx == 1:
                continue
            total += padded[dy:dy + rows, dx:dx + cols]
            count += weight[dy:dy + rows, dx:dx + cols]
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(valid, values, np.where(count > 0, total / count, np.nan))


def encode(values: np.ndarray) -> bytes:
    """values (256, 256) dB with NaN → lossless Terrarium PNG (elevation = dB, 150 = not modeled).

    MapLibre's color-relief shader encodes its colour stops in the source encoding, so a
    standard encoding is required; the custom per-channel trick decodes but cannot be ramped.
    Not modeled is a high value so linear sampling at the coverage edge can only look louder.
    """
    values = pad_one_pixel(values)
    db = np.where(np.isnan(values), NOT_MODELED_DB, np.round(values, 1)) + 32768.0
    red = np.floor(db / 256.0)
    green = np.floor(db - red * 256.0)
    blue = np.round((db - red * 256.0 - green) * 256.0)
    rgb = np.stack([red, green, np.minimum(blue, 255)], axis=-1).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def downsample(children: dict[tuple[int, int], np.ndarray]) -> np.ndarray:
    bands = next(iter(children.values())).shape[0]
    big = np.full((bands, 512, 512), np.nan)
    for (dx, dy), values in children.items():
        big[:, dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = values
    blocks = big.reshape(bands, 256, 2, 256, 2)
    with np.errstate(invalid="ignore"):
        return np.nanmean(blocks, axis=(2, 4))


def build_field(fields: dict[tuple[int, int], np.ndarray], mbtiles: dict[str, Path]) -> int:
    dbs = {}
    for band, path in mbtiles.items():
        dbs[band] = sqlite3.connect(path)
        dbs[band].executescript("CREATE TABLE metadata (name text, value text); CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);")
    cells = list(fields)
    lons, lats = [], []
    for cx, cy in cells:
        for x, y in ((cx * 1000, cy * 1000), (cx * 1000 + 1000, cy * 1000 + 1000)):
            lon, lat = TO_LONLAT.transform(x, y)
            lons.append(lon), lats.append(lat)
    z = FIELD_MAX_ZOOM
    wanted = set()
    for cx, cy in cells:
        corners = [TO_LONLAT.transform(cx * 1000 + dx, cy * 1000 + dy) for dx in (0, 1000) for dy in (0, 1000)]
        xs, ys = zip(*(lonlat_to_tile(lon, lat, z) for lon, lat in corners))
        wanted |= {(tx, ty) for tx in range(min(xs), max(xs) + 1) for ty in range(min(ys), max(ys) + 1)}
    level: dict[tuple[int, int], np.ndarray] = {}
    for tx, ty in wanted:
        lon, lat = lonlat_of_pixels(z, tx, ty)
        x, y = TO_UTM.transform(lon, lat)
        values = np.full((len(mbtiles), 256, 256), np.nan)
        cx, cy = np.floor(x / 1000).astype(int), np.floor(y / 1000).astype(int)
        for cell in set(zip(cx.ravel().tolist(), cy.ravel().tolist())):
            if cell not in fields:
                continue
            mask = (cx == cell[0]) & (cy == cell[1])
            # Bilinear between 5 m cell centres (clamped at the tile edge) for a smooth surface.
            fx = np.clip((x[mask] - cell[0] * 1000) / CELL - 0.5, 0, 199)
            fy = np.clip((y[mask] - cell[1] * 1000) / CELL - 0.5, 0, 199)
            c0, r0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
            c1, r1 = np.minimum(c0 + 1, 199), np.minimum(r0 + 1, 199)
            wx, wy = fx - c0, fy - r0
            grid = fields[cell]
            corners = [(grid[:, r0, c0], (1 - wx) * (1 - wy)), (grid[:, r0, c1], wx * (1 - wy)), (grid[:, r1, c0], (1 - wx) * wy), (grid[:, r1, c1], wx * wy)]
            total = sum(np.nan_to_num(v) * w for v, w in corners)
            weight = sum(np.isfinite(v) * w for v, w in corners)
            with np.errstate(invalid="ignore", divide="ignore"):
                values[:, mask] = np.where(weight > 0.5, total / weight, np.nan)
        if np.isfinite(values).any():
            level[(tx, ty)] = values
    written = 0
    while True:
        for (tx, ty), values in level.items():
            for index, band in enumerate(mbtiles):
                dbs[band].execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (z, tx, 2 ** z - 1 - ty, encode(values[index])))
            written += 1
        if z == FIELD_MIN_ZOOM:
            break
        parents: dict[tuple[int, int], dict] = {}
        for (tx, ty), values in level.items():
            parents.setdefault((tx // 2, ty // 2), {})[(tx % 2, ty % 2)] = values
        level = {key: downsample(children) for key, children in parents.items()}
        z -= 1
    for band, db in dbs.items():
        meta = {"name": f"quiet-la-field-{band}", "format": "png", "type": "overlay", "minzoom": str(FIELD_MIN_ZOOM), "maxzoom": str(FIELD_MAX_ZOOM),
                "bounds": f"{min(lons)},{min(lats)},{max(lons)},{max(lats)}", "center": f"{(min(lons) + max(lons)) / 2},{(min(lats) + max(lats)) / 2},{FIELD_MIN_ZOOM + 3}",
                "description": {"q": "24 h CNEL, roads + aircraft", "r": "24 h CNEL, roads only"}.get(band, f"Road-noise LAeq ({band.upper()})") + ", Terrarium raster-dem: elevation = dB; 150 = not modeled"}
        db.executemany("INSERT INTO metadata VALUES (?, ?)", meta.items())
        db.commit()
        db.close()
    return written


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--tiles-root", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    started = time.time()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    tiles = collect_tiles([root.resolve() for root in args.tiles_root])
    if not tiles:
        raise SystemExit("no cty-* tiles found")
    manifests = {tile_id: json.loads((path / "build-manifest.json").read_text()) for tile_id, path in tiles.items()}
    origins = {tile_id: tile_origin(manifest) for tile_id, manifest in manifests.items()}
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        points: dict[str, np.ndarray] = {}
        airports, building_loudest = load_airports(AIRPORT_CONTOURS), {}
        n_receivers = write_ndjson(tmp / "receivers.ndjson", receiver_features(tiles, points, airports, building_loudest))
        tippecanoe(tmp / "receivers.ndjson", out / "receivers.pmtiles", "receivers", ["-Z11", "-z16", "-B16", "-r2", "--no-tile-size-limit"])
        n_buildings = write_ndjson(tmp / "buildings.ndjson", building_features(tiles, building_loudest))
        tippecanoe(tmp / "buildings.ndjson", out / "buildings.pmtiles", "buildings", ["-Z13", "-z16", "--no-tile-size-limit", "--no-feature-limit", "--no-tiny-polygon-reduction", "--no-simplification-of-shared-nodes", "--simplification=1"])
        n_roads = write_ndjson(tmp / "roads.ndjson", road_features(tiles))
        tippecanoe(tmp / "roads.ndjson", out / "roads.pmtiles", "roads", ["-Z10", "-z16", "--no-tile-size-limit"])
        by_cell = {(origins[t][0] // 1000, origins[t][1] // 1000): t for t in tiles}
        fields, glows = {}, {}
        for (cx, cy), tile_id in by_cell.items():
            nearby = [points[by_cell[(cx + dx, cy + dy)]] for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (cx + dx, cy + dy) in by_cell]
            fields[(cx, cy)], glows[(cx, cy)] = tile_field(origins[tile_id], np.vstack(nearby))
        n_field = build_field(fields, {band: tmp / f"field_{band}.mbtiles" for band in BANDS})
        build_field(glows, {band: tmp / f"glow_{band}.mbtiles" for band in BANDS})
        for kind in ("field", "glow"):
            for band in BANDS:
                subprocess.run(["pmtiles", "convert", str(tmp / f"{kind}_{band}.mbtiles"), str(out / f"{kind}_{band}.pmtiles")], check=True, capture_output=True)
        (out / "field.pmtiles").unlink(missing_ok=True)
    coverage = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"tile_id": t, "model": manifests[t].get("study_model", "county-v1")},
         "geometry": {"type": "Polygon", "coordinates": [[list(TO_LONLAT.transform(origins[t][0] + dx, origins[t][1] + dy)) for dx, dy in ((0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0))]]}}
        for t in sorted(tiles)]}
    (out / "coverage.geojson").write_text(json.dumps(coverage, separators=(",", ":")) + "\n")
    outline = unary_union([box(x, y, x + 1000, y + 1000) for x, y in origins.values()])
    outline_geojson = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {},
                       "geometry": mapping(shp_transform(lambda x, y, z=None: TO_LONLAT.transform(x, y), outline.boundary))}]}
    (out / "coverage_outline.geojson").write_text(json.dumps(outline_geojson, separators=(",", ":")) + "\n")
    extra = ["coverage_outline.geojson"]
    (out / "basemap").mkdir(exist_ok=True)
    target = out / "basemap" / BASEMAP.name
    if BASEMAP.exists() and not target.exists():
        try:
            os.link(BASEMAP, target)
        except OSError:
            shutil.copy2(BASEMAP, target)
    if target.exists():
        extra.append(f"basemap/{BASEMAP.name}")
    (out / "context").mkdir(exist_ok=True)
    for path in sorted(CONTEXT.glob("*.geojson")):
        shutil.copy2(path, out / "context" / path.name)
        extra.append(f"context/{path.name}")
    # The estimated aircraft contours beyond the official lines (aircraft.py), drawn dashed on the map.
    estimated = []
    for airport in airports:
        for level in airport.levels:
            if level in airport.official:
                continue
            line = shp_transform(lambda x, y, z=None: TO_LONLAT.transform(x, y), airport.contour[level].boundary.simplify(5.0))
            estimated.append({"type": "Feature", "properties": {"AIRPORT_NAME": airport.name, "CLASS": level, "estimated": True}, "geometry": mapping(line)})
    (out / "context" / "airport_noise_estimated.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": estimated}, separators=(",", ":")) + "\n")
    extra.append("context/airport_noise_estimated.geojson")
    layers = {
        "schema": "quiet_la_county_layers_v1", "built_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tiles": sorted(tiles), "receiver_count": n_receivers, "building_count": n_buildings, "road_segments": n_roads, "field_tiles": n_field,
        "field_encoding": {"type": "terrarium", "files": {"D": "field_d.pmtiles", "E": "field_e.pmtiles", "N": "field_n.pmtiles", "Q": "field_q.pmtiles", "R": "field_r.pmtiles"}, "db": "elevation (0.1 dB steps)", "nodata": NOT_MODELED_DB, "edge_padding_px": 1,
                           "cell_m": CELL, "sigma_m": SIGMA, "fill_sigma_m": SIGMA_FILL, "glow_sigma_m": SIGMA_GLOW, "zooms": [FIELD_MIN_ZOOM, FIELD_MAX_ZOOM]},
        "files": {name: {"sha256": sha(out / name), "bytes": (out / name).stat().st_size}
                  for name in ("receivers.pmtiles", "buildings.pmtiles", "roads.pmtiles", *(f"{k}_{b}.pmtiles" for k in ("field", "glow") for b in BANDS), "coverage.geojson", *extra)},
        "building_percentiles": {"what": "percentiles 0..100 of the loudest facade level per building, per period (d/e/n LAeq, q 24 h CNEL, r roads-only CNEL)", **BUILDING_PERCENTILES},
        "aircraft": {"method": "official airport CNEL contours, extended to 55 dB by each airport's contour area ratio (aircraft.py)",
                     "floor_db": FLOOR_DB, "min_source_year": MIN_YEAR,
                     "airports": [{"name": a.name, "official_levels": sorted(a.official), "area_ratio": round(a.ratio, 2), "source": a.source} for a in airports]},
        "builder": "science/pipeline/build_county_layers.py", "seconds": round(time.time() - started, 1),
    }
    (out / "layers.json").write_text(json.dumps(layers, indent=1) + "\n")
    print(json.dumps({k: layers[k] for k in ("receiver_count", "building_count", "road_segments", "field_tiles", "seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
