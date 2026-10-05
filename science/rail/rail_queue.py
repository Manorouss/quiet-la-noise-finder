#!/usr/bin/env python3
"""Run the rail pass for every computed tile near a main line, one tile at a time, and keep the results.

For each tile whose newest road run (release_county_tiles.completed_runs) has receivers within 1.5 km of a
track in the line config, and no rail result for that road run yet: prepare a rail attempt from the road run
(build_rail_inputs.py), run it (run_rail_attempt.py, Mac engine on its own port, few threads), and write
work/rail/results/<tile>.json = {"road_attempt", "config", "levels": {source_receiver_key: [D, E, N] LAeq}}.
A new road run for a tile (e.g. a newer model) or a new line-config version makes its rail result stale, and the tile is redone.
Waits while implementation/work/pipeline_control/pause-mac exists (the owner's pause); stops when
implementation/work/pipeline_control/STOP-rail exists. When the queue first runs empty it writes
work/rail/RELEASE: from then on the map build adds trains (before, a half-done line would show seams), and
it keeps checking every 10 minutes for newly computed tiles (compute_up.sh keeps it running).

  rail_queue.py [--config lines/sfv.json] [--port 9140] [--threads 2] [--once]
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

import shapely
from shapely.geometry import box

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "pipeline"))
import build_rail_inputs as B  # noqa: E402
from release_county_tiles import completed_runs  # noqa: E402

PROJECT = HERE.parents[4]
WORK = PROJECT / "implementation/work"
RAIL = WORK / "rail"
CONTROL = WORK / "pipeline_control"
GEO_PYTHON = HERE.parent / "pipeline/geo-python"


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}"
    print(line, flush=True)
    with (RAIL / "queue.log").open("a") as f:
        f.write(line + "\n")


def todo(config: Path) -> list[tuple[str, Path]]:
    cfg = json.loads(config.read_text())
    net = shapely.union_all([g for _, g, _, _ in B.tracks(cfg)])
    out = []
    for tile, (run, _model) in sorted(completed_runs().items()):
        x, y = int(tile.split("-e")[1].split("-")[0]) * 1000, int(tile.split("-n")[1]) * 1000
        if not box(x - 1500, y - 1500, x + 2500, y + 2500).intersects(net):
            continue
        result = RAIL / "results" / f"{tile}.json"
        if result.exists():
            done = json.loads(result.read_text())
            if done.get("road_attempt") == run.name and done.get("config_version", 1) == cfg.get("version", 1):
                continue
        out.append((tile, run))
    return out


def collect(attempt: Path, tile: str, run: Path, config: Path) -> dict:
    keys = {f["properties"]["PK"]: f["properties"]["RECEIVER_KEY"] for f in json.loads((attempt / "input/receivers.geojson").read_text())["features"]}
    levels: dict[str, list] = {}
    with (attempt / "export/receivers_level_rail.csv").open() as f:
        for r in csv.DictReader(f):
            if r["PERIOD"] in ("D", "E", "N"):
                levels.setdefault(keys[int(r["IDRECEIVER"])], [None, None, None])["DEN".index(r["PERIOD"])] = round(float(r["LAEQ"]), 2)
    version = json.loads(config.read_text()).get("version", 1)
    return {"tile": tile, "road_attempt": run.name, "rail_attempt": attempt.name, "config": config.name, "config_version": version,
            "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "levels": levels}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--config", type=Path, default=HERE / "lines/sfv.json")
    parser.add_argument("--port", type=int, default=9140)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--once", action="store_true", help="one tile, then stop")
    args = parser.parse_args()
    (RAIL / "results").mkdir(parents=True, exist_ok=True)
    (RAIL / "attempts").mkdir(parents=True, exist_ok=True)
    while not (CONTROL / "STOP-rail").exists():
        if (CONTROL / "pause-mac").exists():
            time.sleep(60)
            continue
        queue = todo(args.config)
        if not queue:
            if not (RAIL / "RELEASE").exists():
                (RAIL / "RELEASE").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " first pass complete\n")
                log("rail queue empty: first pass complete, trains released to the map")
            if args.once:
                return 0
            time.sleep(600)
            continue
        tile, run = queue[0]
        n = 1
        while (attempt := RAIL / "attempts" / f"{tile}-{run.name.split('-g20')[1]}-rail-v{n}").exists():
            n += 1
        log(f"start {tile} (road {run.name}; {len(queue)} left)")
        built = subprocess.run([str(GEO_PYTHON), str(HERE / "build_rail_inputs.py"), "--road-attempt", str(run), "--config", str(args.config), "--out", str(attempt)],
                               capture_output=True, text=True)
        if built.returncode == 3:
            (RAIL / "results" / f"{tile}.json").write_text(json.dumps({"tile": tile, "road_attempt": run.name, "config": args.config.name,
                                                                       "config_version": json.loads(args.config.read_text()).get("version", 1), "levels": {}, "note": "no track in range"}))
            log(f"skip {tile}: no track in range")
            continue
        if built.returncode != 0:
            log(f"FAILED to prepare {tile}: {built.stderr.strip()[-300:]}")
            return 1
        ran = subprocess.run([str(GEO_PYTHON), str(HERE / "run_rail_attempt.py"), str(attempt), "--port", str(args.port), "--threads", str(args.threads)],
                             capture_output=True, text=True)
        if ran.returncode != 0:
            log(f"FAILED {tile}: {ran.stderr.strip()[-300:]}")
            return 1
        result = collect(attempt, tile, run, args.config)
        (RAIL / "results" / f"{tile}.json").write_text(json.dumps(result, separators=(",", ":")))
        log(f"done {tile}: {json.loads(ran.stdout.strip().splitlines()[-1]).get('seconds')} s, {len(result['levels'])} receivers")
        if args.once:
            return 0
    log("STOP-rail flag: stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
