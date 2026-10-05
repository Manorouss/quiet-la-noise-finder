#!/usr/bin/env python3
"""Detect freeway sound walls in lidar points (from ept_fetch.py) and write them as GeoJSON lines.

Method, per 0.5 m cell within --corridor m of freeway (S1100) centrelines:
  height above ground = highest non-ground return - ground surface (class 2 minimum, gap-filled);
  candidate if 1.8-7 m high, mostly single returns (vegetation scatters into multiple returns),
  outside building footprints (+1 m) and bridge decks (class 17, +1 m), thin (at most --density
  of the surrounding 2.5 m window is raised, while tree crowns and roofs fill it), and open on
  the road side (1-2 m toward the nearest freeway/ramp centreline is below 1 m, which rejects the
  edges of decks, roofs and canopies). Each connected candidate group becomes
  polyline pieces through its cells, ordered along its main direction and cut at gaps and every
  ~40 m (2 m bins that jump >1.5 m off the line are dropped). A piece is kept when it is thin (mean width <= 1.6 m) and runs within --max-angle
  degrees of the nearest freeway or ramp (S1100/S1630), which drops tree edges and fences that
  cross the corridor; a group needs >= --min-length m of kept pieces. Walls get the group's
  median height. Output (WGS84 GeoJSON) feeds build_county_tile.py --walls.

Usage:
  detect_walls.py --points pts.npz --sources sources.geojson --buildings buildings.geojson --out walls.geojson
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from pyproj import Transformer
from rasterio import features
from rasterio.transform import from_origin
import shapely
from shapely.geometry import LineString, MultiLineString, box, mapping, shape
from shapely.ops import transform as shp_transform, unary_union
from shapely.strtree import STRtree

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


def grow(mask: np.ndarray, cells: int) -> np.ndarray:
    """Dilate a boolean mask by `cells` (8-neighbourhood)."""
    for _ in range(cells):
        g = np.pad(mask, 1)
        mask = g[1:-1, 1:-1] | g[:-2, 1:-1] | g[2:, 1:-1] | g[1:-1, :-2] | g[1:-1, 2:] | g[:-2, :-2] | g[:-2, 2:] | g[2:, :-2] | g[2:, 2:]
    return mask


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


def polyline(xy: np.ndarray, max_gap: float = 4.0, chunk: float = 40.0) -> list[tuple[LineString, int]]:
    """Order cells along the group's main axis, split at gaps and into ~chunk-m straight-ish pieces.

    Returns (line, number of cells) per piece."""
    centre = xy.mean(axis=0)
    _, _, vt = np.linalg.svd(xy - centre, full_matrices=False)
    t, s = (xy - centre) @ vt[0], (xy - centre) @ vt[1]
    order = np.argsort(t)
    t, s = t[order], s[order]
    lines, start = [], 0
    for i in range(1, len(t) + 1):
        if i == len(t) or t[i] - t[i - 1] > max_gap or t[i] - t[start] > chunk:
            if i - start >= 4:
                bins = np.maximum(1, int((t[i - 1] - t[start]) / 2.0))
                edges = np.linspace(t[start], t[i - 1] + 1e-6, bins + 1)
                idx = np.digitize(t[start:i], edges) - 1
                tb = np.array([t[start:i][idx == b].mean() for b in range(bins) if (idx == b).any()])
                sb = np.array([np.median(s[start:i][idx == b]) for b in range(bins) if (idx == b).any()])
                # Drop 2 m bins that jump off the line (posts, gantries, a tree edge joined to the wall).
                if len(sb) >= 3:
                    smooth = np.array([np.median(sb[max(0, k - 2):k + 3]) for k in range(len(sb))])
                    keep = np.abs(sb - smooth) <= 1.5
                    tb, sb = tb[keep], sb[keep]
                if len(tb) >= 2:
                    lines.append((LineString(centre + np.outer(tb, vt[0]) + np.outer(sb, vt[1])), i - start))
            start = i
    return lines


def heading(line: LineString) -> float:
    (ax, ay), (bx, by) = line.coords[0][:2], line.coords[-1][:2]
    return math.atan2(by - ay, bx - ax)


class References:
    """Freeway and ramp centrelines for the orientation test."""

    def __init__(self, lines):
        self.lines = [part for line in lines for part in getattr(line, "geoms", [line]) if part.length > 1]
        self.tree = STRtree(self.lines)

    def near(self, area) -> MultiLineString:
        return MultiLineString([[c[:2] for c in self.lines[int(j)].coords] for j in self.tree.query(area)])

    def direction_at(self, point, radius: float = 120.0) -> float | None:
        near = self.tree.query(point.buffer(radius))
        if len(near) == 0:
            return None
        ref = min((self.lines[int(j)] for j in near), key=lambda g: g.distance(point))
        d = ref.project(point)
        return heading(LineString([ref.interpolate(max(0.0, d - 10.0)), ref.interpolate(min(ref.length, d + 10.0))]))


def aligned(line: LineString, references: References, max_angle: float) -> bool:
    ref = references.direction_at(line.interpolate(0.5, normalized=True))
    if ref is None:
        return False
    angle = abs(math.degrees(heading(line) - ref)) % 180
    return min(angle, 180 - angle) <= max_angle


def detect_block(x, y, z, cls, ret, freeways, footprints, references, args) -> list[dict]:
    x0, y0 = np.floor(x.min()), np.floor(y.min())
    cols, rows = int(np.ceil((x.max() - x0) / CELL)) + 1, int(np.ceil((y.max() - y0) / CELL)) + 1
    ix, iy = ((x - x0) / CELL).astype(int), ((y - y0) / CELL).astype(int)
    ground = np.full((rows, cols), np.nan)
    top = np.full((rows, cols), np.nan)
    hits = np.zeros((rows, cols))
    multi = np.zeros((rows, cols))
    g = cls == 2
    np.fmin.at(ground, (iy[g], ix[g]), z[g])
    ng = (cls != 2) & (cls != 7) & (cls != 18) & (cls != 17)  # skip ground, noise and bridge decks
    np.fmax.at(top, (iy[ng], ix[ng]), z[ng])
    np.add.at(hits, (iy[ng], ix[ng]), 1)
    np.add.at(multi, (iy[ng], ix[ng]), (ret[ng] > 1).astype(float))
    height = top - fill_gaps(ground)
    transform = from_origin(x0, y0 + rows * CELL, CELL, CELL)
    corridor = features.rasterize([(freeways.buffer(args.corridor).intersection(box(x0, y0, x0 + cols * CELL, y0 + rows * CELL)), 1)], out_shape=(rows, cols), transform=transform)[::-1]
    local = box(x0, y0, x0 + cols * CELL, y0 + rows * CELL)
    near = [p for p in footprints if p.intersects(local)]
    buildings = features.rasterize([(p, 1) for p in near], out_shape=(rows, cols), transform=transform)[::-1] if near else np.zeros((rows, cols))
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
    candidate &= density <= args.density
    # Bridge decks and their parapets are not walls (deck edges pass the thin test).
    deck = np.zeros((rows, cols), bool)
    deck[iy[cls == 17], ix[cls == 17]] = True
    candidate &= ~grow(deck, 2)
    # A wall's road side is open (shoulder, slope); deck, roof and canopy edges are raised on the road side
    # too. Check 1-2 m toward the nearest freeway or ramp centreline.
    rr, cc = np.nonzero(candidate)
    roads = references.near(box(x0, y0, x0 + cols * CELL, y0 + rows * CELL).buffer(args.corridor + 50))
    if len(rr) and not roads.is_empty:
        px, py = x0 + (cc + 0.5) * CELL, y0 + (rr + 0.5) * CELL
        target = shapely.get_coordinates(shapely.shortest_line(shapely.points(px, py), roads))[1::2]
        vx, vy = target[:, 0] - px, target[:, 1] - py
        norm = np.maximum(np.hypot(vx, vy), 1e-6)
        open_side = np.zeros(len(rr), int)
        for step in (1.0, 1.5, 2.0):
            sx = np.clip(((px + vx / norm * step - x0) / CELL).astype(int), 0, cols - 1)
            sy = np.clip(((py + vy / norm * step - y0) / CELL).astype(int), 0, rows - 1)
            h = height[sy, sx]
            open_side += np.isnan(h) | (h < 1.0)
        closed = open_side < 2
        candidate[rr[closed], cc[closed]] = False
    # Bridge dashes (stretches beside trees fail the thin test) by grouping on a mask grown 2 cells.
    labels, count = label(grow(candidate, 2))
    labels = np.where(candidate, labels, 0)
    walls = []
    for n in range(1, count + 1):
        rr, cc = np.nonzero(labels == n)
        if len(rr) < args.min_length / CELL:
            continue
        xy = np.column_stack([x0 + (cc + 0.5) * CELL, y0 + (rr + 0.5) * CELL])
        # Thin (one or two cells wide) and parallel to the nearest freeway or ramp.
        pieces = [line for line, cells in polyline(xy)
                  if line.length >= 5 and cells * CELL * CELL / line.length <= 1.6 and aligned(line, references, args.max_angle)]
        if sum(line.length for line in pieces) < args.min_length:
            continue
        wall_height = f"{float(np.median(height[rr, cc])):.1f}"
        for line in pieces:
            walls.append({"line": line, "height": wall_height})
    return walls


def add_detection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--corridor", type=float, default=60.0)
    parser.add_argument("--min-length", type=float, default=25.0)
    parser.add_argument("--density", type=float, default=0.6, help="thin-line test: max raised share of the 2.5 m window (was 0.45)")
    parser.add_argument("--max-angle", type=float, default=25.0, help="max angle in degrees between a wall piece and the nearest freeway/ramp")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--points", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True, help="tile sources.geojson (EPSG:26911)")
    parser.add_argument("--buildings", type=Path, required=True, help="tile buildings.geojson (EPSG:26911)")
    parser.add_argument("--out", type=Path, required=True)
    add_detection_args(parser)
    args = parser.parse_args()
    d = np.load(args.points)
    source_features = json.loads(args.sources.read_text())["features"]
    freeways = unary_union([shape(f["geometry"]) for f in source_features if f["properties"].get("MTFCC") == "S1100"])
    references = References([shape(f["geometry"]) for f in source_features if f["properties"].get("MTFCC") in ("S1100", "S1630")])
    footprints = [shape(f["geometry"]).buffer(1.0) for f in json.loads(args.buildings.read_text())["features"] if not f["properties"].get("BARRIER")]
    if freeways.is_empty:
        args.out.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
        print("no freeway in this area")
        return 0
    corridor_all = freeways.buffer(args.corridor)
    block, overlap = 500.0, 40.0
    xmin, ymin, xmax, ymax = corridor_all.bounds
    walls = []
    for bx in np.arange(np.floor(xmin / block) * block, xmax, block):
        for by in np.arange(np.floor(ymin / block) * block, ymax, block):
            core = box(bx, by, bx + block, by + block)
            if not core.intersects(corridor_all):
                continue
            x0b, y0b, x1b, y1b = bx - overlap, by - overlap, bx + block + overlap, by + block + overlap
            sel = (d["x"] >= x0b) & (d["x"] < x1b) & (d["y"] >= y0b) & (d["y"] < y1b)
            if sel.sum() < 1000:
                continue
            for wall in detect_block(d["x"][sel], d["y"][sel], d["z"][sel], d["cls"][sel], d["ret"][sel], freeways, footprints, references, args):
                clipped = wall["line"].intersection(core)
                for part in getattr(clipped, "geoms", [clipped]):
                    if part.geom_type == "LineString" and part.length >= 5:
                        walls.append({"type": "Feature", "properties": {"height": wall["height"], "source": "lidar_detected", "length_m": round(part.length, 1)},
                                      "geometry": mapping(shp_transform(lambda a, b, c=None: TO_LONLAT.transform(a, b), part))})
    args.out.write_text(json.dumps({"type": "FeatureCollection", "features": walls}) + "\n")
    print(f"{len(walls)} wall lines, {sum(w['properties']['length_m'] for w in walls) / 1000:.2f} km")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
