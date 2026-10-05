#!/usr/bin/env python3
"""Fetch USGS 3DEP lidar points for a small UTM area from the public Entwine (EPT) archive.

Reads https://s3-us-west-2.amazonaws.com/usgs-lidar-public/<dataset>/ (EPSG:3857 octree of
LAZ nodes), walks the hierarchy for nodes that intersect the area, downloads them with curl
(system trust store, works behind the proxy), decodes with laspy/lazrs and keeps the points
inside the area. --max-depth stops at a coarser octree level (CA_LosAngeles_1_B23: level 10
adds ~1.9 m point spacing, 11 ~1 m); --cache keeps hierarchy files and shallow nodes on disk.
Output: compressed .npz with x, y (EPSG:26911 metres), z (m), cls (ASPRS class), ret (return
number), intensity.

Usage:
  ept_fetch.py --box 373000 3779000 374000 3780000 --out points.npz [--dataset CA_LosAngeles_1_B23]
  ept_fetch.py --box ... --corridor-sources sources.geojson --corridor 70 --out corridor.npz
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import laspy
import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import box as shp_box, shape
from shapely.ops import transform as shp_transform, unary_union
from shapely.prepared import prep

BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public"
DATASET = "CA_LosAngeles_1_B23"
TO_MERC = Transformer.from_crs("EPSG:26911", "EPSG:3857", always_xy=True)
TO_UTM = Transformer.from_crs("EPSG:3857", "EPSG:26911", always_xy=True)
CACHE_DEPTH = 9  # nodes at or above this level are shared by many requests; cache them


def get(url: str, cache: Path | None = None) -> bytes:
    if cache is not None and cache.exists():
        return cache.read_bytes()
    data = subprocess.run(["curl", "-s", "-f", "--retry", "4", "--retry-delay", "3", "--max-time", "300", url],
                          capture_output=True, check=True).stdout
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.part")
        tmp.write_bytes(data)
        tmp.replace(cache)
    return data


def node_bounds(cube: list[float], key: str) -> tuple[float, float, float, float]:
    depth, x, y, _ = map(int, key.split("-"))
    size = (cube[3] - cube[0]) / 2 ** depth
    return cube[0] + x * size, cube[1] + y * size, cube[0] + (x + 1) * size, cube[1] + (y + 1) * size


def fetch_points(area_utm, dataset: str = DATASET, max_depth: int | None = None, cache: Path | None = None,
                 jobs: int = 8) -> dict[str, np.ndarray]:
    """Points inside a shapely area (EPSG:26911) as arrays x, y, z, cls, ret, intensity."""
    root = f"{BASE}/{dataset}"
    store = cache / dataset if cache else None
    ept = json.loads(get(f"{root}/ept.json", store / "ept.json" if store else None))
    cube = ept["bounds"]
    area = shp_transform(lambda x, y, z=None: TO_MERC.transform(x, y), area_utm)
    area_prep = prep(area)
    wanted, pending = [], ["0-0-0-0"]
    while pending:
        key = pending.pop()
        hierarchy = json.loads(get(f"{root}/ept-hierarchy/{key}.json", store / "ept-hierarchy" / f"{key}.json" if store else None))
        for node, count in hierarchy.items():
            depth = int(node.split("-")[0])
            if (max_depth is not None and depth > max_depth) or not area_prep.intersects(shp_box(*node_bounds(cube, node))):
                continue
            if count == -1:
                pending.append(node)
            elif count > 0:
                wanted.append(node)

    def fetch(node: str):
        depth = int(node.split("-")[0])
        data = get(f"{root}/ept-data/{node}.laz", store / "ept-data" / f"{node}.laz" if store and depth <= CACHE_DEPTH else None)
        with tempfile.NamedTemporaryFile(suffix=".laz") as handle:
            handle.write(data)
            handle.flush()
            las = laspy.read(handle.name)
        x, y = np.asarray(las.x), np.asarray(las.y)
        keep = shapely.contains_xy(area, x, y)
        return x[keep], y[keep], np.asarray(las.z)[keep], np.asarray(las.classification)[keep], np.asarray(las.return_number)[keep], np.asarray(las.intensity)[keep]

    with ThreadPoolExecutor(jobs) as pool:
        parts = list(pool.map(fetch, wanted))
    if not parts:
        empty = np.zeros(0)
        return {"x": empty, "y": empty, "z": empty.astype(np.float32), "cls": empty.astype(np.uint8), "ret": empty.astype(np.uint8),
                "intensity": empty.astype(np.uint16), "nodes": 0}
    x, y, z, cls, ret, inten = (np.concatenate([p[i] for p in parts]) for i in range(6))
    ux, uy = TO_UTM.transform(x, y)
    return {"x": np.asarray(ux, dtype=np.float64), "y": np.asarray(uy, dtype=np.float64), "z": z.astype(np.float32), "cls": cls.astype(np.uint8),
            "ret": ret.astype(np.uint8), "intensity": inten.astype(np.uint16), "nodes": len(wanted)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--box", type=float, nargs=4, required=True, metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="EPSG:26911")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", default=DATASET)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--max-depth", type=int, default=None)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument("--corridor-sources", type=Path, help="only fetch within --corridor m of freeway (S1100) lines in this EPSG:26911 GeoJSON")
    parser.add_argument("--corridor", type=float, default=70.0)
    args = parser.parse_args()
    area = shp_box(*args.box)
    if args.corridor_sources:
        lines = [shape(f["geometry"]) for f in json.loads(args.corridor_sources.read_text())["features"] if f["properties"].get("MTFCC") == "S1100"]
        area = unary_union(lines).buffer(args.corridor).intersection(area)
        if area.is_empty:
            raise SystemExit("no freeway corridor in the box")
    points = fetch_points(area, args.dataset, args.max_depth, args.cache, args.jobs)
    print(f"{points.pop('nodes')} nodes intersect the area")
    np.savez_compressed(args.out, **points)
    counts = np.bincount(points["cls"], minlength=20)
    print(f"{len(points['x']):,} points; classes {dict((i, int(c)) for i, c in enumerate(counts) if c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
