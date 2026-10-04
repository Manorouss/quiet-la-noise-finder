#!/usr/bin/env python3
"""Keep the county queue stocked with built tiles, nearest-first from a start point.

Every 1 km UTM cell with at least --min-buildings DPW buildings is a target.
Cells already packaged (any county_v1 attempt with the same x0/y0 and layout)
are skipped. The daemon builds the next cells with build_county_tile.py until
the queue holds --stock tiles, then waits. Create <queue>/STOP to end it.

Usage:
  county_daemon.py --queue implementation/work/pipeline_queue/county_main
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[4]
ATTEMPTS = PROJECT / "implementation/work/campaign/county_v1/attempts"
DENSITY = PROJECT / "implementation/work/county_building_density_1km.json"
LAYOUT_ARGS = ["--grid", "20", "--facade-spacing", "10", "--dense-near-roads", "50"]
LAYOUT = "g20a50f10"


def packaged() -> set[tuple[int, int]]:
    done = set()
    for manifest in ATTEMPTS.glob(f"phase1-county-*-{LAYOUT}-v1/attempt_manifest.json"):
        tile = json.loads(manifest.read_text()).get("tile", {})
        done.add((int(tile["x0"]) // 1000, int(tile["y0"]) // 1000))
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--min-buildings", type=int, default=25)
    parser.add_argument("--stock", type=int, default=12)
    parser.add_argument("--start", type=float, nargs=2, default=(356500.0, 3782500.0), help="UTM x y to grow outward from")
    args = parser.parse_args()
    queue = args.queue.resolve()
    (queue / "todo").mkdir(parents=True, exist_ok=True)
    log = (queue / "daemon.log").open("a")
    density = {tuple(map(int, k.split(","))): v for k, v in json.loads(DENSITY.read_text()).items()}
    cells = sorted((c for c, n in density.items() if n >= args.min_buildings),
                   key=lambda c: math.hypot(c[0] * 1000 + 500 - args.start[0], c[1] * 1000 + 500 - args.start[1]))
    failed: set[tuple[int, int]] = set()
    print(f"{time.strftime('%FT%TZ', time.gmtime())} daemon start: {len(cells)} target cells", file=log, flush=True)
    while not (queue / "STOP").exists():
        if len(list((queue / "todo").iterdir())) >= args.stock:
            time.sleep(30)
            continue
        done = packaged()
        nxt = next((c for c in cells if c not in done and c not in failed), None)
        if nxt is None:
            print(f"{time.strftime('%FT%TZ', time.gmtime())} all target cells packaged", file=log, flush=True)
            break
        name = f"la-e{nxt[0]}-n{nxt[1]}"
        result = subprocess.run([str(HERE / "geo-python"), str(HERE / "build_county_tile.py"), "--name", name,
                                 "--x0", str(nxt[0] * 1000), "--y0", str(nxt[1] * 1000), *LAYOUT_ARGS],
                                capture_output=True, text=True)
        attempt = ATTEMPTS / f"phase1-county-{name}-{LAYOUT}-v1"
        if result.returncode == 0 and (attempt / "attempt_manifest.json").exists():
            (queue / "todo" / name).write_text(str(attempt) + "\n")
            print(f"{time.strftime('%FT%TZ', time.gmtime())} built {name} ({len(done) + 1}/{len(cells)} packaged)", file=log, flush=True)
        else:
            failed.add(nxt)
            print(f"{time.strftime('%FT%TZ', time.gmtime())} FAILED {name}: {result.stderr[-300:]!r}", file=log, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
