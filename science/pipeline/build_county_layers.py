#!/usr/bin/env python3
"""Turn released county tiles into PMTiles map layers for the county map.

Inputs are portal tile directories written by build_tile_assets.py (benchmark.geojson,
buildings.geojson, build-manifest.json), already QA-flagged (physical-ceiling masks,
on-road labels). Later roots win when a tile appears twice. Outputs, in --out:

  receivers.pmtiles  points: k integer id (selection only), d/e/n LAeq per period (absent when masked
                     or unavailable; road traffic plus trains where the rail pass ran, science/rail/
                     rail_queue.py), t train CNEL (trains alone, where 40 dB or more), q 24 h CNEL of
                     roads, trains and aircraft, a aircraft CNEL (only
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
                     (dB, 0.1 dB steps); elevation 150 means not modeled (one pixel of padding past the edge).
                     Web tiles sample a 5 m grid per 1 km tile bilinearly. How the grid is made (FIELD_METHOD):
                       1. All unmasked receivers (open ground and building facades) of the tile and its eight
                          neighbours are joined into a mesh of triangles (a Delaunay triangulation) and the level
                          is interpolated linearly across each triangle, so the surface passes through every
                          modeled value instead of averaging them. Triangles with a side over 40 m are dropped:
                          a long triangle would invent values where nothing was modeled.
                       2. Building footprints are cut out. The remaining ground is smoothed lightly (a Gaussian of
                          4 m) using only ground cells, so the street side of a house never bleeds into its yard and
                          a wall never pulls its own value into the street.
                       3. Ground the mesh does not cover takes the old wide fill (30 m kernel) when there are
                          receivers nearby, and stays not modeled otherwise.
                       4. Cells inside a footprint are not modeled values, but the map blends neighbouring pixels
                          linearly, so a 'not modeled' value there would ring the house with a loud halo (measured:
                          errors above 100 dB at the wall). Up to 15 m inside the footprint they take the surface at
                          the closest point of ground (the value just outside the nearest wall: street or yard side,
                          never an average across the house). The 2D map covers houses with an opaque footprint, the
                          3D map with the extruded building, so this only keeps the edge clean. Deeper inside big
                          buildings the old wide fill is used where it exists, so a large complex has no holes.
                     Neighbouring tiles use the same receivers and footprints around the edge, so tiles join seamlessly.
  glow_{d,e,n,q,r}.pmtiles the old surface with a 24 m kernel (unchanged), for the soft Glow style.
  coverage.geojson   modeled 1 km tiles, plus coverage_outline.geojson (dissolved edge).
  basemap/           Protomaps LA County basemap (hard link to the downloaded extract).
  context/           fire stations, heliports and airport noise contours (GeoJSON).
  layers.json        tile list, counts, encodings, file hashes and build provenance.

Usage:
  build_county_layers.py --tiles-root <dir> [--tiles-root <dir> ...] --out <dir> [--cache-dir <dir> | --no-cache] [--jobs N]

The field grid of a tile is cached (npz per tile in --cache-dir, default <work>/county_layers/field_cache) under a key made of
FIELD_METHOD and the exact receivers and footprints around the tile, so a publish recomputes only tiles whose surroundings
changed. Changing FIELD_METHOD recomputes every tile once. The surface needs scipy: run with geo-python-sci (geo-python has none).
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import io
import json
import math
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import sys
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
RAIL_RESULTS = PROJECT / "implementation/work/rail/results"
RAIL_TILES: list[str] = []   # tiles whose receivers include trains (written to layers.json)


def rail_levels(tile_id: str, path: Path) -> dict:
    """Train D/E/N LAeq per source receiver key from the rail pass, if it ran on this tile's released road run."""
    result = RAIL_RESULTS / f"{tile_id}.json"
    if not result.exists() or not (RAIL_RESULTS.parent / "RELEASE").exists():  # (rail_queue.py: first pass done)
        return {}
    data = json.loads(result.read_text())
    build = json.loads((path / "build-manifest.json").read_text())
    if not data.get("levels"):
        return {}
    # A newer road run of the same cell with the same receivers (same count: the layout is deterministic) keeps the rail result.
    if data.get("road_attempt") != build.get("attempt_id") and len(data["levels"]) != int(build.get("receiver_count", -1)):
        return {}
    RAIL_TILES.append(tile_id)
    return data["levels"]


def esum(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return a
    return 10 * math.log10(10 ** (a / 10) + 10 ** (b / 10))
TO_UTM = Transformer.from_crs("OGC:CRS84", "EPSG:26911", always_xy=True)
TO_LONLAT = Transformer.from_crs("EPSG:26911", "OGC:CRS84", always_xy=True)
PERIODS = ("D", "E", "N")
BANDS = "denqr"         # field rasters: the three periods, the 24 h CNEL (roads + aircraft) and roads-only CNEL
AIRPORT_CONTOURS = CONTEXT / "la_county_airport_noise_contours.geojson"
BUILDING_PERCENTILES: dict[str, list[float]] = {}  # filled by building_features, written to layers.json
CELL = 5.0              # field grid (m)
MARGIN = 100.0          # neighbour receivers used around each tile (m)
SIGMA, SIGMA_FILL, SIGMA_GLOW = 9.0, 30.0, 24.0   # the old surface: 9 m blur, 30 m wide fill (also the fallback) and the 24 m glow
# The field surface (see the module notes). Bump FIELD_METHOD whenever the surface changes in any way: every tile's cached grid
# is then rebuilt once on the next run.
FIELD_METHOD = "tin-4m-v1"
SURFACE_SIGMA = 4.0     # m, Gaussian over ground cells only (the mesh is sampled and smoothed on a 1 m grid; every 5 m cell centre is a 1 m cell centre)
EDGE_MAX = 40.0         # m, triangles with a longer side are dropped
FILL_CELLS = 3          # building cells up to this many 5 m cells (15 m) from ground take the value of the nearest ground cell
FOOTPRINT_MARGIN = 150.0  # m around the tile core in which footprints are considered
FIELD_CACHE = PROJECT / "implementation/work/county_layers/field_cache"
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
        rail = rail_levels(tile_id, path)
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
            train = rail.get(p.get("source_receiver_key")) if rail else None
            if train and all(v is not None for v in train) and all(v is not None for v in values):
                values = [esum(v, t) for v, t in zip(values, train)]
                train_cnel = road_cnel(*train)
                if train_cnel >= 40:
                    out["t"] = round(train_cnel, 1)
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
                    if train:  # the facade statistics of the tile assets are road only: recompute with trains
                        for name, value in zip("den", values):
                            best[name] = round(max(best.get(name, -1.0), value), 1)
                            best[name + "l"] = round(min(best.get(name + "l", 999.0), value), 1)
                        if "t" in out:
                            best["t"] = max(best.get("t", -1.0), out["t"])
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


def legacy_surface(origin: tuple[int, int], pts: np.ndarray, pad_cells: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """The old surface, kept as the wide fill and for the glow: (field, glow) values (bands, H, W) on the 5 m grid, NaN where unknown.

    Normalized Gaussian convolution of the receiver values: 9 m where receivers are dense, else 30 m. The field covers the
    tile core plus pad_cells 5 m cells on every side; the glow (24 m kernel, unchanged) covers the core only.
    """
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
    first, last = int(MARGIN / CELL), int((MARGIN + 1000) / CELL)
    core = slice(first, last)
    ext = slice(first - pad_cells, last + pad_cells)
    return result[:, ext, ext], glow[:, core, core]


def _scipy():
    try:
        from scipy import ndimage
        from scipy.spatial import Delaunay
    except ImportError as exc:  # geo-python (Python 3.12) has no scipy
        raise SystemExit("the field surface needs scipy: run this with science/pipeline/geo-python-sci") from exc
    return ndimage, Delaunay


@functools.lru_cache(maxsize=40)
def tile_polygons(path: str) -> tuple[tuple, np.ndarray]:
    """Building footprints of one released tile in UTM metres: (keys, polygons). Cached: a tile is read by the nine tiles around it."""
    import shapely
    features = json.loads((Path(path) / "buildings.geojson").read_text())["features"]
    keys, rings, owner, extra = [], [], [], []
    for feature in features:
        geometry = feature["geometry"]
        key = feature["properties"].get("building_key")
        if geometry["type"] == "Polygon" and len(geometry["coordinates"]) == 1:
            rings.append(np.asarray(geometry["coordinates"][0], dtype=float)[:, :2])
            owner.append(len(keys))
            keys.append(key)
        else:  # holes or several parts: rare, built one by one
            parts = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
            extra.append((len(keys), key, parts))
            keys.append(key)
    polygons = np.empty(len(keys), dtype=object)
    if rings:
        sizes = np.array([len(r) for r in rings])
        flat = np.concatenate(rings)
        x, y = TO_UTM.transform(flat[:, 0], flat[:, 1])
        coords = np.column_stack([x, y])
        index = np.repeat(np.arange(len(rings)), sizes)
        polygons[np.array(owner)] = shapely.polygons(shapely.linearrings(coords, indices=index))
    for slot, _key, parts in extra:
        shapes = []
        for part in parts:
            projected = [np.column_stack(TO_UTM.transform(*np.asarray(ring, dtype=float)[:, :2].T)) for ring in part]
            shapes.append(shapely.Polygon(projected[0], projected[1:]))
        polygons[slot] = shapes[0] if len(shapes) == 1 else shapely.MultiPolygon(shapes)
    bad = ~shapely.is_valid(polygons)
    if bad.any():
        polygons[bad] = shapely.make_valid(polygons[bad])
    return tuple(keys), polygons


def tile_footprints(origin: tuple[int, int], paths: list[Path]):
    """Union of the building footprints around a tile core (its own and its neighbours' files, one per building key), prepared; None if none."""
    import shapely
    x0, y0 = origin
    area = shapely.box(x0 - FOOTPRINT_MARGIN, y0 - FOOTPRINT_MARGIN, x0 + 1000 + FOOTPRINT_MARGIN, y0 + 1000 + FOOTPRINT_MARGIN)
    unique: dict = {}
    for path in paths:
        keys, polygons = tile_polygons(str(path))
        for key, polygon in zip(keys, polygons):
            unique.setdefault(key if key is not None else id(polygon), polygon)
    if not unique:
        return None
    polygons = np.array(list(unique.values()), dtype=object)
    near = polygons[shapely.intersects(polygons, area)]
    if not len(near):
        return None
    union = shapely.union_all(near)
    shapely.prepare(union)
    return union


def tile_field(origin: tuple[int, int], pts: np.ndarray, footprints=None, stats: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(field, glow): values (bands, 200, 200) on the 5 m grid of the 1 km tile core, NaN where unknown.

    The field is the house-aware surface described in the module notes (triangulated receivers, footprints cut out, light
    smoothing over ground only, wide-fill fallback, cells inside footprints continued from the nearest ground). `footprints`
    is the prepared union of the building polygons in UTM metres (tile_footprints), or None for open country. The glow is the
    old 24 m surface, unchanged. `stats` collects counts and seconds.
    """
    import shapely
    ndimage, Delaunay = _scipy()
    clock = time.perf_counter
    st = stats if stats is not None else {}
    nb = len(BANDS)
    x0, y0 = origin
    t = clock()
    wide, glow = legacy_surface(origin, pts, FILL_CELLS)         # (nb, 206, 206) and the core glow
    st["t_legacy"] = clock() - t

    # 1. mesh of all unmasked receivers around the tile, long triangles dropped
    t = clock()
    sel = (pts[:, 0] >= x0 - MARGIN) & (pts[:, 0] <= x0 + 1000 + MARGIN) & (pts[:, 1] >= y0 - MARGIN) & (pts[:, 1] <= y0 + 1000 + MARGIN)
    p = pts[sel]
    tri = None
    if len(p) >= 4:
        _, first = np.unique(np.round(p[:, :2], 2), axis=0, return_index=True)   # identical coordinates carry identical values
        p = p[np.sort(first)]
        try:
            tri = Delaunay(p[:, :2])
        except RuntimeError:  # all points on a line, or too few
            tri = None
    st.update(receivers=len(p), triangles=0, triangles_dropped=0)
    if tri is not None:
        corners = p[:, :2][tri.simplices]
        side = np.max([np.hypot(*(corners[:, a] - corners[:, b]).T) for a, b in ((0, 1), (1, 2), (2, 0))], axis=0)
        too_long = side > EDGE_MAX
        st.update(triangles=len(side), triangles_dropped=int(too_long.sum()))
    st["t_mesh"] = clock() - t

    # Fine grid (1 m cells): core + room for the smoothing kernel and for the building fill, so tile edges agree.
    pad = int(math.ceil(3.0 * SURFACE_SIGMA)) + 1 + int(CELL) * FILL_CELLS
    n = 1000 + 2 * pad
    t = clock()
    fx = x0 + (np.arange(n) - pad + 0.5)
    fy = y0 + (np.arange(n) - pad + 0.5)
    X, Y = np.meshgrid(fx, fy)
    X, Y = X.ravel(), Y.ravel()
    house = shapely.contains_xy(footprints, X, Y) if footprints is not None else np.zeros(X.shape, dtype=bool)
    st["t_footprints_mask"] = clock() - t

    # 2. interpolate across the triangles, cut out houses, smooth over ground cells only
    t = clock()
    valid = np.zeros(X.shape, dtype=bool)
    vals = np.full((nb, n * n), np.nan)
    if tri is not None:
        pf = np.column_stack([X, Y])
        simplex = tri.find_simplex(pf)
        ok = simplex >= 0
        ok[ok] = ~too_long[simplex[ok]]
        valid = ok & ~house
        idx = np.flatnonzero(valid)
        s = simplex[idx]
        transform = tri.transform[s]
        b = np.einsum("mij,mj->mi", transform[:, :2, :], pf[idx] - transform[:, 2, :])
        weights = np.column_stack([b, 1.0 - b.sum(axis=1)])
        vertices = tri.simplices[s]
        for k in range(nb):
            v = p[:, 2 + k]
            vals[k, idx] = weights[:, 0] * v[vertices[:, 0]] + weights[:, 1] * v[vertices[:, 1]] + weights[:, 2] * v[vertices[:, 2]]
    st["t_interpolate"] = clock() - t

    t = clock()
    valid2 = valid.reshape(n, n)
    centre = pad + 2 + 5 * np.arange(-FILL_CELLS, 200 + FILL_CELLS)   # fine-cell index of each 5 m cell centre (core +- fill cells)
    m = len(centre)
    sl = slice(FILL_CELLS, FILL_CELLS + 200)                          # the core inside the extended grid
    out = np.full((nb, m, m), np.nan)
    inside_house = house.reshape(n, n)[np.ix_(centre, centre)]
    filled_fine = np.zeros((m, m), dtype=bool)
    if tri is not None and valid.any():
        den = ndimage.gaussian_filter(valid2.astype(float), SURFACE_SIGMA, mode="constant", truncate=3.0)
        good = valid2 & (den > 1e-6)
        if inside_house.any():
            # A 5 m cell centre inside a house takes the surface at the closest point of ground (1 m grid), within 15 m: the value
            # just outside the nearest wall, never an average across the house.
            distance, nearest = ndimage.distance_transform_edt(~good, return_indices=True)
            filled_fine = inside_house & (distance[np.ix_(centre, centre)] <= FILL_CELLS * CELL + 1e-9)
            near_row = nearest[0][np.ix_(centre, centre)][filled_fine]
            near_col = nearest[1][np.ix_(centre, centre)][filled_fine]
        for k in range(nb):
            num = ndimage.gaussian_filter(np.where(valid2, vals[k].reshape(n, n), 0.0), SURFACE_SIGMA, mode="constant", truncate=3.0)
            res = np.where(good, num / np.where(good, den, 1.0), np.nan)
            out[k] = res[np.ix_(centre, centre)]
            if filled_fine.any():
                out[k][filled_fine] = res[near_row, near_col]
    st["t_smooth"] = clock() - t

    # 3. ground the mesh does not reach: the old wide fill where the old surface had receivers nearby
    t = clock()
    out = np.round(out, 1)   # (rounded again at the end: the wide fill comes unrounded)
    gap = ~np.isfinite(out[0]) & ~inside_house
    use_wide = gap & np.isfinite(wide[0])
    for k in range(nb):
        out[k][use_wide] = wide[k][use_wide]
    st.update(gap_cells=int(gap[sl, sl].sum()), wide_fill_cells=int(use_wide[sl, sl].sum()))

    # 4. house cells the 1 m fill could not reach (next to ground that came from the wide fill, or beyond the mesh) continue the nearest
    # ground cell of the 5 m grid, up to FILL_CELLS away
    ground = np.isfinite(out[0]) & ~inside_house
    rest = inside_house & ~np.isfinite(out[0])
    filled_coarse = np.zeros_like(rest)
    if rest.any() and ground.any():
        distance, nearest = ndimage.distance_transform_edt(~ground, return_indices=True)
        filled_coarse = rest & (distance <= FILL_CELLS + 1e-9)
        out[:, filled_coarse] = out[:, nearest[0][filled_coarse], nearest[1][filled_coarse]]
    # deep inside big buildings (more than 15 m from ground) the old wide fill, where it exists: hidden under the footprint, but no holes
    # in the thin gaps and courtyards of a large complex, and no hard edge for the map's linear blending
    deep = inside_house & ~np.isfinite(out[0]) & np.isfinite(wide[0])
    for k in range(nb):
        out[k][deep] = wide[k][deep]
    st.update(building_cells=int(inside_house[sl, sl].sum()), building_cells_filled=int((filled_fine | filled_coarse)[sl, sl].sum()),
              building_cells_filled_coarse=int(filled_coarse[sl, sl].sum()), building_cells_wide_fill=int(deep[sl, sl].sum()))
    st["t_fill"] = clock() - t
    return np.round(out[:, sl, sl], 1), glow


def sources_stamp() -> str:
    """Fingerprint of everything outside the receivers that shapes the field values: the aircraft model and its contours."""
    h = hashlib.sha256()
    for path in (HERE / "aircraft.py", AIRPORT_CONTOURS):
        h.update(path.read_bytes() if path.exists() else b"-")
    return h.hexdigest()[:16]


def tile_signature(tile_id: str, path: Path) -> str:
    """Identity of a released tile's inputs to the field: its run, its two data files and its train result (if the rail pass is released)."""
    parts = [tile_id, json.loads((path / "build-manifest.json").read_text()).get("attempt_id", "")]
    for name in ("benchmark.geojson", "buildings.geojson"):
        stat = (path / name).stat()
        parts.append(f"{stat.st_size}:{stat.st_mtime_ns}")
    result = RAIL_RESULTS / f"{tile_id}.json"
    if result.exists() and (RAIL_RESULTS.parent / "RELEASE").exists():
        stat = result.stat()
        parts.append(f"rail:{stat.st_size}:{stat.st_mtime_ns}")
    return "|".join(parts)


def field_key(cell: tuple[int, int], by_cell: dict, signatures: dict, stamp: str) -> str:
    """Cache key of a cell's field: the method plus the exact inputs of the nine tiles around it."""
    h = hashlib.sha256(f"{FIELD_METHOD}|{stamp}".encode())
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            tile = by_cell.get((cell[0] + dx, cell[1] + dy))
            h.update(f"|{dx},{dy}:{signatures[tile] if tile else '-'}".encode())
    return h.hexdigest()[:24]


def read_cached(cache_dir: Path | None, tile_id: str, key: str):
    if cache_dir is None:
        return None
    path = cache_dir / f"{tile_id}.npz"
    try:
        with np.load(path) as data:
            if str(data["key"]) != key:
                return None
            raw = data["field"]
    except (OSError, ValueError, KeyError):
        return None
    return np.where(raw == -32768, np.nan, raw / 10.0)


def write_cached(cache_dir: Path | None, tile_id: str, key: str, field: np.ndarray) -> None:
    if cache_dir is None:
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw = np.where(np.isfinite(field), np.round(field * 10.0), -32768).astype(np.int16)   # the field is already in 0.1 dB steps
    tmp = cache_dir / f".{tile_id}.{os.getpid()}.tmp.npz"
    np.savez_compressed(tmp, key=key, field=raw, method=FIELD_METHOD)
    os.replace(tmp, cache_dir / f"{tile_id}.npz")


_JOB: dict = {}   # set before forking: everything compute_cell needs


def compute_cell(cell: tuple[int, int]):
    """(cell, field, glow, stats, cache hit) for one 1 km cell."""
    job = _JOB
    tile_id = job["by_cell"][cell]
    stats: dict = {}
    nearby = [job["by_cell"][(cell[0] + dx, cell[1] + dy)] for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (cell[0] + dx, cell[1] + dy) in job["by_cell"]]
    pts = np.vstack([job["points"][t] for t in nearby])
    key = field_key(cell, job["by_cell"], job["signatures"], job["stamp"])
    cached = read_cached(job["cache_dir"], tile_id, key)
    if cached is not None:
        _, glow = legacy_surface(job["origins"][tile_id], pts)
        return cell, cached, glow, stats, True
    t = time.perf_counter()
    footprints = tile_footprints(job["origins"][tile_id], [job["tiles"][t] for t in nearby])
    stats["t_footprints_prep"] = time.perf_counter() - t
    field, glow = tile_field(job["origins"][tile_id], pts, footprints, stats)
    write_cached(job["cache_dir"], tile_id, key, field)
    return cell, field, glow, stats, False


def compute_fields(by_cell: dict, tiles: dict, origins: dict, points: dict, cache_dir: Path | None, jobs: int):
    """Field and glow grids for every cell: from the cache when the cell's surroundings are unchanged, else computed (in `jobs` processes)."""
    _JOB.update(by_cell=by_cell, tiles=tiles, origins=origins, points=points, cache_dir=cache_dir, stamp=sources_stamp(),
                signatures={t: tile_signature(t, tiles[t]) for t in by_cell.values()})
    order = sorted(by_cell, key=lambda c: (c[1], c[0]))   # neighbours of consecutive cells overlap: footprint files stay in memory
    results = {}
    if jobs > 1 and sys.platform != "win32":
        with multiprocessing.get_context("fork").Pool(jobs) as pool:
            for item in pool.imap(compute_cell, order, chunksize=max(1, len(order) // (jobs * 8) or 1)):
                results[item[0]] = item[1:]
    else:
        for cell in order:
            item = compute_cell(cell)
            results[item[0]] = item[1:]
    fields = {cell: results[cell][0] for cell in by_cell}
    glows = {cell: results[cell][1] for cell in by_cell}
    computed = [r[2] for r in results.values() if not r[3]]
    summary = {"cells": len(by_cell), "cache_hits": sum(1 for r in results.values() if r[3]), "computed": len(computed)}
    for key in sorted({k for s in computed for k in s}):
        summary[key] = round(sum(s.get(key, 0) for s in computed), 2 if key.startswith("t_") else 0)
    return fields, glows, summary


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
    parser.add_argument("--cache-dir", type=Path, default=FIELD_CACHE, help="per-tile field cache (default: %(default)s)")
    parser.add_argument("--no-cache", action="store_true", help="compute every tile's field, read and write no cache")
    parser.add_argument("--jobs", type=int, default=int(os.environ.get("QUIETLA_FIELD_JOBS", "1")), help="processes for the field surface")
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
        field_started = time.time()
        fields, glows, field_summary = compute_fields(by_cell, tiles, origins, points, None if args.no_cache else args.cache_dir.resolve(), max(1, args.jobs))
        field_summary["seconds"] = round(time.time() - field_started, 1)
        print(json.dumps({"field_surface": field_summary}), flush=True)
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
                           "method": FIELD_METHOD, "cell_m": CELL, "sigma_m": SURFACE_SIGMA, "edge_max_m": EDGE_MAX, "building_fill_m": FILL_CELLS * CELL, "fill_sigma_m": SIGMA_FILL,
                           "glow_sigma_m": SIGMA_GLOW, "zooms": [FIELD_MIN_ZOOM, FIELD_MAX_ZOOM]},
        "field_surface": {"method": FIELD_METHOD, **field_summary,
                          "what": "triangulated receivers (open ground and facades), triangles with a side over 40 m dropped, building footprints cut out, Gaussian 4 m over ground cells only, "
                                  "30 m wide fill where the mesh does not reach, building cells continued 15 m from the nearest ground (wide fill deeper in); glow unchanged"},
        "files": {name: {"sha256": sha(out / name), "bytes": (out / name).stat().st_size}
                  for name in ("receivers.pmtiles", "buildings.pmtiles", "roads.pmtiles", *(f"{k}_{b}.pmtiles" for k in ("field", "glow") for b in BANDS), "coverage.geojson", *extra)},
        "building_percentiles": {"what": "percentiles 0..100 of the loudest facade level per building, per period (d/e/n LAeq, q 24 h CNEL, r roads-only CNEL)", **BUILDING_PERCENTILES},
        "rail": {"method": "CNOSSOS-EU railway emission and propagation (science/rail), US train types calibrated to the FTA reference levels; added to the road levels per period", "tiles": sorted(RAIL_TILES)},
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
