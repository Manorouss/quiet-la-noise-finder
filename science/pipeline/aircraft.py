"""Aircraft CNEL at any point, estimated from the official airport noise contours.

Input: LA County airport noise contours (GeoJSON, WGS84), one polygon per airport and CLASS =
the band from CLASS to CLASS + 5 dB CNEL. Per airport, contour polygons are rebuilt (the area
inside the CLASS line = union of all bands >= CLASS). The official maps stop at 65 CNEL for most
airports (California quarterly reports), although aircraft are clearly heard beyond, so each
airport's contours are extended outward down to 55 CNEL: every 5 dB step grows the contour area
by the ratio between the airport's two outermost official contours (clamped to 1.8-2.8, default
2.3), which follows how its own contours spread.

Level at a point between contour lines c and c + 5: c + 5 * d_out / (d_out + d_in), with the
distances to the two lines. Inside the innermost contour: c + 5 * min(1, d_out / R), R being the
radius of the largest circle inside that polygon. Several airports add up as energy. Outside the
55 CNEL line no aircraft energy is added (aircraft may still be heard there).

Contours whose newest source predates MIN_YEAR are left out (the 1991 county plan, e.g. a
Palmdale contour drawn for a planned intercontinental airport); the map shows them as outlines only.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import polylabel, transform as shp_transform, unary_union

TO_UTM = Transformer.from_crs("OGC:CRS84", "EPSG:26911", always_xy=True)
FLOOR_DB = 55
RATIO_DEFAULT, RATIO_MIN, RATIO_MAX = 2.3, 1.8, 2.8
MIN_YEAR = 2000


def grow_to_area(polygon, target: float):
    """Outward buffer whose area matches target (bisection on the distance, metres)."""
    lo, hi = 0.0, 200.0
    while polygon.buffer(hi, quad_segs=8).area < target and hi < 20000:
        hi *= 2
    for _ in range(30):
        mid = (lo + hi) / 2
        if polygon.buffer(mid, quad_segs=8).area < target:
            lo = mid
        else:
            hi = mid
    return polygon.buffer(hi, quad_segs=8)


class Airport:
    def __init__(self, name: str, bands: list[tuple[int, object]], source: str):
        self.name, self.source = name, source
        levels = sorted({c for c, _ in bands})
        self.official = set(levels)
        self.contour = {c: unary_union([g for cc, g in bands if cc >= c]).buffer(0) for c in levels}
        outer = levels[0]
        ratio = RATIO_DEFAULT
        if outer + 5 in self.contour and self.contour[outer + 5].area > 0:
            ratio = min(max(self.contour[outer].area / self.contour[outer + 5].area, RATIO_MIN), RATIO_MAX)
        self.ratio = ratio
        c = outer
        while c > FLOOR_DB:
            self.contour[c - 5] = grow_to_area(self.contour[c], self.contour[c].area * ratio)
            c -= 5
        self.levels = sorted(self.contour)
        for g in self.contour.values():
            shapely.prepare(g)
        top = self.levels[-1]
        parts = list(getattr(self.contour[top], "geoms", [self.contour[top]]))
        self.core_parts = [(p, max(p.exterior.distance(polylabel(p, tolerance=2.0)), 10.0)) for p in parts]

    def levels_at(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """CNEL (dB) at points, NaN outside the 55 dB line."""
        out = np.full(len(x), np.nan)
        outer = self.contour[self.levels[0]]
        inside = shapely.contains_xy(outer, x, y)
        if not inside.any():
            return out
        idx = np.flatnonzero(inside)
        px, py = x[idx], y[idx]
        points = shapely.points(px, py)
        level = np.full(len(idx), self.levels[0])
        for c in self.levels[1:]:
            level[shapely.contains_xy(self.contour[c], px, py)] = c
        values = np.empty(len(idx))
        for c in self.levels:
            sel = level == c
            if not sel.any():
                continue
            d_out = shapely.distance(self.contour[c].boundary, points[sel])
            if c + 5 in self.contour:
                d_in = shapely.distance(self.contour[c + 5], points[sel])
                values[sel] = c + 5 * d_out / np.maximum(d_out + d_in, 1e-6)
            else:
                radius = np.full(sel.sum(), self.core_parts[0][1])
                for part, r in self.core_parts:
                    radius[shapely.contains_xy(part, px[sel], py[sel])] = r
                values[sel] = c + 5 * np.minimum(1.0, d_out / radius)
        out[idx] = values
        return out


def source_year(text: str) -> int:
    years = [int(y) for y in re.findall(r"(?<!\d)(19\d\d|20\d\d)(?!\d)", text or "")]
    return max(years) if years else 0


def load_airports(path: Path, min_year: int = MIN_YEAR) -> list[Airport]:
    bands, sources = defaultdict(list), {}
    for f in json.loads(path.read_text())["features"]:
        p = f["properties"]
        if source_year(f"{p.get('SOURCE', '')} {p.get('DATE_RECEIVED', '')}") < min_year:
            continue
        try:
            level = int(float(p["CLASS"]))
        except (KeyError, TypeError, ValueError):
            continue
        geom = shp_transform(lambda a, b, z=None: TO_UTM.transform(a, b), shape(f["geometry"])).buffer(0)
        bands[p.get("AIRPORT_NAME") or "Airport"].append((level, geom))
        sources[p.get("AIRPORT_NAME") or "Airport"] = p.get("SOURCE") or ""
    return [Airport(name, b, sources[name]) for name, b in sorted(bands.items())]


def aircraft_cnel(airports: list[Airport], x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Energy sum over airports; NaN where no airport reaches 55 dB CNEL."""
    energy = np.zeros(len(x))
    for airport in airports:
        levels = airport.levels_at(x, y)
        hit = np.isfinite(levels)
        energy[hit] += 10 ** (levels[hit] / 10)
    with np.errstate(divide="ignore"):
        return np.where(energy > 0, 10 * np.log10(np.where(energy > 0, energy, 1)), np.nan)


def road_cnel(day: float, evening: float, night: float) -> float:
    """CNEL from day (7-19), evening (19-22, +5 dB) and night (22-7, +10 dB) LAeq."""
    return 10 * math.log10((12 * 10 ** (day / 10) + 3 * 10 ** ((evening + 5) / 10) + 9 * 10 ** ((night + 10) / 10)) / 24)


def energy_sum(a: float, b: float) -> float:
    return 10 * math.log10(10 ** (a / 10) + 10 ** (b / 10))
