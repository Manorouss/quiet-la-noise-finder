#!/usr/bin/env python3
"""Split the OpenStreetMap highway ways of LA County into 1 km UTM cell files for the tile builder.

Input: an osmium GeoJSONSeq export of highway ways (science/pipeline/compute_up.sh does not make it;
see implementation/work/source_cache/osm/la_highways.geojsonseq and the audit of 2026-10-05):
  osmium extract -b -118.97,33.25,-117.60,34.87 socal-261003.osm.pbf -o la_county.osm.pbf
  osmium tags-filter la_county.osm.pbf w/highway=motorway,...,residential,living_street,service,road -o la_highways.osm.pbf
  osmium export la_highways.osm.pbf -f geojsonseq -o la_highways.geojsonseq --geometry-types=linestring --attributes=type,id
Output: <out>/e<X>-n<Y>.json per cell = [{"id", "highway", "lanes", "oneway", "maxspeed", "service", "name", "coords": [[x, y], ...]}]
in EPSG:26911, each way in every cell its bounding box touches, plus <out>/meta.json. build_county_tile.py
--osm-dir reads the cells covering a tile's halo and classifies TIGER local streets by the OSM highway tag.

  osm_streets_cache.py --input .../la_highways.geojsonseq --out .../source_cache/osm_streets
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from pyproj import Transformer

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:26911", always_xy=True)
KEEP = ("highway", "lanes", "oneway", "maxspeed", "service", "name")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cells: dict[tuple[int, int], list] = {}
    ways, started = 0, time.monotonic()
    digest = hashlib.sha256()
    with args.input.open("rb") as stream:
        for raw in stream:
            digest.update(raw)
            line = raw.strip().lstrip(b"\x1e")
            if not line:
                continue
            f = json.loads(line)
            if f.get("geometry", {}).get("type") != "LineString":
                continue
            p = f.get("properties", {})
            lons, lats = zip(*[(c[0], c[1]) for c in f["geometry"]["coordinates"]])
            xs, ys = TO_UTM.transform(lons, lats)
            way = {"id": p.get("@id"), **{k: p[k] for k in KEEP if k in p}, "coords": [[round(x, 2), round(y, 2)] for x, y in zip(xs, ys)]}
            ways += 1
            for cx in range(int(min(xs)) // 1000, int(max(xs)) // 1000 + 1):
                for cy in range(int(min(ys)) // 1000, int(max(ys)) // 1000 + 1):
                    cells.setdefault((cx, cy), []).append(way)
    for (cx, cy), items in cells.items():
        (args.out / f"e{cx}-n{cy}.json").write_text(json.dumps(items, separators=(",", ":")))
    meta = {"source": str(args.input), "source_sha256": digest.hexdigest(), "ways": ways, "cells": len(cells),
            "built_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seconds": round(time.monotonic() - started, 1)}
    (args.out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
