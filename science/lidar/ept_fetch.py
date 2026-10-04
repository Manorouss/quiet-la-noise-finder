#!/usr/bin/env python3
"""Fetch USGS 3DEP lidar points for a small UTM box from the public Entwine (EPT) archive.

Reads https://s3-us-west-2.amazonaws.com/usgs-lidar-public/<dataset>/ (EPSG:3857 octree of
LAZ nodes), walks the hierarchy for nodes that intersect the box, downloads them with curl
(system trust store, works behind the proxy), decodes with laspy/lazrs and keeps the points
inside the box. Output: compressed .npz with x, y (EPSG:26911 metres), z (m), cls (ASPRS
class), ret (return number), intensity.

Usage:
  ept_fetch.py --box 373000 3779000 374000 3780000 --out points.npz [--dataset CA_LosAngeles_1_B23]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import laspy
import numpy as np
from pyproj import Transformer

BASE = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public"
TO_MERC = Transformer.from_crs("EPSG:26911", "EPSG:3857", always_xy=True)
TO_UTM = Transformer.from_crs("EPSG:3857", "EPSG:26911", always_xy=True)


def get(url: str) -> bytes:
    return subprocess.run(["curl", "-s", "-f", "--retry", "3", url], capture_output=True, check=True).stdout


def node_bounds(cube: list[float], key: str) -> tuple[float, float, float, float]:
    depth, x, y, _ = map(int, key.split("-"))
    size = (cube[3] - cube[0]) / 2 ** depth
    return cube[0] + x * size, cube[1] + y * size, cube[0] + (x + 1) * size, cube[1] + (y + 1) * size


def intersects(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--box", type=float, nargs=4, required=True, metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="EPSG:26911")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dataset", default="CA_LosAngeles_1_B23")
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    root = f"{BASE}/{args.dataset}"
    ept = json.loads(get(f"{root}/ept.json"))
    cube = ept["bounds"]
    xs, ys = TO_MERC.transform([args.box[0], args.box[2], args.box[0], args.box[2]], [args.box[1], args.box[1], args.box[3], args.box[3]])
    box3857 = (min(xs), min(ys), max(xs), max(ys))
    wanted, pending = [], ["0-0-0-0"]
    while pending:
        hierarchy = json.loads(get(f"{root}/ept-hierarchy/{pending.pop()}.json"))
        for key, count in hierarchy.items():
            if not intersects(node_bounds(cube, key), box3857):
                continue
            if count == -1:
                pending.append(key)
            elif count > 0:
                wanted.append(key)
    print(f"{len(wanted)} nodes intersect the box")
    parts = []

    def fetch(key: str):
        with tempfile.NamedTemporaryFile(suffix=".laz") as handle:
            handle.write(get(f"{root}/ept-data/{key}.laz"))
            handle.flush()
            las = laspy.read(handle.name)
        x, y = np.asarray(las.x), np.asarray(las.y)
        keep = (x >= box3857[0]) & (x <= box3857[2]) & (y >= box3857[1]) & (y <= box3857[3])
        return x[keep], y[keep], np.asarray(las.z)[keep], np.asarray(las.classification)[keep], np.asarray(las.return_number)[keep], np.asarray(las.intensity)[keep]

    with ThreadPoolExecutor(args.jobs) as pool:
        parts = list(pool.map(fetch, wanted))
    x, y, z, cls, ret, inten = (np.concatenate([p[i] for p in parts]) for i in range(6))
    ux, uy = TO_UTM.transform(x, y)
    inside = (ux >= args.box[0]) & (ux <= args.box[2]) & (uy >= args.box[1]) & (uy <= args.box[3])
    np.savez_compressed(args.out, x=ux[inside].astype(np.float64), y=uy[inside].astype(np.float64), z=z[inside].astype(np.float32),
                        cls=cls[inside].astype(np.uint8), ret=ret[inside].astype(np.uint8), intensity=inten[inside].astype(np.uint16))
    counts = np.bincount(cls[inside], minlength=20)
    print(f"{int(inside.sum()):,} points; classes {dict((i, int(c)) for i, c in enumerate(counts) if c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
