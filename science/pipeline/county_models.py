"""County model versions, oldest first. The newest one is being computed; per 1 km cell the map
shows the newest model that has a finished run, so older tiles stay until they are replaced.

Each model fixes the tile layout (attempt id part), the builder arguments, the run label the
workers add (LABEL_PREFIX) and their extra run_attempt.py arguments (RUN_ARGS).
"""
from __future__ import annotations

from pathlib import Path

PROJECT = Path(__file__).resolve().parents[5]
WORK = PROJECT / "implementation/work"
ATTEMPTS = WORK / "campaign/county_v1/attempts"
LAYOUT_ARGS = ["--grid", "20", "--facade-spacing", "10", "--dense-near-roads", "50"]

MODELS = [
    {"name": "county-v1", "layout": "g20a50f10", "run_label": "nv", "queue": WORK / "pipeline_queue/county_main",
     "build_args": LAYOUT_ARGS, "run_args": "--no-vertical",
     "summary": "USGS 10 m terrain (engine at 20 m), no sound walls, roads on bridges at grade"},
    {"name": "county-v2", "layout": "g20a50f10t10wb", "run_label": "v2", "queue": WORK / "pipeline_queue/county_v2",
     "build_args": LAYOUT_ARGS + ["--dem-dir", str(WORK / "source_cache/usgs_lidar_dem10"), "--dem-cell", "10",
                                  "--corridor-dir", str(WORK / "source_cache/corridor_v2")],
     "run_args": "--no-vertical --terrain-downscale 1",
     "summary": "2023 lidar terrain at 10 m, lidar-detected sound walls, roads on bridges at deck height"},
    {"name": "county-v3", "layout": "g20a50f10t10wbc", "run_label": "v3", "queue": WORK / "pipeline_queue/county_v3",
     "build_args": LAYOUT_ARGS + ["--dem-dir", str(WORK / "source_cache/usgs_lidar_dem10"), "--dem-cell", "10",
                                  "--corridor-dir", str(WORK / "source_cache/corridor_v2"),
                                  "--classes", "--osm-dir", str(WORK / "source_cache/osm_streets")],
     "run_args": "--no-vertical --terrain-downscale 1 --max-error-db 0.1 --atmo",
     "summary": "v2 plus: weather share per period (day 0.2, evening 0.85, night 0.8), levels per road class (freeway, arterial, local) "
                "and a night weather range, local streets classed by OpenStreetMap (cul-de-sacs, collectors), source pruning 0.1 dB"},
]
CURRENT = MODELS[-1]


def by_name(name: str) -> dict:
    return next(m for m in MODELS if m["name"] == name)


def run_glob(model: dict) -> str:
    """Glob for completed-run attempt directories of a model (any host, any version)."""
    return f"phase1-county-*-{model['layout']}-{model['run_label']}-*-v*"


def package_glob(model: dict) -> str:
    return f"phase1-county-*-{model['layout']}-v1"
