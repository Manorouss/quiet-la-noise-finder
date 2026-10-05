#!/usr/bin/env python3
"""Train horns at highway-rail grade crossings, as extra sources of the rail pass (CNOSSOS-EU has no horns).

Rule (49 CFR 222): the horn sounds at least 15 s and at most 20 s before the lead locomotive enters a public
grade crossing, but not more than 1/4 mile (402 m) ahead, unless the crossing is in a quiet zone. Here each
crossing outside a quiet zone (FRA crossing inventory, whistle ban "No"; "24 hr" = quiet zone) gets a horn
line source along the track on both sides, of length L = min(v x 17.5 s, 402 m) per side (private crossings,
outside the federal rule: 7-22 h only, see build_rail_inputs.load_crossings), 4 m above the
rail (locomotive roof), omnidirectional (DIR_ID 0). Trains from either side sound on their side only, so the
2L line carries half the trains, each sounding over L:

  emission per metre  W' = W_horn x N / 2 / (3600 s x v)        (N trains per hour, v in m/s)

W_horn is calibrated so that one pass-by gives the FTA reference horn SEL of 110 dBA at 50 ft at the crossing
(FTA Transit Noise and Vibration Impact Assessment Manual, 2018); calibrate.py --horn checks it. The spectrum
is a generic locomotive-horn shape (chord fundamentals ~250-600 Hz with harmonics), A-weighted peak around
500-1000 Hz.
"""
from __future__ import annotations

import math

import shapely
from shapely.geometry import LineString, Point
from shapely.ops import substring

THIRDS = [50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000]
A_WEIGHT = [-30.2, -26.2, -22.5, -19.1, -16.1, -13.4, -10.9, -8.6, -6.6, -4.8, -3.2, -1.9, -0.8, 0.0, 0.6, 1.0, 1.2, 1.3, 1.2, 1.0, 0.5, -0.1, -1.1, -2.5]
SHAPE = [-28, -26, -24, -20, -16, -12, -8, -4, -2, 0, 0, 0, 0, 0, -1, -2, -4, -5, -7, -9, -12, -15, -19, -23]   # unweighted, dB
HORN_LW_DBA = 143.3   # A-weighted sound power of a sounding horn, calibrated (calibrate.py --horn) to SEL 110 dBA at 50 ft
HORN_HEIGHT_M = 4.0
HORN_SECONDS = 17.5
HORN_MAX_M = 402.0
SNAP_M = 30.0


def band_levels(lw_dba: float) -> list[float]:
    """Unweighted third-octave sound power levels with the horn shape whose A-weighted sum is lw_dba."""
    a_sum = 10 * math.log10(sum(10 ** ((s + a) / 10) for s, a in zip(SHAPE, A_WEIGHT)))
    return [round(lw_dba - a_sum + s, 2) for s in SHAPE]


def emission(trains_per_hour: float, speed_kmh: float, lw_dba: float = HORN_LW_DBA) -> list[float]:
    """Per-metre emission of a crossing's horn line (both sides, 2L long) for N trains per hour at speed v."""
    if trains_per_hour <= 0:
        return [-99.0] * len(THIRDS)
    v = max(speed_kmh, 10.0) / 3.6
    offset = 10 * math.log10(trains_per_hour / 2 / (3600 * v))
    return [round(b + offset, 2) for b in band_levels(lw_dba)]


def horn_line(track: LineString, crossing: Point, speed_kmh: float) -> LineString | None:
    """The 2L stretch of track centred on the crossing (None if the crossing is not on this track)."""
    if track.distance(crossing) > SNAP_M:
        return None
    half = min(max(speed_kmh, 10.0) / 3.6 * HORN_SECONDS, HORN_MAX_M)
    at = track.project(crossing)
    start, end = max(0.0, at - half), min(track.length, at + half)
    if end - start < 10:
        return None
    return substring(track, start, end)


def feature(line: LineString, pk: int, rates: dict, speed_kmh: float, lw_dba: float = HORN_LW_DBA, crossing: str = "") -> dict:
    """A rail-source feature (same columns as the engine's LW_RAILWAY) for one horn line; rates = trains/h per D/E/N."""
    props = {"PK": pk, "PK_SECTION": -1, "DIR_ID": 0, "GS": 0.0, "SOURCE": "horn", "CROSSING": crossing}
    for period in "DEN":
        for f, value in zip(THIRDS, emission(rates[period], speed_kmh, lw_dba)):
            props[f"HZ{period}{f}"] = value
    coords = [[x, y, HORN_HEIGHT_M] for x, y in line.coords]
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords}, "properties": props}
