#!/usr/bin/env python3
"""Keep a county queue stocked with built tiles, nearest-first from a start point.

Every 1 km UTM cell with at least --min-buildings DPW buildings is a target. Cells already
packaged for the model (county_models.py; default: the current one) are skipped. The daemon
builds the next cells with build_county_tile.py until the queue holds --stock tiles, then
waits. Models with --corridor-dir inputs (v2: lidar walls and bridge decks) build a cell only
when every corridor block touching its 1.5 km halo is finished; the daemon takes the nearest
ready cell among the next 40, so coverage grows compactly while corridor_products.py catches
up. Create <queue>/STOP to end it.

Usage:
  county_daemon.py [--model county-v2] [--queue implementation/work/pipeline_queue/county_v2]
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

from county_models import ATTEMPTS, CURRENT, WORK, by_name, package_glob

HERE = Path(__file__).resolve().parent
DENSITY = WORK / "county_building_density_1km.json"
HALO = 1500
LOOKAHEAD = 40


def stamp() -> str:
    return time.strftime("%FT%TZ", time.gmtime())


def packaged(model: dict) -> set[tuple[int, int]]:
    done = set()
    for manifest in ATTEMPTS.glob(f"{package_glob(model)}/attempt_manifest.json"):
        tile = json.loads(manifest.read_text()).get("tile", {})
        done.add((int(tile["x0"]) // 1000, int(tile["y0"]) // 1000))
    return done


def corridor_dir(model: dict) -> Path | None:
    args = model["build_args"]
    return Path(args[args.index("--corridor-dir") + 1]) if "--corridor-dir" in args else None


def corridor_ready(cell: tuple[int, int], corridor: Path, plan: dict) -> bool:
    """Same block selection as build_county_tile.corridor_inputs (touching counts as intersecting)."""
    size = int(plan["block_m"])
    x0, y0 = cell[0] * 1000 - HALO, cell[1] * 1000 - HALO
    x1, y1 = cell[0] * 1000 + 1000 + HALO, cell[1] * 1000 + 1000 + HALO
    blocks = set(plan["blocks"])
    for bx in range(math.floor(x0 / size) * size, x1 + 1, size):
        for by in range(math.floor(y0 / size) * size, y1 + 1, size):
            if bx + size < x0 or by + size < y0:
                continue
            key = f"e{bx // 1000}-n{by // 1000}"
            if key in blocks and not (corridor / "blocks" / key / "done.json").exists():
                return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", default=CURRENT["name"])
    parser.add_argument("--queue", type=Path, default=None, help="default: the model's queue")
    parser.add_argument("--min-buildings", type=int, default=25)
    parser.add_argument("--stock", type=int, default=12)
    parser.add_argument("--start", type=float, nargs=2, default=(356500.0, 3782500.0), help="UTM x y to grow outward from")
    args = parser.parse_args()
    model = by_name(args.model)
    queue = (args.queue or model["queue"]).resolve()
    (queue / "todo").mkdir(parents=True, exist_ok=True)
    log = (queue / "daemon.log").open("a")
    density = {tuple(map(int, k.split(","))): v for k, v in json.loads(DENSITY.read_text()).items()}
    cells = sorted((c for c, n in density.items() if n >= args.min_buildings),
                   key=lambda c: math.hypot(c[0] * 1000 + 500 - args.start[0], c[1] * 1000 + 500 - args.start[1]))
    corridor = corridor_dir(model)
    failed: set[tuple[int, int]] = set()
    waiting = False
    print(f"{stamp()} daemon start: model {model['name']} ({model['layout']}), {len(cells)} target cells", file=log, flush=True)
    while not (queue / "STOP").exists():
        if len(list((queue / "todo").iterdir())) >= args.stock:
            time.sleep(30)
            continue
        done = packaged(model)
        upcoming = [c for c in cells if c not in done and c not in failed]
        if not upcoming:
            print(f"{stamp()} all target cells packaged", file=log, flush=True)
            break
        nxt = upcoming[0]
        if corridor is not None:
            plan = json.loads((corridor / "plan.json").read_text())
            nxt = next((c for c in upcoming[:LOOKAHEAD] if corridor_ready(c, corridor, plan)), None)
            if nxt is None:
                if not waiting:
                    print(f"{stamp()} waiting for corridor blocks near la-e{upcoming[0][0]}-n{upcoming[0][1]}", file=log, flush=True)
                waiting = True
                time.sleep(60)
                continue
            waiting = False
        name = f"la-e{nxt[0]}-n{nxt[1]}"
        result = subprocess.run([str(HERE / "geo-python"), str(HERE / "build_county_tile.py"), "--name", name,
                                 "--x0", str(nxt[0] * 1000), "--y0", str(nxt[1] * 1000), *model["build_args"]],
                                capture_output=True, text=True)
        attempt = ATTEMPTS / f"phase1-county-{name}-{model['layout']}-v1"
        if result.returncode == 0 and (attempt / "attempt_manifest.json").exists():
            (queue / "todo" / name).write_text(str(attempt) + "\n")
            print(f"{stamp()} built {name} ({len(done) + 1}/{len(cells)} packaged)", file=log, flush=True)
        else:
            failed.add(nxt)
            print(f"{stamp()} FAILED {name}: {result.stderr[-300:]!r}", file=log, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
