#!/usr/bin/env python3
"""Capture every LA County DPW building footprint once, resumably.

Keyset pagination (OBJECTID > last, ordered, 1,000 per request) over
CODE = 'Building', geometry in EPSG:26911, written as gzip GeoJSON pages.
Re-running skips pages already on disk. A final count check compares the
captured total with the service's own count, and a manifest records it.

Usage:
  capture_county_buildings.py --out implementation/work/county_buildings_2026-10-04
"""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import time
import urllib.parse
from pathlib import Path

URL = "https://dpw.gis.lacounty.gov/dpw/rest/services/buildingfootprints/MapServer/0/query"
WHERE = "CODE='Building'"


def get(params: dict) -> dict:
    for attempt in range(6):
        r = subprocess.run(["curl", "-sS", "--fail", "--max-time", "180", URL + "?" + urllib.parse.urlencode(params)], capture_output=True)
        try:
            return json.loads(r.stdout)
        except Exception:
            if attempt == 5:
                raise RuntimeError(f"query failed: {r.stderr[-300:]!r}")
            time.sleep(10 * (attempt + 1))
    raise AssertionError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    pages = args.out / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    expected = get({"where": WHERE, "returnCountOnly": "true", "f": "json"})["count"]
    done = sorted(pages.glob("page_*.geojson.gz"))
    last, total = 0, 0
    for p in done:
        doc = json.loads(gzip.decompress(p.read_bytes()))
        total += len(doc["features"])
        last = max(last, max(f["properties"]["OBJECTID"] for f in doc["features"]))
    ordinal = len(done)
    started = time.time()
    while True:
        doc = get({"where": f"{WHERE} AND OBJECTID > {last}", "outFields": "OBJECTID,BLD_ID,HEIGHT,ELEV,DATE_",
                   "returnGeometry": "true", "outSR": "26911", "orderByFields": "OBJECTID ASC",
                   "resultRecordCount": "1000", "f": "geojson"})
        features = doc.get("features", [])
        if not features:
            break
        ordinal += 1
        (pages / f"page_{ordinal:05d}.geojson.gz").write_bytes(gzip.compress(json.dumps(doc, separators=(",", ":")).encode(), 6))
        total += len(features)
        last = max(f["properties"]["OBJECTID"] for f in features)
        if ordinal % 100 == 0:
            rate = (total) / max(1.0, time.time() - started)
            print(f"{ordinal} pages, {total}/{expected} features, last OBJECTID {last}, {rate:.0f} features/s", flush=True)
    manifest = {"service": URL, "where": WHERE, "out_sr": 26911, "pages": ordinal, "features": total,
                "service_count": expected, "complete": total == expected, "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps(manifest))
    return 0 if manifest["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
