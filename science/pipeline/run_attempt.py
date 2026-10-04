#!/usr/bin/env python3
"""Run one NoiseModelling attempt on the Mac or on the Windows PC.

Stages a fresh attempt from an existing one (same immutable inputs, new id),
then drives the unchanged Phase 1 runner (implementation/work/campaign/
run_phase1_regional_attempt.py). Only the engine launch, thread count,
diffraction flags and file paths are swapped:

  --host mac  engine runs locally on overlay:loopback-helper:installed-lib
  --host pc   inputs are copied to D:\\quietla\\attempts\\<id>, the engine runs
              on the PC (identical jars, Temurin 21.0.7) behind an SSH port
              forward, and the export CSV is copied back.

A rerun of the same source and label stages the next free -v<N>; existing
attempts are never overwritten. While implementation/work/pipeline_control/
pause-<host> exists the runner holds between WPS polls (see PausableClock),
so compute_dashboard.py can freeze and later resume the engine.

Usage:
  run_attempt.py --source-attempt <dir> --label t16 --host pc --threads 16 --port 9101
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[5]
WORK = PROJECT / "implementation/work"
CAMPAIGN = WORK / "campaign"
CONTROL = WORK / "pipeline_control"
RUNNER = CAMPAIGN / "run_phase1_regional_attempt.py"
HELPER = CAMPAIGN / "noisemodelling_loopback_launcher/noisemodelling-loopback-launcher.jar"
HF_OVERLAY = CAMPAIGN / "tarzana_full_mixed_road_v1/sentinel_forensics/r02_c04_source_diagnostic_v1/engine_overlay_hf_v1/runtime_overlay/classes"
MAC_LIB = Path("/Volumes/NoiseModelling/NoiseModelling.app/Contents/app/lib")
MAC_JAVA = Path("/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home/bin/java")
MAIN = "org.quietla.noisemodelling.LoopbackNoiseModellingServer"

SSH_CONFIG = Path.home() / ".ssh/quietla_pc_config"
PC = "quietla-pc"
PC_ROOT = "D:/quietla"
PC_JAVA = f"{PC_ROOT}/jdk21/jdk-21.0.7+6/bin/java.exe"
PC_LIB = f"{PC_ROOT}/nm-lib"
PC_HELPER = f"{PC_ROOT}/helper/noisemodelling-loopback-launcher.jar"
PC_OVERLAY = f"{PC_ROOT}/overlays/hf_v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ssh(command: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", "-F", str(SSH_CONFIG), PC, command], capture_output=True, text=True, check=check)


def win(path: str) -> str:
    return path.replace("/", "\\")


def stage(source: Path, label: str) -> Path:
    manifest = json.loads((source / "attempt_manifest.json").read_text())
    base = f"{manifest['attempt_id'].rsplit('-v', 1)[0]}-{label}"
    version = 1
    while (source.parent / f"{base}-v{version}").exists():
        version += 1
    attempt_id = f"{base}-v{version}"
    target = source.parent / attempt_id
    (target / "input").mkdir(parents=True)
    for record in manifest["inputs"].values():
        shutil.copy2(source / record["path"], target / record["path"])
    manifest["attempt_id"] = attempt_id
    manifest["region_key"] = f"{manifest['region_key']}_{label.replace('-', '_')}"
    manifest["table_prefix"] = f"{manifest['table_prefix']}_{label.upper().replace('-', '_')}"[:56]
    manifest["staged_from"] = {"attempt": source.name, "manifest_sha256": sha(source / "attempt_manifest.json")}
    (target / "attempt_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    auth = {
        "schema_version": 1, "attempt_id": attempt_id, "region_key": manifest["region_key"],
        "table_prefix": manifest["table_prefix"], "attempt_manifest_sha256": sha(target / "attempt_manifest.json"),
        "runner_sha256": sha(RUNNER), "propagation_authorized": True, "public_output_writes_authorized": False,
    }
    (target / "phase1_authorization.json").write_text(json.dumps(auth, indent=1) + "\n")
    return target


class PausableClock:
    """Stands in for the runner's time module while a host can be paused.

    sleep() is where the runner waits between WPS polls, so no request is in
    flight there. While pause-<host> or frozen-<host>-<port> exists it writes
    held-<host>-<port> (the dashboard freezes the engine only after seeing it)
    and waits; monotonic() leaves the held time out so no runner timeout fires.
    """

    def __init__(self, host: str, port: int):
        self.pause = CONTROL / f"pause-{host}"
        self.frozen = CONTROL / f"frozen-{host}-{port}"
        self.held = CONTROL / f"held-{host}-{port}"
        self.held_seconds = 0.0

    def __getattr__(self, name: str):
        return getattr(time, name)

    def monotonic(self) -> float:
        return time.monotonic() - self.held_seconds

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
        if not (self.pause.exists() or self.frozen.exists()):
            return
        start = time.monotonic()
        self.held.write_text(f"{os.getpid()}\n")
        try:
            while self.pause.exists() or self.frozen.exists():
                time.sleep(1)
        finally:
            self.held.unlink(missing_ok=True)
            self.held_seconds += time.monotonic() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source-attempt", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--host", choices=("mac", "pc"), default="mac")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--no-horizontal", action="store_true", help="disable diffraction over horizontal edges (roofs, terrain)")
    parser.add_argument("--no-vertical", action="store_true",
                        help="disable diffraction around vertical edges (lateral paths); CNOSSOS-EU / NoiseModelling: off for road sources")
    parser.add_argument("--max-error-db", default="0.0", help="NoiseModelling confMaxError source pruning")
    parser.add_argument("--refl-order", type=int, default=None, help="confReflOrder override (reflections off by default)")
    parser.add_argument("--refl-dist", type=float, default=None, help="confMaxReflDist override in metres")
    parser.add_argument("--terrain-downscale", type=int, default=None, help="Import_Asc_File downscale override (runner default 2)")
    parser.add_argument("--keep-runtime", action="store_true",
                        help="keep the engine database (by default it is deleted after a successful run; exports and manifests stay)")
    args = parser.parse_args()

    attempt = stage(args.source_attempt.resolve(), args.label)
    pc_attempt = f"{PC_ROOT}/attempts/{attempt.name}"
    if args.host == "pc":
        ssh(f'powershell -NoProfile -Command "New-Item -ItemType Directory -Force {win(pc_attempt)}\\input,{win(pc_attempt)}\\export | Out-Null"')
        subprocess.run(["scp", "-F", str(SSH_CONFIG), "-q", *[str(p) for p in sorted((attempt / "input").iterdir())],
                        f"{PC}:{pc_attempt}/input/"], check=True)

    sys.path.insert(0, str(RUNNER.parent))
    import run_phase1_regional_attempt as runner  # noqa: E402

    def engine_command(port: int, runtime: Path) -> list[str]:
        runtime.mkdir(parents=True, exist_ok=True)
        if args.host == "mac":
            classpath = f"{HF_OVERLAY}:{HELPER}:{MAC_LIB}/*"
            return [str(MAC_JAVA), "-cp", classpath, MAIN, "--port", str(port), "--working-dir", str(runtime.resolve()),
                    "--unsecure", "--browser-skip"]
        classpath = ";".join([win(PC_OVERLAY), win(PC_HELPER), win(PC_LIB) + "\\*"])
        remote = f'"{win(PC_JAVA)}" -cp "{classpath}" {MAIN} --port {port} --working-dir "{win(pc_attempt)}\\runtime" --unsecure --browser-skip'
        return ["ssh", "-F", str(SSH_CONFIG), "-o", "ExitOnForwardFailure=yes", "-L", f"{port}:127.0.0.1:{port}", PC, remote]

    original_propagation = runner.propagation

    def propagation(prefix: str) -> dict[str, object]:
        values = dict(original_propagation(prefix))
        values.update(confMaxError=str(args.max_error_db), confThreadNumber=str(args.threads))
        if args.no_horizontal:
            values["confDiffHorizontal"] = "false"
        if args.no_vertical:
            values["confDiffVertical"] = "false"
        if args.refl_order is not None:
            values["confReflOrder"] = str(args.refl_order)
        if args.refl_dist is not None:
            values["confMaxReflDist"] = str(args.refl_dist)
        return values

    original_execute = runner.execute_wps

    def execute_wps(endpoint, provenance, events, label, process, parameters):
        if args.terrain_downscale is not None and "downscale" in parameters:
            parameters = {**parameters, "downscale": str(args.terrain_downscale)}
        if args.host == "mac":
            return original_execute(endpoint, provenance, events, label, process, parameters)
        remote = dict(parameters)
        for key in ("pathFile", "exportPath"):
            if key in remote:
                local = Path(str(remote[key]))
                remote[key] = win(f"{pc_attempt}/{local.relative_to(attempt.resolve()).as_posix()}")
        job = original_execute(endpoint, provenance, events, label, process, remote)
        if "exportPath" in parameters:
            subprocess.run(["scp", "-F", str(SSH_CONFIG), "-q", f"{PC}:{pc_attempt}/{Path(str(parameters['exportPath'])).relative_to(attempt.resolve()).as_posix()}",
                            str(parameters["exportPath"])], check=True)
        return job

    runner.engine_command, runner.propagation, runner.execute_wps = engine_command, propagation, execute_wps
    CONTROL.mkdir(parents=True, exist_ok=True)
    runner.time = PausableClock(args.host, args.port)
    try:
        runner.main_run(argparse.Namespace(attempt_dir=attempt, port=args.port, startup_timeout=120.0))
    finally:
        if args.host == "pc":
            ssh(f'powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort {args.port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}"', check=False)
    if not args.keep_runtime:
        # The H2 database and the PC's input copy are only needed while the engine runs.
        shutil.rmtree(attempt / "runtime", ignore_errors=True)
        if args.host == "pc":
            ssh(f'powershell -NoProfile -Command "Remove-Item -Recurse -Force {win(pc_attempt)} -ErrorAction SilentlyContinue"', check=False)
    (attempt / "run_host.json").write_text(json.dumps({
        "host": args.host, "threads": args.threads, "horizontal_diffraction": not args.no_horizontal, "vertical_diffraction": not args.no_vertical,
        "max_error_db": args.max_error_db, "overlay": "hf_v1", "pc_attempt": pc_attempt if args.host == "pc" else None,
        "refl_order": args.refl_order if args.refl_order is not None else 0, "refl_dist_m": args.refl_dist, "terrain_downscale": args.terrain_downscale or 2,
    }, indent=1) + "\n")
    print(attempt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
