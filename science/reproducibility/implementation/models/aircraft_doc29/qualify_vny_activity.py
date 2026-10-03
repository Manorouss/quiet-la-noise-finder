"""Summarize activity observed near VNY without calling traces movements."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

from .reclip_paths import _iter_json_paths

# FAA airport reference point from the FAA Airport/Facility Directory entry.
# It is an airport reference point, not either runway threshold/centerline.
AIRPORT_REFERENCE = (34.2098333333, -118.49)
RADII_M = (1_000, 3_000, 5_000, 10_000)


def distance_m(lat: float, lon: float, reference=AIRPORT_REFERENCE) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat, lon, reference[0], reference[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(a))


def run(paths_file: Path) -> dict:
    total = 0
    radius_paths = Counter()
    radius_points = Counter()
    nearest_types = {radius: Counter() for radius in RADII_M}
    nearest_quality = {radius: {"position_source": Counter(), "geometric_altitude_state_and_datum": Counter(), "primary_altitude_state_and_datum": Counter()} for radius in RADII_M}
    classified_arrival_departure = 0
    classified_runway = 0
    for path in _iter_json_paths(paths_file):
        total += 1
        points = path["points"]
        distances = [distance_m(float(p["lat"]), float(p["lon"])) for p in points]
        nearest_idx = min(range(len(distances)), key=distances.__getitem__)
        nearest = points[nearest_idx]
        nearest_distance = distances[nearest_idx]
        for radius in RADII_M:
            n = sum(d <= radius for d in distances)
            if n:
                radius_paths[radius] += 1
                radius_points[radius] += n
                nearest_types[radius][path.get("type_code") or "missing"] += 1
                nearest_quality[radius]["position_source"][nearest.get("position_source", "unknown")] += 1
                nearest_quality[radius]["geometric_altitude_state_and_datum"][nearest.get("geometric_altitude_state", "unknown") + "/" + nearest.get("geometric_altitude_datum", "unknown")] += 1
                nearest_quality[radius]["primary_altitude_state_and_datum"][nearest.get("altitude_state", "unknown") + "/" + nearest.get("altitude_datum", "unknown")] += 1

        # Current normalized traces have no airport origin/destination, runway,
        # operation class, airport elevation conversion, or active-runway input.
        # Thus geometric proximity and instantaneous track cannot establish a
        # VNY arrival/departure or runway assignment.

    return {
        "schema": "quiet_la_vny_activity_proximity_qualification_v1",
        "source_activity_file": paths_file.name,
        "local_date": "2026-01-15",
        "timezone": "America/Los_Angeles",
        "utc_window": {"start_inclusive": "2026-01-15T08:00:00Z", "end_exclusive": "2026-01-16T08:00:00Z"},
        "airport_reference": {
            "lat": AIRPORT_REFERENCE[0], "lon": AIRPORT_REFERENCE[1],
            "source": "FAA Airport/Facility Directory, Van Nuys (VNY)(KVNY), 22 Jan 2026 cycle, p.308",
            "source_url": "https://aeronav.faa.gov/afd/22JAN2026/SW_308_22JAN2026.pdf",
            "meaning": "airport reference point only; not a runway threshold or receiver location",
        },
        "regional_path_fragments_examined_not_flight_count": total,
        "airport_proximity_counts": {
            f"within_{radius}m": {
                "path_fragments_not_flight_count": radius_paths[radius],
                "observed_points": radius_points[radius],
                "type_code_path_fragments": dict(sorted(nearest_types[radius].items(), key=lambda v: (-v[1], v[0]))),
            } for radius in RADII_M
        },
        "nearest_observed_point_availability_by_radius": {
            f"within_{radius}m": {k: dict(v) for k, v in nearest_quality[radius].items()}
            for radius in RADII_M
        },
        "runway_and_operation_classification": {
            "confirmed_arrival_departure_path_fragments": classified_arrival_departure,
            "confirmed_runway_path_fragments": classified_runway,
            "status": "not_classified",
            "reason": "The minimized trace release has no destination/origin, confirmed runway, movement event identifier, or active-runway schedule. The FAA airport reference point is not a runway threshold; proximity and track direction alone cannot distinguish VNY operations from transit/overflight, traffic pattern, or other nearby-airport movement. Geometric altitude is WGS84 ellipsoid height and is not converted to airport/MSL datum.",
            "known_field_geometry": "FAA lists parallel runways 16L/34R and 16R/34L. Assigning one requires runway threshold/centerline geometry and a qualified direction/operation test; this report does not infer it from activity proximity.",
        },
        "acoustic_modeling_status": "no activity path is converted to noise or an SEL event; no matched prediction or measurement accuracy claim",
        "independent_measurements": "Jan 15 LAWA daily Local A/C CNEL values remain untouched and are not used for filtering, tuning, or classification.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paths", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    result = run(args.paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({
        "regional_path_fragments_examined_not_flight_count": result["regional_path_fragments_examined_not_flight_count"],
        "airport_proximity_counts": {k: {"path_fragments_not_flight_count": v["path_fragments_not_flight_count"], "observed_points": v["observed_points"]} for k, v in result["airport_proximity_counts"].items()},
        "runway_and_operation_classification": result["runway_and_operation_classification"],
    }, indent=2))


if __name__ == "__main__":
    main()
