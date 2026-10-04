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

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[4]
WORK = PROJECT / "implementation/work"
CONTROL = WORK / "pipeline_control"
QUEUE = WORK / "pipeline_queue/county_main"
ATTEMPTS = WORK / "campaign/county_v1/attempts"
DENSITY = WORK / "county_building_density_1km.json"
LAYOUT = "g20a50f10"
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


def workers() -> list[dict]:
    found = []
    for line in ps_lines():
        parts = line.split()
        if len(parts) > 7 and parts[2] == "/bin/bash" and parts[3].endswith("queue_worker_persist.sh") and parts[4].endswith("county_main"):
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
    lines = pc_engine("suspend", port)
    return any(line.startswith("suspend") for line in lines)


def thaw(host: str, port: int) -> bool:
    if host == "mac":
        for pid, _ in mac_engine_pids(port):
            subprocess.run(["kill", "-CONT", str(pid)], check=False)
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

    def scan(self) -> None:
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
            elif f"-{LAYOUT}-nv-" in name and (path / "run_host.json").exists():
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
        for label, hosts in (("all", ("mac", "pc")), ("mac", ("mac",)), ("pc", ("pc",))):
            for window in (3 * 3600, 24 * 3600):
                recent = [r for r in self.runs.values() if r["host"] in hosts and r["ended"] > now - window]
                if len(recent) >= 3 or window == 24 * 3600:
                    span = min(window, now - min((r["ended"] - r["seconds"] for r in recent), default=now)) or 1
                    rates[label] = sum(r["receivers"] for r in recent) / span if recent else 0.0
                    break
        running_cells = set()
        for entry in QUEUE.glob("running/*"):
            match = re.match(r"la-e(\d+)-n(\d+)\.", entry.name)
            if match:
                running_cells.add((int(match[1]), int(match[2])))
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        grid = [[c[0], c[1], 3 if c in done else 2 if c in running_cells else 1 if c in built else 0] for c in sorted(cells)]
        recent = sorted(self.runs.values(), key=lambda r: r["ended"], reverse=True)[:8]
        return {
            "total_receivers": round(total), "done_receivers": finished, "fraction": finished / total if total else 0,
            "cells_total": len(cells), "cells_done": len(done),
            "rate": rates, "eta_days": (total - finished) / rates["all"] / 86400 if rates.get("all") else None,
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
        label = f"-{LAYOUT}-nv-{host}{threads}-v"
        candidates = []
        for name in running:
            tile, _, tile_host = name.rpartition(".")
            if tile_host == host:
                candidates += [p for p in ATTEMPTS.glob(f"phase1-county-{tile}{label}*") if not (p / "run_host.json").exists()]
        if candidates:
            attempt = max(candidates, key=lambda p: p.stat().st_birthtime)
            row["tile"] = attempt.name.split("phase1-county-", 1)[1].split(f"-{LAYOUT}", 1)[0]
            row["stage"] = read_json(attempt / "phase1_run_state.json").get("stage")
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
    for host in ("mac", "pc"):
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
        hosts[host] = {"state": state, "paused": paused, "engines": mine}
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
        if host not in ("mac", "pc", "all"):
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
