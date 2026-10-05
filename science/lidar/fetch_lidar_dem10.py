#!/usr/bin/env python3
"""Build a 10 m lidar terrain cache for the county from USGS 1 m DEM cloud-optimized GeoTIFFs.

For every 10 km USGS tile that touches a target cell (DPW building density >= --min-buildings,
plus a 1.5 km halo), picks the preferred project (2023 LA survey first, then 2025 post-wildfire,
then 2016, then any other), reads the 4 m internal overview over HTTP (no full download) and
averages it onto a 10 m EPSG:26911 grid aligned to the tile. Output: <out>/x<X>y<Y>.tif plus
index.json (project per tile). Requires SSL_CERT_FILE/CURL_CA_BUNDLE to trust the system roots
(see geo-python-net).

Usage:
  fetch_lidar_dem10.py --index usgs_dem1m_index.json --out usgs_lidar_dem10 [--jobs 4]
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[4]
DENSITY = PROJECT / "implementation/work/county_building_density_1km.json"
PREFERENCE = ["CA_LosAngeles_B23", "CA_2025LosAngelesPostWildfire_C25", "CA_LosAngeles_2016", "CA_CaliforniaGaps_B23"]
CELL = 10.0


def tile_key(url: str) -> tuple[int, int] | None:
    match = re.search(r"x(\d+)y(\d+)", url.rsplit("/", 1)[-1])
    return (int(match.group(1)), int(match.group(2))) if match else None


def wanted_tiles(min_buildings: int, halo: float = 1500.0) -> set[tuple[int, int]]:
    tiles = set()
    for key, count in json.loads(DENSITY.read_text()).items():
        if count < min_buildings:
            continue
        cx, cy = map(int, key.split(","))
        for x in (cx * 1000 - halo, cx * 1000 + 1000 + halo):
            for y in (cy * 1000 - halo, cy * 1000 + 1000 + halo):
                tiles.add((int(x // 10000), int(y // 10000) + 1))
    return tiles


def fetch(url: str, key: tuple[int, int], out: Path) -> str:
    target = out / f"x{key[0]}y{key[1]}.tif"
    if target.exists():
        return "cached"
    x0, y1 = key[0] * 10000, key[1] * 10000
    size = int(10000 / CELL)
    transform = rasterio.transform.from_origin(x0, y1, CELL, CELL)
    grid = np.full((size, size), -9999.0, dtype=np.float32)
    with rasterio.open(f"/vsicurl/{url}") as src:
        factor = 4 if 4 in src.overviews(1) else (src.overviews(1) or [1])[0]
        data = src.read(1, out_shape=(src.height // factor, src.width // factor), resampling=Resampling.average)
        src_transform = src.transform * src.transform.scale(src.width / data.shape[1], src.height / data.shape[0])
        reproject(data, grid, src_transform=src_transform, src_crs=src.crs, src_nodata=src.nodata, dst_transform=transform, dst_crs="EPSG:26911",
                  dst_nodata=-9999.0, resampling=Resampling.average)
    tmp = target.with_suffix(".part.tif")
    with rasterio.open(tmp, "w", driver="GTiff", width=size, height=size, count=1, dtype="float32", crs="EPSG:26911", transform=transform,
                       nodata=-9999.0, compress="deflate", predictor=3, tiled=True) as dst:
        dst.write(grid, 1)
    tmp.rename(target)
    return f"{(grid == -9999.0).mean():.0%} empty"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--min-buildings", type=int, default=25)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    candidates: dict[tuple[int, int], list[str]] = {}
    for url in json.loads(args.index.read_text()):
        key = tile_key(url)
        if key:
            candidates.setdefault(key, []).append(url)
    choice = {}
    for key in sorted(wanted_tiles(args.min_buildings)):
        urls = candidates.get(key, [])
        ranked = sorted(urls, key=lambda u: next((i for i, p in enumerate(PREFERENCE) if f"/{p}/" in u), len(PREFERENCE)))
        if ranked:
            choice[key] = ranked[0]
    missing = sorted(wanted_tiles(args.min_buildings) - set(choice))
    print(f"{len(choice)} tiles to cache, {len(missing)} without 1 m lidar (fall back to the 10 m USGS DEM): {missing[:12]}")
    with ThreadPoolExecutor(args.jobs) as pool:
        results = dict(zip(choice, pool.map(lambda kv: fetch(kv[1], kv[0], args.out), choice.items())))
    index = {f"x{k[0]}y{k[1]}": {"url": choice[k], "project": choice[k].split("/Projects/")[1].split("/")[0], "result": results[k]} for k in choice}
    (args.out / "index.json").write_text(json.dumps({"cell_m": CELL, "tiles": index, "missing": [f"x{x}y{y}" for x, y in missing]}, indent=1) + "\n")
    print("done", {r: list(results.values()).count(r) for r in set(results.values())} if len(set(results.values())) < 6 else len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
