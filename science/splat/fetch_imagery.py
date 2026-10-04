#!/usr/bin/env python3
"""Mosaic public-domain USGS orthoimagery (The National Map, USGSImageryOnly) for a UTM box.

Fetches Web Mercator tiles at --zoom (19 ≈ 0.25 m/pixel at LA) with curl and writes a PNG plus
a JSON sidecar with the mosaic's EPSG:3857 bounds, for colouring lidar points.

Usage:
  fetch_imagery.py --box 373000 3779000 374000 3780000 --out ortho.png [--zoom 19]
"""
from __future__ import annotations

import argparse
import io
import json
import math
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image
from pyproj import Transformer

URL = "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}"
TO_LONLAT = Transformer.from_crs("EPSG:26911", "OGC:CRS84", always_xy=True)
R = 6378137.0


def tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    return int((lon + 180) / 360 * n), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--box", type=float, nargs=4, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--zoom", type=int, default=19)
    args = parser.parse_args()
    corners = [TO_LONLAT.transform(x, y) for x in (args.box[0], args.box[2]) for y in (args.box[1], args.box[3])]
    txs, tys = zip(*(tile_xy(lon, lat, args.zoom) for lon, lat in corners))
    tx0, tx1, ty0, ty1 = min(txs), max(txs), min(tys), max(tys)
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))

    def fetch(xy):
        x, y = xy
        data = subprocess.run(["curl", "-s", "-f", "--retry", "3", URL.format(z=args.zoom, x=x, y=y)], capture_output=True).stdout
        return xy, data

    with ThreadPoolExecutor(8) as pool:
        for (x, y), data in pool.map(fetch, [(x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]):
            if data:
                mosaic.paste(Image.open(io.BytesIO(data)).convert("RGB"), ((x - tx0) * 256, (y - ty0) * 256))
    size = 2 * math.pi * R / 2 ** args.zoom
    bounds = [-math.pi * R + tx0 * size, math.pi * R - (ty1 + 1) * size, -math.pi * R + (tx1 + 1) * size, math.pi * R - ty0 * size]
    mosaic.save(args.out)
    args.out.with_suffix(".json").write_text(json.dumps({"bounds_3857": bounds, "zoom": args.zoom, "source": "USGS The National Map, USGSImageryOnly (public domain)"}) + "\n")
    print(f"{mosaic.size[0]}x{mosaic.size[1]} px, {(tx1 - tx0 + 1) * (ty1 - ty0 + 1)} tiles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
