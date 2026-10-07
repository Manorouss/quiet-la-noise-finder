#!/usr/bin/env python3
"""Progress page and pause/resume control for the county compute.

Serves http://<mac>:8765 on the home network (the PC or a phone can open it too):
overall progress, each machine's engines with per-tile progress, a county map of
finished cells, and Pause/Resume buttons per machine.

Pausing writes implementation/work/pipeline_control/pause-<host>. Workers then
start no new tile, and run_attempt.py holds each running tile between WPS polls
and writes held-<host>-<port>. This server then freezes that engine (SIGSTOP on
the Mac, NtSuspendProcess on the PC via D:\\quietla\\tools\\engine_control.ps1)
and marks frozen-<host>-<port>. Resume thaws the engine first, and the runner
continues only after the frozen marker is gone, so no work is lost and no WPS
request can time out.

Usage:
  compute_dashboard.py [--port 8765]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from county_models import CURRENT, MODELS, run_glob

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[4]
WORK = PROJECT / "implementation/work"
CONTROL = WORK / "pipeline_control"
ATTEMPTS = WORK / "campaign/county_v1/attempts"
DENSITY = WORK / "county_building_density_1km.json"
# Progress is for the model being computed now; cells done only on an older model show separately.
QUEUE, LAYOUT, LABEL = CURRENT["queue"], CURRENT["layout"], CURRENT["run_label"]
OLDER = MODELS[:-1]
MIN_BUILDINGS = 25
SSH = ["ssh", "-F", str(Path.home() / ".ssh/quietla_pc_config"), "-o", "ConnectTimeout=10", "quietla-pc"]
MAC_JAVA = "/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home/bin/java"
PROGRESS = re.compile(rb"(\d+(?:\.\d+)?) %\s*$", re.M)


def estimated_receivers(buildings: int) -> float:
    # Fit over the first county tiles (receivers vs DPW buildings per km², R² 0.74).
    return 6507 + 2.69 * buildings


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def ps_lines() -> list[str]:
    return subprocess.run(["ps", "-axo", "pid=,stat=,command="], capture_output=True, text=True).stdout.splitlines()


def cloud_hosts() -> dict[str, dict]:
    """Rented Linux hosts from pipeline_control/cloud_hosts.txt: name -> threads, local port, description (type, zone)."""
    out = {}
    path = CONTROL / "cloud_hosts.txt"
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 3 and not parts[0].startswith("#"):
                out[parts[0]] = {"threads": int(parts[1]), "port": int(parts[2]), "label": " ".join(parts[3:]) or "rented machine"}
    return out


def workers() -> list[dict]:
    found = []
    for line in ps_lines():
        parts = line.split()
        if len(parts) > 7 and parts[2] == "/bin/bash" and parts[3].endswith("queue_worker_persist.sh") and parts[4].rstrip("/") == str(QUEUE):
            found.append({"pid": int(parts[0]), "host": parts[5], "port": int(parts[6]), "threads": int(parts[7])})
    return sorted(found, key=lambda w: (w["host"], w["port"]))


# ---------------------------------------------------------------- freeze / thaw

def mac_engine_pids(port: int) -> list[tuple[int, str]]:
    pids = []
    for line in ps_lines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[2].startswith(MAC_JAVA) and f"LoopbackNoiseModellingServer --port {port} " in parts[2] + " ":
            pids.append((int(parts[0]), parts[1]))
    return pids


def pc_engine(action: str, port: int) -> list[str]:
    command = f"powershell -NoProfile -ExecutionPolicy Bypass -File D:\\quietla\\tools\\engine_control.ps1 -Action {action} -Port {port}"
    result = subprocess.run([*SSH, command], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip()[-300:])
    return [line for line in result.stdout.splitlines() if line.startswith((action, "none"))]


def freeze(host: str, port: int) -> bool:
    if host == "mac":
        pids = mac_engine_pids(port)
        for pid, _ in pids:
            subprocess.run(["kill", "-STOP", str(pid)], check=False)
        return bool(pids)
    if host != "pc":
        return True   # a rented host is not frozen (it is billed either way): its worker just stops after the current tile
    lines = pc_engine("suspend", port)
    return any(line.startswith("suspend") for line in lines)


def thaw(host: str, port: int) -> bool:
    if host == "mac":
        for pid, _ in mac_engine_pids(port):
            subprocess.run(["kill", "-CONT", str(pid)], check=False)
        return True
    if host != "pc":
        return True
    pc_engine("resume", port)
    return True


EVENTS: list[str] = []


def note(message: str) -> None:
    EVENTS.append(f"{datetime.now().strftime('%H:%M:%S')} {message}")
    del EVENTS[:-30]


def reconcile_forever() -> None:
    while True:
        try:
            # Driven by the marker files, so a run is handled whichever process started it.
            engines = {tuple(path.name.split("-")[1:]) for path in CONTROL.glob("held-*-*")}
            engines |= {tuple(path.name.split("-")[1:]) for path in CONTROL.glob("frozen-*-*")}
            for host, port_text in sorted(engines):
                port = int(port_text)
                paused = (CONTROL / f"pause-{host}").exists()
                held = (CONTROL / f"held-{host}-{port}").exists()
                frozen = CONTROL / f"frozen-{host}-{port}"
                if paused and held and not frozen.exists():
                    if freeze(host, port):
                        frozen.write_text(datetime.now(timezone.utc).isoformat() + "\n")
                        note(f"froze {host} engine :{port}")
                elif not paused and frozen.exists():
                    if thaw(host, port):
                        frozen.unlink(missing_ok=True)
                        note(f"resumed {host} engine :{port}")
        except Exception as error:  # keep reconciling; show the problem on the page
            note(f"control error: {type(error).__name__}: {error}")
        time.sleep(3)


# ---------------------------------------------------------------- progress

class Progress:
    """Caches immutable per-attempt facts so a refresh only reads new attempts."""

    def __init__(self) -> None:
        self.density = {tuple(map(int, k.split(","))): v for k, v in json.loads(DENSITY.read_text()).items()}
        self.built: dict[str, tuple[tuple[int, int], int]] = {}
        self.runs: dict[str, dict] = {}
        self.older: dict[str, tuple[int, int]] = {}

    def scan(self) -> None:
        for model in OLDER:
            for path in ATTEMPTS.glob(run_glob(model)):
                if path.name not in self.older and (path / "run_host.json").exists():
                    tile = read_json(path / "attempt_manifest.json").get("tile", {})
                    if tile and not tile["x0"] % 1000 and not tile["y0"] % 1000:
                        self.older[path.name] = (int(tile["x0"]) // 1000, int(tile["y0"]) // 1000)
        for path in ATTEMPTS.glob(f"phase1-county-*-{LAYOUT}-*"):
            name = path.name
            if name in self.built or name in self.runs:
                continue
            if name.endswith(f"-{LAYOUT}-v1"):
                manifest = read_json(path / "attempt_manifest.json")
                tile = manifest.get("tile", {})
                if not tile or tile["x0"] % 1000 or tile["y0"] % 1000:
                    continue
                self.built[name] = ((int(tile["x0"]) // 1000, int(tile["y0"]) // 1000), int(manifest["counts"]["receivers"]))
            elif f"-{LAYOUT}-{LABEL}-" in name and (path / "run_host.json").exists():
                manifest = read_json(path / "attempt_manifest.json")
                run = read_json(path / "phase1_run_manifest.json")
                tile = manifest.get("tile", {})
                if not tile or tile["x0"] % 1000 or tile["y0"] % 1000 or not run.get("ended_at_utc"):
                    continue
                self.runs[name] = {
                    "cell": (int(tile["x0"]) // 1000, int(tile["y0"]) // 1000),
                    "receivers": int(manifest["counts"]["receivers"]),
                    "ended": datetime.fromisoformat(run["ended_at_utc"]).timestamp(),
                    "seconds": float(run.get("elapsed_seconds", 0)),
                    "host": read_json(path / "run_host.json").get("host"),
                    "tile": name.split("phase1-county-", 1)[1].split(f"-{LAYOUT}", 1)[0],
                }

    def summary(self) -> dict:
        self.scan()
        done = {run["cell"]: run["receivers"] for run in self.runs.values()}
        built = {cell: receivers for cell, receivers in self.built.values()}
        cells = {c for c, n in self.density.items() if n >= MIN_BUILDINGS} | set(built) | set(done)
        total = sum(done.get(c) or built.get(c) or estimated_receivers(self.density.get(c, 0)) for c in cells)
        finished = sum(done.values())
        now = time.time()
        rates = {}
        groups = [("all", None), ("mac", ("mac",)), ("pc", ("pc",))] + [(h, (h,)) for h in cloud_hosts()]
        for label, hosts in groups:
            for window in (3 * 3600, 24 * 3600):
                recent = [r for r in self.runs.values() if (hosts is None or r["host"] in hosts) and r["ended"] > now - window]
                if len(recent) >= 3 or window == 24 * 3600:
                    span = min(window, now - min((r["ended"] - r["seconds"] for r in recent), default=now)) or 1
                    rates[label] = sum(r["receivers"] for r in recent) / span if recent else 0.0
                    break
        host_info = {}
        for label, hosts in groups[1:]:
            ends = [r["ended"] for r in self.runs.values() if r["host"] == label]
            host_info[label] = {"n3h": sum(e > now - 3 * 3600 for e in ends), "last": max(ends, default=0)}
        running_cells = set()
        for entry in QUEUE.glob("running/*"):
            match = re.match(r"la-e(\d+)-n(\d+)\.", entry.name)
            if match:
                running_cells.add((int(match[1]), int(match[2])))
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        older = set(self.older.values())
        grid = [[c[0], c[1], 3 if c in done else 2 if c in running_cells else 1 if c in built else 4 if c in older else 0] for c in sorted(cells)]
        recent = sorted(self.runs.values(), key=lambda r: r["ended"], reverse=True)[:8]
        return {
            "total_receivers": round(total), "done_receivers": finished, "fraction": finished / total if total else 0,
            "cells_total": len(cells), "cells_done": len(done), "cells_older_only": len(older - set(done)),
            "model": CURRENT["name"], "model_summary": CURRENT["summary"],
            "rate": rates, "host_info": host_info, "eta_days": (total - finished) / rates["all"] / 86400 if rates.get("all") else None,
            "map": {"x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys), "cells": grid},
            "recent": [{"tile": r["tile"], "host": r["host"], "receivers": r["receivers"], "minutes": round(r["seconds"] / 60, 1),
                        "ended": datetime.fromtimestamp(r["ended"]).strftime("%H:%M")} for r in recent],
        }


def engine_rows(all_workers: list[dict]) -> list[dict]:
    rows = []
    running = {entry.name: entry for entry in QUEUE.glob("running/*")}
    for worker in all_workers:
        host, port, threads = worker["host"], worker["port"], worker["threads"]
        row = {"host": host, "port": port, "threads": threads, "tile": None, "stage": None, "percent": None, "minutes": None,
               "held": (CONTROL / f"held-{host}-{port}").exists(), "frozen": (CONTROL / f"frozen-{host}-{port}").exists()}
        label = f"-{LAYOUT}-{LABEL}-{host}{threads}-v"
        candidates = []
        for name in running:
            tile, _, tile_host = name.rpartition(".")
            if tile_host == host:
                candidates += [p for p in ATTEMPTS.glob(f"phase1-county-{tile}{label}*") if not (p / "run_host.json").exists()]
        if candidates:
            attempt = max(candidates, key=lambda p: p.stat().st_birthtime)
            row["tile"] = attempt.name.split("phase1-county-", 1)[1].split(f"-{LAYOUT}", 1)[0]
            row["stage"] = read_json(attempt / "phase1_run_state.json").get("stage")
            # The runner records stages only during the imports; later stages show as finished WPS jobs.
            finished = {path.name.split("_job_")[0].removeprefix("final_") for path in (attempt / "phase1_provenance").glob("final_*_job_*.xml")}
            if "propagation" in finished:
                row["stage"] = "export"
            elif "road_emissions" in finished:
                row["stage"] = "propagation"
            elif "import_terrain" in finished:
                row["stage"] = "road_emissions"
            row["minutes"] = round((time.time() - attempt.stat().st_birthtime) / 60, 1)
            log = attempt / "phase1_provenance/engine.log"
            if row["stage"] == "propagation" and log.exists():
                with log.open("rb") as handle:
                    handle.seek(max(0, log.stat().st_size - 4096))
                    hits = PROGRESS.findall(handle.read())
                row["percent"] = float(hits[-1]) if hits else 0.0
        rows.append(row)
    return rows


PROGRESS_CACHE = Progress()
LOCK = threading.Lock()


def status() -> dict:
    with LOCK:
        summary = PROGRESS_CACHE.summary()
    all_workers = workers()
    engines = engine_rows(all_workers)
    hosts = {}
    cloud = cloud_hosts()
    for host in ("mac", "pc", *cloud):
        mine = [e for e in engines if e["host"] == host]
        paused = (CONTROL / f"pause-{host}").exists()
        busy = [e for e in mine if e["tile"]]
        if not mine:
            state = "stopped"
        elif paused:
            state = "paused" if all(e["frozen"] for e in busy) else "pausing"
        elif any(e["frozen"] for e in mine):
            state = "resuming"
        else:
            state = "running" if busy else "waiting"
        hosts[host] = {"state": state, "paused": paused, "engines": mine, "cloud": host in cloud, "label": cloud.get(host, {}).get("label")}
    # Days left come from the machines working now, not from the tiles that happened to finish lately: a paused or
    # vanished machine does not count, and one that has no finished tile yet counts at the usual speed per thread.
    info = summary.pop("host_info")
    now = time.time()
    threads = {h: sum(e["threads"] for e in v["engines"]) for h, v in hosts.items()}
    per_thread = sorted(summary["rate"][h] / threads[h] for h in hosts if threads[h] and info[h]["n3h"] >= 3 and summary["rate"].get(h))
    usual = per_thread[len(per_thread) // 2] if per_thread else 0.0
    expected, working = 0.0, 0
    for h, v in hosts.items():
        live = v["state"] in ("running", "resuming") or (v["state"] == "waiting" and now - info[h]["last"] < 1800)
        v["warming_up"] = bool(live and info[h]["n3h"] < 2)
        v["expected_rate"] = usual * threads[h] if v["warming_up"] else summary["rate"].get(h, 0.0)
        if live:
            working += 1
            expected += v["expected_rate"]
    summary["rate_now"], summary["machines_working"] = expected, working
    summary["eta_days"] = (summary["total_receivers"] - summary["done_receivers"]) / expected / 86400 if expected else None
    queue = {d: len(list((QUEUE / d).glob("*"))) for d in ("priority", "todo", "running", "failed")}
    daemon = any("county_daemon.py" in line for line in ps_lines())
    return {"time": datetime.now().strftime("%H:%M:%S"), "summary": summary, "hosts": hosts, "queue": queue,
            "daemon": daemon, "events": EVENTS[-6:]}


def set_pause(host: str, paused: bool) -> None:
    for name in (("mac", "pc") if host == "all" else (host,)):
        flag = CONTROL / f"pause-{name}"
        if paused:
            flag.write_text(datetime.now(timezone.utc).isoformat() + "\n")
        else:
            flag.unlink(missing_ok=True)
        note(f"{'pause' if paused else 'resume'} {name} requested")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self.send(200, (HERE / "compute_dashboard.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self.send(200, json.dumps(status()).encode(), "application/json")
        else:
            self.send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        # The custom header forces a CORS preflight, which this server never answers,
        # so other web pages open in a browser cannot press the buttons.
        if self.headers.get("X-Quiet-LA") != "1" or self.path not in ("/api/pause", "/api/resume"):
            self.send(403, b"forbidden", "text/plain")
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) if length else b"{}")
        host = body.get("host")
        if host not in ("mac", "pc", "all", *cloud_hosts()):
            self.send(400, b"host must be mac, pc or all", "text/plain")
            return
        set_pause(host, self.path == "/api/pause")
        self.send(200, b'{"ok":true}', "application/json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    CONTROL.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=reconcile_forever, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
