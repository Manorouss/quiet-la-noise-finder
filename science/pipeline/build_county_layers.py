#!/usr/bin/env python3
"""Turn released county tiles into PMTiles map layers for the county map.

Inputs are portal tile directories written by build_tile_assets.py (benchmark.geojson,
buildings.geojson, build-manifest.json), already QA-flagged (physical-ceiling masks,
on-road labels). Later roots win when a tile appears twice. Outputs, in --out:

  receivers.pmtiles  points: k receiver key, d/e/n LAeq per period (absent when masked
                     or unavailable), m masked, o on road, f facade (1) or open space (0),
                     b building key (facade only). All points from z16; thinned below.
  buildings.pmtiles  footprints: k key, h height (m), d/e/n highest facade LAeq, c count.
  roads.pmtiles      modeled road sources clipped to each tile core: a AADT, c MTFCC,
                     nm name, t traffic basis (hpms | default). Minor roads appear later.
  field.pmtiles      raster-dem, custom encoding: R/G/B = Day/Evening/Night, value v
                     means 25 + 0.3 v dB; 0 means not modeled. Built by normalized
                     Gaussian convolution of the receiver values (dB) on a 5 m grid per
                     tile, using neighbours' receivers for seamless edges; gaps inside
                     large buildings are filled from a wider kernel within modeled tiles.
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

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[4]
ATTEMPTS = PROJECT / "implementation/work/campaign/county_v1/attempts"
BASEMAP = PROJECT / "implementation/work/county_layers/basemap/la_county_20261004.pmtiles"
CONTEXT = PROJECT / "implementation/apps/quiet-la-web/public/_local-data/context"
TO_UTM = Transformer.from_crs("OGC:CRS84", "EPSG:26911", always_xy=True)
TO_LONLAT = Transformer.from_crs("EPSG:26911", "OGC:CRS84", always_xy=True)
PERIODS = ("D", "E", "N")
CELL = 5.0              # field grid (m)
MARGIN = 100.0          # neighbour receivers used around each tile (m)
SIGMA, SIGMA_FILL = 9.0, 30.0
DB0, DB_STEP = 25.0, 0.3
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

def receiver_features(tiles: dict[str, Path], points: dict[str, np.ndarray]):
    for tile_id, path in tiles.items():
        data = json.loads((path / "benchmark.geojson").read_text())
        rows = []
        for feature in data["features"]:
            p = feature["properties"]
            lon, lat = feature["geometry"]["coordinates"][:2]
            out = {"k": p["receiver_key"], "f": 1 if p["receiver_family"] == "building_facade_exterior" else 0}
            values = [laeq(p, period) for period in PERIODS]
            for name, value in zip("den", values):
                if value is not None:
                    out[name] = round(value, 1)
            if p.get("masked"):
                out["m"] = 1
            if p.get("on_road"):
                out["o"] = 1
            if p.get("building_key"):
                out["b"] = p["building_key"]
            yield {"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lon, 7), round(lat, 7)]}, "properties": out}
            if not p.get("masked") and all(v is not None for v in values):
                rows.append((lon, lat, *values))
        array = np.array(rows, dtype=np.float64) if rows else np.zeros((0, 5))
        if len(array):
            x, y = TO_UTM.transform(array[:, 0], array[:, 1])
            array[:, 0], array[:, 1] = x, y
        points[tile_id] = array


def building_features(tiles: dict[str, Path]):
    merged: dict[str, dict] = {}
    for path in tiles.values():
        for feature in json.loads((path / "buildings.geojson").read_text())["features"]:
            p = feature["properties"]
            key = p["building_key"]
            out = {"k": key, "h": round(float(p.get("height_m") or 4.0), 1), "c": int(p.get("receiver_count") or 0)}
            for name, period in zip("den", PERIODS):
                value = ((p.get("periods") or {}).get(period) or {}).get("max")
                if isinstance(value, (int, float)) and math.isfinite(value) and value != -99:
                    out[name] = round(float(value), 1)
            previous = merged.get(key)
            if previous:  # same footprint released by two tiles: keep the loudest facade per period
                for name in "den":
                    if name in previous and (name not in out or previous["properties"][name] > out[name]):
                        out[name] = previous["properties"][name]
                out["c"] += previous["properties"]["c"]
            merged[key] = {"type": "Feature", "geometry": feature["geometry"], "properties": out}
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


def tile_field(origin: tuple[int, int], pts: np.ndarray) -> np.ndarray:
    """Values (3, H, W) on the 5 m grid of the 1 km tile core, NaN where unknown."""
    x0, y0 = origin
    lo_x, lo_y = x0 - MARGIN, y0 - MARGIN
    size = int((1000 + 2 * MARGIN) / CELL)
    ix = np.floor((pts[:, 0] - lo_x) / CELL).astype(int)
    iy = np.floor((pts[:, 1] - lo_y) / CELL).astype(int)
    inside = (ix >= 0) & (ix < size) & (iy >= 0) & (iy < size)
    ix, iy, values = ix[inside], iy[inside], pts[inside, 2:5]
    count = np.zeros((size, size))
    np.add.at(count, (iy, ix), 1.0)
    result = np.full((3, size, size), np.nan)
    den_small, den_fill = smooth(count, SIGMA / CELL), smooth(count, SIGMA_FILL / CELL)
    for band in range(3):
        total = np.zeros((size, size))
        np.add.at(total, (iy, ix), values[:, band])
        small, fill = smooth(total, SIGMA / CELL), smooth(total, SIGMA_FILL / CELL)
        with np.errstate(invalid="ignore", divide="ignore"):
            field = np.where(den_small > 0.05, small / den_small, np.where(den_fill > 0.02, fill / den_fill, np.nan))
        result[band] = field
    core = slice(int(MARGIN / CELL), int((MARGIN + 1000) / CELL))
    return result[:, core, core]


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


def encode(values: np.ndarray) -> bytes:
    """values (3, 256, 256) dB with NaN → lossless RGB PNG in the custom DEM encoding."""
    codes = np.where(np.isnan(values), 0, np.clip(np.rint((values - DB0) / DB_STEP), 1, 255)).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(np.moveaxis(codes, 0, -1), "RGB").save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def downsample(children: dict[tuple[int, int], np.ndarray]) -> np.ndarray:
    big = np.full((3, 512, 512), np.nan)
    for (dx, dy), values in children.items():
        big[:, dy * 256:(dy + 1) * 256, dx * 256:(dx + 1) * 256] = values
    blocks = big.reshape(3, 256, 2, 256, 2)
    with np.errstate(invalid="ignore"):
        return np.nanmean(blocks, axis=(2, 4))


def build_field(fields: dict[tuple[int, int], np.ndarray], mbtiles: Path) -> int:
    db = sqlite3.connect(mbtiles)
    db.executescript("CREATE TABLE metadata (name text, value text); CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob);")
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
        values = np.full((3, 256, 256), np.nan)
        cx, cy = np.floor(x / 1000).astype(int), np.floor(y / 1000).astype(int)
        for cell in set(zip(cx.ravel().tolist(), cy.ravel().tolist())):
            if cell not in fields:
                continue
            mask = (cx == cell[0]) & (cy == cell[1])
            col = np.clip(((x[mask] - cell[0] * 1000) / CELL).astype(int), 0, 199)
            row = np.clip(((y[mask] - cell[1] * 1000) / CELL).astype(int), 0, 199)
            values[:, mask] = fields[cell][:, row, col]
        if np.isfinite(values).any():
            level[(tx, ty)] = values
    written = 0
    while True:
        for (tx, ty), values in level.items():
            db.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (z, tx, 2 ** z - 1 - ty, encode(values)))
            written += 1
        if z == FIELD_MIN_ZOOM:
            break
        parents: dict[tuple[int, int], dict] = {}
        for (tx, ty), values in level.items():
            parents.setdefault((tx // 2, ty // 2), {})[(tx % 2, ty % 2)] = values
        level = {key: downsample(children) for key, children in parents.items()}
        z -= 1
    meta = {"name": "quiet-la-field", "format": "png", "type": "overlay", "minzoom": str(FIELD_MIN_ZOOM), "maxzoom": str(FIELD_MAX_ZOOM),
            "bounds": f"{min(lons)},{min(lats)},{max(lons)},{max(lats)}", "center": f"{(min(lons) + max(lons)) / 2},{(min(lats) + max(lats)) / 2},{FIELD_MIN_ZOOM + 3}",
            "description": f"Road-noise LAeq; R/G/B = D/E/N; dB = {DB0} + {DB_STEP} * value; 0 = not modeled"}
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
        n_receivers = write_ndjson(tmp / "receivers.ndjson", receiver_features(tiles, points))
        tippecanoe(tmp / "receivers.ndjson", out / "receivers.pmtiles", "receivers", ["-Z11", "-z16", "-B16", "-r2", "--no-tile-size-limit"])
        n_buildings = write_ndjson(tmp / "buildings.ndjson", building_features(tiles))
        tippecanoe(tmp / "buildings.ndjson", out / "buildings.pmtiles", "buildings", ["-Z13", "-z16", "--no-tile-size-limit", "--no-feature-limit"])
        n_roads = write_ndjson(tmp / "roads.ndjson", road_features(tiles))
        tippecanoe(tmp / "roads.ndjson", out / "roads.pmtiles", "roads", ["-Z10", "-z16", "--no-tile-size-limit"])
        by_cell = {(origins[t][0] // 1000, origins[t][1] // 1000): t for t in tiles}
        fields = {}
        for (cx, cy), tile_id in by_cell.items():
            nearby = [points[by_cell[(cx + dx, cy + dy)]] for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (cx + dx, cy + dy) in by_cell]
            fields[(cx, cy)] = tile_field(origins[tile_id], np.vstack(nearby))
        n_field = build_field(fields, tmp / "field.mbtiles")
        subprocess.run(["pmtiles", "convert", str(tmp / "field.mbtiles"), str(out / "field.pmtiles")], check=True, capture_output=True)
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
    layers = {
        "schema": "quiet_la_county_layers_v1", "built_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tiles": sorted(tiles), "receiver_count": n_receivers, "building_count": n_buildings, "road_segments": n_roads, "field_tiles": n_field,
        "field_encoding": {"type": "custom", "red": "D", "green": "E", "blue": "N", "db": f"{DB0} + {DB_STEP} * value", "nodata": 0,
                           "cell_m": CELL, "sigma_m": SIGMA, "fill_sigma_m": SIGMA_FILL, "zooms": [FIELD_MIN_ZOOM, FIELD_MAX_ZOOM]},
        "files": {name: {"sha256": sha(out / name), "bytes": (out / name).stat().st_size}
                  for name in ("receivers.pmtiles", "buildings.pmtiles", "roads.pmtiles", "field.pmtiles", "coverage.geojson", *extra)},
        "builder": "science/pipeline/build_county_layers.py", "seconds": round(time.time() - started, 1),
    }
    (out / "layers.json").write_text(json.dumps(layers, indent=1) + "\n")
    print(json.dumps({k: layers[k] for k in ("receiver_count", "building_count", "road_segments", "field_tiles", "seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
