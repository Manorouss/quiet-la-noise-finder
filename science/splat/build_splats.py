#!/usr/bin/env python3
"""Turn lidar points + NAIP imagery into a Gaussian-splat scene (.splat) for one tile.

Points (ept_fetch.py .npz, EPSG:26911) are thinned on a voxel grid (ground coarser than
buildings and trees), coloured from NAIP 0.6 m orthoimagery (top-down, so walls take roof or
ground colours: an honest limitation of aerial data), and written in the common 32-byte
.splat layout: position (3 x float32, metres, x east, y north, z up, relative to --origin),
scale (3 x float32), RGBA (4 x uint8), rotation quaternion (4 x uint8, identity).
Ground splats are flat discs; everything else is round. A JSON sidecar gives the origin
(EPSG:26911 and lon/lat), count and sources.

Usage:
  build_splats.py --points lidar.npz --naip a.tif b.tif --origin 373000 3779000 --out scene.splat
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer

TO_LONLAT = Transformer.from_crs("EPSG:26911", "OGC:CRS84", always_xy=True)


def thin(x, y, z, voxel: float):
    """One point per voxel (the highest), returned as indices."""
    key = (np.floor(x / voxel).astype(np.int64) * 73856093) ^ (np.floor(y / voxel).astype(np.int64) * 19349663) ^ (np.floor(z / voxel).astype(np.int64) * 83492791)
    order = np.lexsort((-z, key))
    first = np.ones(len(order), dtype=bool)
    first[1:] = key[order][1:] != key[order][:-1]
    return order[first]


def colours(paths: list[Path], x: np.ndarray, y: np.ndarray) -> np.ndarray:
    rgb = np.zeros((len(x), 3), dtype=np.uint8)
    filled = np.zeros(len(x), dtype=bool)
    for path in paths:
        with rasterio.open(path) as src:
            rows, cols = rasterio.transform.rowcol(src.transform, x, y)
            rows, cols = np.asarray(rows), np.asarray(cols)
            inside = (~filled) & (rows >= 0) & (rows < src.height) & (cols >= 0) & (cols < src.width)
            if not inside.any():
                continue
            r0, r1, c0, c1 = rows[inside].min(), rows[inside].max() + 1, cols[inside].min(), cols[inside].max() + 1
            window = src.read([1, 2, 3], window=((r0, r1), (c0, c1)))
            rgb[inside] = np.moveaxis(window[:, rows[inside] - r0, cols[inside] - c0], 0, -1)
            filled |= inside
    return rgb


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--naip", type=Path, nargs="+", required=True)
    parser.add_argument("--origin", type=float, nargs=2, required=True, help="EPSG:26911 x y of the scene origin")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ground-voxel", type=float, default=0.55)
    parser.add_argument("--object-voxel", type=float, default=0.32)
    parser.add_argument("--box", type=float, nargs=4, default=None, help="optional EPSG:26911 clip box")
    args = parser.parse_args()
    d = np.load(args.points)
    x, y, z, cls = d["x"], d["y"], d["z"].astype(np.float64), d["cls"]
    keep = (cls != 7) & (cls != 18)  # drop noise
    if args.box:
        keep &= (x >= args.box[0]) & (x <= args.box[2]) & (y >= args.box[1]) & (y <= args.box[3])
    x, y, z, cls = x[keep], y[keep], z[keep], cls[keep]
    ground = cls == 2
    gi = np.flatnonzero(ground)[thin(x[ground], y[ground], z[ground], args.ground_voxel)]
    oi = np.flatnonzero(~ground)[thin(x[~ground], y[~ground], z[~ground], args.object_voxel)]
    idx = np.concatenate([gi, oi])
    is_ground = np.concatenate([np.ones(len(gi), bool), np.zeros(len(oi), bool)])
    px, py, pz = x[idx], y[idx], z[idx]
    rgb = colours(args.naip, px, py)
    z0 = float(np.percentile(pz[is_ground], 5)) if is_ground.any() else float(pz.min())
    n = len(idx)
    record = np.zeros(n, dtype=[("pos", "<f4", 3), ("scale", "<f4", 3), ("rgba", "u1", 4), ("rot", "u1", 4)])
    record["pos"] = np.column_stack([px - args.origin[0], py - args.origin[1], pz - z0])
    g, o = args.ground_voxel * 0.62, args.object_voxel * 0.62
    record["scale"] = np.where(is_ground[:, None], np.array([g, g, 0.04], dtype=np.float32), np.array([o, o, o], dtype=np.float32))
    record["rgba"][:, :3] = rgb
    record["rgba"][:, 3] = 255
    record["rot"] = np.array([255, 128, 128, 128], dtype=np.uint8)  # identity quaternion, bytes (w, x, y, z) = q * 128 + 128
    record.tofile(args.out)
    lon, lat = TO_LONLAT.transform(args.origin[0], args.origin[1])
    meta = {"format": "splat-32", "count": int(n), "ground": int(len(gi)), "objects": int(len(oi)), "origin_utm11n": args.origin, "origin_lonlat": [lon, lat],
            "origin_z_m": z0, "axes": "x east, y north, z up (metres)", "sources": ["USGS 3DEP lidar CA_LosAngeles_1_B23 (2023)", "USDA NAIP 2022 0.6 m (public domain)"]}
    args.out.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps({k: meta[k] for k in ("count", "ground", "objects")}), f"{args.out.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
