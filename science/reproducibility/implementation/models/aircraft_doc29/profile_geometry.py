"""Construct a straight-route 3-D fixed-profile path from Volume 3 A inputs.

Implements Doc 29 Volume 2 §§3.6.1, 3.6.4, and 3.6.5 for fixed-point
profiles. This first version does not synthesize performance profiles from
ANP procedures and does not add turn or track-dispersion geometry.
"""
from __future__ import annotations

from math import sqrt

APPROACH_HEIGHTS_M = (18.9, 41.5, 68.3, 102.1, 147.5, 214.9, 334.9, 609.6, 1289.6)
MAX_APPROACH_HEIGHT_M = 1289.6
MAX_SPEED_CHANGE_MPS = 10.0
FT_PER_M = 1.0 / 0.3048


def _interpolate_profile(a: dict, b: dict, fraction_distance: float) -> dict:
    f = fraction_distance
    v1, v2 = float(a["speed_mps"]), float(b["speed_mps"])
    p1, p2 = float(a["power"]), float(b["power"])
    if p1 >= 0 and p2 >= 0:
        power = sqrt(max(0.0, p1 * p1 + f * (p2 * p2 - p1 * p1)))
    elif p1 <= 0 and p2 <= 0:
        power = -sqrt(max(0.0, p1 * p1 + f * (p2 * p2 - p1 * p1)))
    elif p1 < 0 < p2:
        power = p1 + (p2 - p1) * sqrt(f)
    else:
        power = p2 + (p1 - p2) * sqrt(1.0 - f)
    return {
        "s_m": float(a["s_m"]) + f * (float(b["s_m"]) - float(a["s_m"])),
        "z_m": float(a["z_m"]) + f * (float(b["z_m"]) - float(a["z_m"])),
        "speed_mps": sqrt(max(0.0, v1 * v1 + f * (v2 * v2 - v1 * v1))),
        "power": power,
        "bank_deg": float(a.get("bank_deg", 0.0)) + f * (float(b.get("bank_deg", 0.0)) - float(a.get("bank_deg", 0.0))),
    }


def _point_at_distance(a: dict, b: dict, s_m: float) -> dict:
    f = (s_m - float(a["s_m"])) / (float(b["s_m"]) - float(a["s_m"]))
    return _interpolate_profile(a, b, f)


def _speed_subsegment_fractions(v1: float, v2: float) -> list[float]:
    dv = abs(v2 - v1)
    if dv <= 20.0 * 0.5144444444444445:
        return []
    n = int(1.0 + dv / MAX_SPEED_CHANGE_MPS)
    fractions = []
    avg_v = (v1 + v2) / 2.0
    for k in range(1, n):
        time_fraction = k / n
        s_fraction = (v1 * time_fraction + 0.5 * (v2 - v1) * time_fraction**2) / avg_v
        fractions.append(s_fraction)
    return fractions


def _speed_subsegment_positions(v1: float, v2: float) -> list[tuple[float, float]]:
    """Return (distance fraction, elapsed-time fraction) for inserted nodes."""
    dv = abs(v2 - v1)
    if dv <= 20.0 * 0.5144444444444445:
        return []
    n = int(1.0 + dv / MAX_SPEED_CHANGE_MPS)
    avg_v = (v1 + v2) / 2.0
    points = []
    for k in range(1, n):
        t = k / n
        s_fraction = (v1 * t + 0.5 * (v2 - v1) * t**2) / avg_v
        points.append((s_fraction, t))
    return points


def _approach_height_fractions(a: dict, b: dict, is_arrival: bool) -> list[float]:
    z1, z2 = float(a["z_m"]), float(b["z_m"])
    if not is_arrival or z2 >= z1:
        return []
    cap = min(z1, MAX_APPROACH_HEIGHT_M)
    nearest = min(APPROACH_HEIGHTS_M, key=lambda h: abs(h - cap))
    targets = [cap * target / nearest for target in APPROACH_HEIGHTS_M if target < nearest]
    if z2 < MAX_APPROACH_HEIGHT_M < z1:
        targets.append(MAX_APPROACH_HEIGHT_M)
    return sorted((z1 - z) / (z1 - z2) for z in targets if z2 < z < z1)


def _climb_height_fractions(a: dict, b: dict, is_departure: bool) -> list[float]:
    """Doc 29 §3.6.4 scaled-height subdivision for a climbing segment."""
    z1, z2 = float(a["z_m"]), float(b["z_m"])
    if not is_departure or z2 <= z1 or z1 >= MAX_APPROACH_HEIGHT_M or z1 < 0:
        return []
    endpoint = min(z2, MAX_APPROACH_HEIGHT_M)
    nearest_index = min(range(len(APPROACH_HEIGHTS_M)),
                        key=lambda i: abs(APPROACH_HEIGHTS_M[i] - endpoint))
    nearest = APPROACH_HEIGHTS_M[nearest_index]
    # The scaled set reaches the original endpoint at nearest_index. Keep only
    # levels strictly between this segment's original endpoints; the prior
    # segment's endpoint is already present and must not be duplicated.
    targets = [endpoint * APPROACH_HEIGHTS_M[i] / nearest
               for i in range(nearest_index + 1)]
    return sorted((z - z1) / (z2 - z1) for z in targets if z1 < z < z2)


def _deduplicate(points: list[dict]) -> list[dict]:
    out = []
    for p in points:
        if out and abs(p["s_m"] - out[-1]["s_m"]) < 1e-9 and abs(p["z_m"] - out[-1]["z_m"]) < 1e-9:
            old = out[-1]
            if (abs(p["speed_mps"] - old["speed_mps"]) < 1e-9
                    and abs(p["power"] - old["power"]) < 1e-9):
                continue
        out.append(p)
    return out


def segment_profile_points(points: list[dict], is_arrival: bool) -> list[dict]:
    """Apply Doc 29 height-first then speed segmentation to profile nodes."""
    if len(points) < 2:
        raise ValueError("a fixed profile needs at least two points")
    result = [points[0]]
    for a, b in zip(points, points[1:]):
        height_fractions = sorted(set(_approach_height_fractions(a, b, is_arrival))
                                  | set(_climb_height_fractions(a, b, not is_arrival)))
        height_nodes = [a]
        height_nodes.extend(_interpolate_profile(a, b, f) for f in height_fractions)
        height_nodes.append(b)
        for height_start, height_end in zip(height_nodes, height_nodes[1:]):
            speed_positions = _speed_subsegment_positions(
                height_start["speed_mps"], height_end["speed_mps"])
            for f, time_fraction in speed_positions:
                point = _interpolate_profile(height_start, height_end, f)
                # §3.6.5 subdivides speed changes as for the ground roll and
                # uses equal power increments at equal-time nodes. §3.6.4
                # height nodes above retain Eq. (3-2d) interpolation.
                point["power"] = height_start["power"] + time_fraction * (
                    height_end["power"] - height_start["power"])
                result.append(point)
            result.append(height_end)
    return _deduplicate(result)


def construct_fixed_profile(case: dict) -> list[dict]:
    """Construct a 3-D straight-track profile from the case's A-sheet fields."""
    route = case[f"route_{case['official_test_case']['route_id']}"]
    profile_data = sorted(case["fixed_point_profile"], key=lambda r: int(r["Point Number"]))
    is_arrival = case["official_test_case"]["operation"] == "A"
    route_start = min(float(p["X coordinate (m)"]) for p in route)
    route_end = max(float(p["X coordinate (m)"]) for p in route)
    runway_sor = float(case["runway_09"]["SOR X coordinate (m)"])
    points = [{
        "s_m": float(r["Distance (m)"]),
        "z_m": float(r["Altitude (m)"]),
        "speed_mps": float(r["True Airspeed (m/s)"]),
        "power": float(r["Corrected Net Thrust (lb or % per engine)"]),
        "bank_deg": 0.0,
        "source_profile_point": int(r["Point Number"]),
    } for r in profile_data]

    # Arrival profile distance is referenced to touchdown; align the final
    # airborne profile point with the runway SOR coordinate from A-10.
    if is_arrival:
        first_ground = next((i for i, p in enumerate(points) if p["z_m"] <= 1.0), None)
        if first_ground is None or first_ground == 0:
            raise ValueError("arrival fixed profile does not mark the ground-roll boundary")
        profile_anchor = points[first_ground - 1]["s_m"]
        offset = runway_sor - profile_anchor
    else:
        offset = runway_sor - points[0]["s_m"]
    for point in points:
        point["s_m"] += offset
    route_end = max(route_end, points[-1]["s_m"])

    # Doc 29 §3.6.6 extends arrival profiles backward with first-point speed
    # and thrust and linearly extrapolated height when the route is longer.
    if is_arrival and route_start < points[0]["s_m"]:
        first, second = points[0], points[1]
        slope = (second["z_m"] - first["z_m"]) / (second["s_m"] - first["s_m"])
        points.insert(0, {
            "s_m": route_start,
            "z_m": first["z_m"] + slope * (route_start - first["s_m"]),
            "speed_mps": first["speed_mps"],
            "power": first["power"],
            "bank_deg": first["bank_deg"],
            "source_profile_point": "arrival_start_extrapolation",
        })
    if points[-1]["s_m"] < route_end:
        last, prior = points[-1], points[-2]
        slope = (last["z_m"] - prior["z_m"]) / (last["s_m"] - prior["s_m"])
        points.append({
            "s_m": route_end, "z_m": last["z_m"] + slope * (route_end - last["s_m"]),
            "speed_mps": last["speed_mps"], "power": last["power"],
            "bank_deg": last["bank_deg"], "source_profile_point": "route_end_extension",
        })

    return segment_profile_points(points, is_arrival)


def segments_from_fixed_profile(case: dict) -> list[dict]:
    points = construct_fixed_profile(case)
    return [{
        "segment_id": i,
        "start": a,
        "end": b,
        "length_3d_m": ((b["s_m"] - a["s_m"])**2 + (b["z_m"] - a["z_m"])**2)**0.5,
    } for i, (a, b) in enumerate(zip(points, points[1:]), 1)]
