#!/usr/bin/env python3
"""Check a built field layer for halos around houses.

The map blends neighbouring field pixels linearly, so a house that is 'not modeled' (elevation 150) in the raster would ring
itself with loud colours, and a narrow gap between two houses would show as a hole. build_county_layers.py continues the surface
under every footprint (up to 15 m) to prevent that; this check proves it on the PRODUCED PMTiles: it samples the z15 field the way
the GPU does (bilinear between pixel centres) at points just OUTSIDE the footprints of a tile and fails if any of them reads as
'not modeled' (>= 100 dB) although the area around it is modeled.

  check_field_halos.py --layers <layers dir> --assets <released tiles dir> --tile cty-e361-n3794 [--tile ...] [--band d] [--max-dist 10]

Exit 1 when a tile has such points. Needs only numpy, shapely, pyproj and Pillow (geo-python is enough).
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import shapely
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import build_county_layers as bcl  # noqa: E402

ZOOM = 15
NOT_MODELED_FROM = 100.0


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _zxy_to_tileid(z: int, x: int, y: int) -> int:
    acc = sum((1 << i) * (1 << i) for i in range(z))
    n = 1 << z
    d, s = 0, n // 2
    while s > 0:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x, y = n - 1 - x, n - 1 - y
            x, y = y, x
        s //= 2
    return acc + d


class PMTiles:
    """Minimal PMTiles v3 reader for a local file."""

    def __init__(self, path: Path):
        self.f = open(path, "rb")
        header = self.f.read(127)
        if header[:7] != b"PMTiles" or header[7] != 3:
            raise SystemExit(f"{path}: not a PMTiles v3 file")
        (self.root_off, self.root_len, _, _, self.leaf_off, _, self.data_off, _) = struct.unpack("<8Q", header[8:72])
        self.internal_compression, self.tile_compression = header[97], header[98]
        self._entries: dict[int, tuple[int, int]] | None = None

    def _directory(self, offset: int, length: int):
        self.f.seek(offset)
        raw = self.f.read(length)
        if self.internal_compression == 2:
            raw = gzip.decompress(raw)
        pos = 0
        n, pos = _varint(raw, pos)
        ids, tid = [], 0
        for _ in range(n):
            delta, pos = _varint(raw, pos)
            tid += delta
            ids.append(tid)
        runs, lengths, offsets = [], [], []
        for _ in range(n):
            value, pos = _varint(raw, pos)
            runs.append(value)
        for _ in range(n):
            value, pos = _varint(raw, pos)
            lengths.append(value)
        for i in range(n):
            value, pos = _varint(raw, pos)
            offsets.append(offsets[i - 1] + lengths[i - 1] if value == 0 and i > 0 else value - 1)
        return list(zip(ids, runs, offsets, lengths))

    def entries(self) -> dict[int, tuple[int, int]]:
        if self._entries is None:
            found: dict[int, tuple[int, int]] = {}
            stack = [(self.root_off, self.root_len)]
            while stack:
                offset, length = stack.pop()
                for tid, run, off, ln in self._directory(offset, length):
                    if run == 0:
                        stack.append((self.leaf_off + off, ln))
                    else:
                        for k in range(run):
                            found[tid + k] = (off, ln)
            self._entries = found
        return self._entries

    def tile(self, z: int, x: int, y: int) -> bytes | None:
        entry = self.entries().get(_zxy_to_tileid(z, x, y))
        if entry is None:
            return None
        self.f.seek(self.data_off + entry[0])
        raw = self.f.read(entry[1])
        return gzip.decompress(raw) if self.tile_compression == 2 else raw


def decode_terrarium(png: bytes) -> np.ndarray:
    a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype(np.float64)
    return a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768


def world_px(lon, lat):
    n = 256 * 2 ** ZOOM
    return (np.asarray(lon) + 180.0) / 360.0 * n, (1 - np.arcsinh(np.tan(np.radians(np.asarray(lat)))) / math.pi) / 2 * n


def mosaic(pm: PMTiles, x0: float, y0: float, x1: float, y1: float):
    """The z15 raster over a UTM window as one array, with the world pixel of its top-left corner."""
    lons, lats = zip(*(bcl.TO_LONLAT.transform(x, y) for x in (x0, x1) for y in (y0, y1)))
    px, py = world_px(lons, lats)
    tx0, tx1, ty0, ty1 = int(px.min() // 256), int(px.max() // 256), int(py.min() // 256), int(py.max() // 256)
    big = np.full(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), 150.0)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            raw = pm.tile(ZOOM, tx, ty)
            if raw is not None:
                big[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = decode_terrarium(raw)
    return big, tx0 * 256, ty0 * 256


def bilinear(grid: np.ndarray, gx: np.ndarray, gy: np.ndarray) -> np.ndarray:
    """Bilinear sample at fractional pixel-centre coordinates (clamped), as the GPU does."""
    gx = np.clip(gx, 0, grid.shape[1] - 1.000001)
    gy = np.clip(gy, 0, grid.shape[0] - 1.000001)
    c0, r0 = np.floor(gx).astype(int), np.floor(gy).astype(int)
    wx, wy = gx - c0, gy - r0
    return (grid[r0, c0] * (1 - wx) * (1 - wy) + grid[r0, c0 + 1] * wx * (1 - wy) + grid[r0 + 1, c0] * (1 - wx) * wy + grid[r0 + 1, c0 + 1] * wx * wy)


def box_fraction(mask: np.ndarray, radius: int) -> np.ndarray:
    """Share of True pixels in a (2*radius+1) square around each pixel (integral image)."""
    padded = np.pad(mask.astype(np.float64), radius + 1)
    integral = padded.cumsum(0).cumsum(1)
    k = 2 * radius + 1
    total = integral[k:, k:] - integral[:-k, k:] - integral[k:, :-k] + integral[:-k, :-k]
    return total[: mask.shape[0], : mask.shape[1]] / (k * k)


def check_tile(layers: Path, assets: Path, tile_id: str, band: str, max_dist: float, step: float = 0.75) -> dict:
    manifest = json.loads((assets / tile_id / "build-manifest.json").read_text())
    origin = bcl.tile_origin(manifest)
    cx, cy = origin[0] // 1000, origin[1] // 1000
    paths = [assets / f"cty-e{cx + dx}-n{cy + dy}" for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (assets / f"cty-e{cx + dx}-n{cy + dy}" / "build-manifest.json").exists()]
    footprints = bcl.tile_footprints(origin, paths)
    if footprints is None:
        return {"tile": tile_id, "footprints": 0, "ok": True, "note": "no buildings"}
    xs = origin[0] + 60 + np.arange(0, 880, step)          # stay 60 m inside the tile: neighbours' tiles are not needed
    ys = origin[1] + 60 + np.arange(0, 880, step)
    X, Y = np.meshgrid(xs, ys)
    X, Y = X.ravel(), Y.ravel()
    inside = shapely.contains_xy(footprints, X, Y)
    near = shapely.distance(shapely.points(X, Y), shapely.boundary(footprints)) <= max_dist
    ring = ~inside & near
    X, Y = X[ring], Y[ring]
    lon, lat = bcl.TO_LONLAT.transform(X, Y)
    pm = PMTiles(layers / f"field_{band}.pmtiles")
    big, ox, oy = mosaic(pm, origin[0] + 30, origin[1] + 30, origin[0] + 970, origin[1] + 970)
    gx, gy = world_px(lon, lat)
    value = bilinear(big, gx - ox - 0.5, gy - oy - 0.5)
    modeled_around = box_fraction(big < NOT_MODELED_FROM, 5)    # share of modeled pixels within +-5 px (about 20 m)
    around = bilinear(modeled_around, gx - ox - 0.5, gy - oy - 0.5)
    holes = (value >= NOT_MODELED_FROM) & (around >= 0.75)
    return {"tile": tile_id, "band": band, "ring_points": int(len(X)), "points_not_modeled": int((value >= NOT_MODELED_FROM).sum()),
            "points_not_modeled_inside_modeled_area": int(holes.sum()), "max_db": round(float(np.nanmax(np.where(value < NOT_MODELED_FROM, value, np.nan))), 1), "ok": not holes.any()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--layers", type=Path, required=True)
    parser.add_argument("--assets", type=Path, default=bcl.PROJECT / "implementation/work/pipeline_release/county_all")
    parser.add_argument("--tile", action="append", required=True)
    parser.add_argument("--band", default="d")
    parser.add_argument("--max-dist", type=float, default=10.0, help="metres from a footprint wall")
    args = parser.parse_args()
    results = [check_tile(args.layers, args.assets, tile, args.band, args.max_dist) for tile in args.tile]
    print(json.dumps(results, indent=1))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
