#!/usr/bin/env python3
"""Detect freeway sound walls in lidar points (from ept_fetch.py) and write them as GeoJSON lines.

Method, per 0.5 m cell within --corridor m of freeway (S1100) centrelines:
  height above ground = highest non-ground return - ground surface (class 2 minimum, gap-filled);
  candidate if 1.8-7 m high, mostly single returns (vegetation scatters into multiple returns),
  outside building footprints (+1 m), and thin (at most 45% of the surrounding 2.5 m window is
  raised, while tree crowns and roofs fill it). Connected candidate groups are kept when they are
  long and thin (>= --min-length m, mean width <= 1.6 m); each becomes a polyline through the
  group's cells ordered along its main direction, split where it bends, with the group's
  median height. Output (WGS84 GeoJSON) feeds build_county_tile.py --walls.

Usage:
  detect_walls.py --points pts.npz --sources sources.geojson --buildings buildings.geojson --out walls.geojson
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin
from shapely.geometry import LineString, mapping, shape
from shapely.ops import transform as shp_transform, unary_union

TO_LONLAT = Transformer.from_crs("EPSG:26911", "OGC:CRS84", always_xy=True)
CELL = 0.5


def fill_gaps(grid: np.ndarray, rounds: int = 60) -> np.ndarray:
    out = grid.copy()
    for _ in range(rounds):
        missing = np.isnan(out)
        if not missing.any():
            break
        pad = np.pad(out, 1, constant_values=np.nan)
        stack = np.stack([pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2], pad[1:-1, 2:]])
        with np.errstate(all="ignore"):
            neighbour = np.nanmean(stack, axis=0)
        out[missing] = neighbour[missing]
    return out


def label(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """8-connected components without scipy (union-find over rows)."""
    labels = np.zeros(mask.shape, dtype=np.int64)
    parent = [0]

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    rows, cols = mask.shape
    for r in range(rows):
        for c in np.flatnonzero(mask[r]):
            neighbours = [labels[r, c - 1]] if c > 0 and labels[r, c - 1] else []
            if r > 0:
                neighbours += [labels[r - 1, cc] for cc in (c - 1, c, c + 1) if 0 <= cc < cols and labels[r - 1, cc]]
            if not neighbours:
                parent.append(len(parent))
                labels[r, c] = len(parent) - 1
            else:
                roots = sorted({find(int(n)) for n in neighbours})
                labels[r, c] = roots[0]
                for other in roots[1:]:
                    parent[other] = roots[0]
    flat = np.array([find(i) for i in range(len(parent))])
    _, remap = np.unique(flat, return_inverse=True)
    return remap[labels], int(remap.max())


def polyline(xy: np.ndarray, max_gap: float = 4.0, chunk: float = 40.0) -> list[LineString]:
    """Order cells along the group's main axis, split at gaps and into ~chunk-m straight-ish pieces."""
    centre = xy.mean(axis=0)
    _, _, vt = np.linalg.svd(xy - centre, full_matrices=False)
    t = (xy - centre) @ vt[0]
    order = np.argsort(t)
    xy, t = xy[order], t[order]
    lines, start = [], 0
    for i in range(1, len(t) + 1):
        if i == len(t) or t[i] - t[i - 1] > max_gap or t[i] - t[start] > chunk:
            piece = xy[start:i]
            if len(piece) >= 4:
                bins = np.maximum(1, int((t[i - 1] - t[start]) / 2.0))
                edges = np.linspace(t[start], t[i - 1] + 1e-6, bins + 1)
                idx = np.digitize(t[start:i], edges) - 1
                pts = [piece[idx == b].mean(axis=0) for b in range(bins) if (idx == b).any()]
                if len(pts) >= 2:
                    lines.append(LineString(pts))
            start = i
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True, help="tile sources.geojson (EPSG:26911)")
    parser.add_argument("--buildings", type=Path, required=True, help="tile buildings.geojson (EPSG:26911)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--corridor", type=float, default=60.0)
    parser.add_argument("--min-length", type=float, default=25.0)
    args = parser.parse_args()
    d = np.load(args.points)
    x, y, z, cls, ret = d["x"], d["y"], d["z"], d["cls"], d["ret"]
    x0, y0 = np.floor(x.min()), np.floor(y.min())
    cols, rows = int(np.ceil((x.max() - x0) / CELL)) + 1, int(np.ceil((y.max() - y0) / CELL)) + 1
    ix, iy = ((x - x0) / CELL).astype(int), ((y - y0) / CELL).astype(int)
    ground = np.full((rows, cols), np.nan)
    top = np.full((rows, cols), np.nan)
    hits = np.zeros((rows, cols))
    multi = np.zeros((rows, cols))
    g = cls == 2
    np.fmin.at(ground, (iy[g], ix[g]), z[g])
    ng = (cls != 2) & (cls != 7) & (cls != 18)  # skip ground and noise
    np.fmax.at(top, (iy[ng], ix[ng]), z[ng])
    np.add.at(hits, (iy[ng], ix[ng]), 1)
    np.add.at(multi, (iy[ng], ix[ng]), (ret[ng] > 1).astype(float))
    height = top - fill_gaps(ground)
    transform = from_origin(x0, y0 + rows * CELL, CELL, CELL)
    freeways = unary_union([shape(f["geometry"]) for f in json.loads(args.sources.read_text())["features"] if f["properties"].get("MTFCC") == "S1100"])
    if freeways.is_empty:
        args.out.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
        print("no freeway in this area")
        return 0
    corridor = features.rasterize([(freeways.buffer(args.corridor), 1)], out_shape=(rows, cols), transform=transform)[::-1]
    footprints = [shape(f["geometry"]).buffer(1.0) for f in json.loads(args.buildings.read_text())["features"] if not f["properties"].get("BARRIER")]
    buildings = features.rasterize([(p, 1) for p in footprints], out_shape=(rows, cols), transform=transform)[::-1] if footprints else np.zeros((rows, cols))
    with np.errstate(invalid="ignore", divide="ignore"):
        single = np.where(hits > 0, 1 - multi / hits, 0)
        candidate = (height >= 1.8) & (height <= 7.0) & (single >= 0.6) & (corridor == 1) & (buildings == 0)
    # Thin-line test: around a wall cell most of a 2.5 m window is open ground; trees and roofs fill it.
    raised = (height >= 1.0).astype(float)
    window = 5
    padded = np.pad(raised, window // 2)
    summed = np.cumsum(np.cumsum(padded, axis=0), axis=1)
    summed = np.pad(summed, ((1, 0), (1, 0)))
    density = (summed[window:, window:] - summed[:-window, window:] - summed[window:, :-window] + summed[:-window, :-window]) / window ** 2
    candidate &= density <= 0.45
    # Bridge dashes (stretches beside trees fail the thin test) by grouping on a mask grown 2 cells.
    grown = candidate.copy()
    for _ in range(2):
        g = np.pad(grown, 1)
        grown = g[1:-1, 1:-1] | g[:-2, 1:-1] | g[2:, 1:-1] | g[1:-1, :-2] | g[1:-1, 2:] | g[:-2, :-2] | g[:-2, 2:] | g[2:, :-2] | g[2:, 2:]
    labels, count = label(grown)
    labels = np.where(candidate, labels, 0)
    walls = []
    for n in range(1, count + 1):
        rr, cc = np.nonzero(labels == n)
        if len(rr) < args.min_length / CELL:
            continue
        xy = np.column_stack([x0 + (cc + 0.5) * CELL, y0 + (rr + 0.5) * CELL])
        pieces = [line for line in polyline(xy) if line.length >= 5]
        total = sum(line.length for line in pieces)
        # Long and thin: a wall is one or two cells wide along its whole length.
        if total < args.min_length or len(rr) * CELL * CELL / total > 1.6:
            continue
        wall_height = f"{float(np.median(height[rr, cc])):.1f}"
        for line in pieces:
            walls.append({"type": "Feature", "properties": {"height": wall_height, "source": "lidar_detected", "length_m": round(line.length, 1)},
                          "geometry": mapping(shp_transform(lambda a, b, c=None: TO_LONLAT.transform(a, b), line))})
    args.out.write_text(json.dumps({"type": "FeatureCollection", "features": walls}) + "\n")
    print(f"{len(walls)} wall lines, {sum(w['properties']['length_m'] for w in walls) / 1000:.2f} km")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
