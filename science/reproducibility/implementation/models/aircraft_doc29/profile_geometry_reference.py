"""Build fixed-profile segment geometry independently, then compare with B-2."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from .profile_geometry import segments_from_fixed_profile


def run(case_path: Path) -> dict:
    case = json.loads(case_path.read_text())
    segments = segments_from_fixed_profile(case)
    reference = case["published_segment_results"]
    comparison = []
    for generated, expected in zip(segments, reference):
        start_error = math.hypot(
            generated["start"]["s_m"] - float(expected["segment_start_x(ft)"]) * 0.3048,
            generated["start"]["z_m"] - float(expected["segment_start_z(ft)"]) * 0.3048,
        )
        end_error = math.hypot(
            generated["end"]["s_m"] - float(expected["segment_end_x(ft)"]) * 0.3048,
            generated["end"]["z_m"] - float(expected["segment_end_z(ft)"]) * 0.3048,
        )
        comparison.append({
            "segment_id": generated["segment_id"],
            "constructed_start": generated["start"],
            "constructed_end": generated["end"],
            "constructed_3d_length_m": generated["length_3d_m"],
            "ecac_geometry_start_error_m": start_error,
            "ecac_geometry_end_error_m": end_error,
        })
    errors = [r["ecac_geometry_end_error_m"] for r in comparison]
    return {
        "schema": "quiet_la_doc29_independent_profile_geometry_result_v1",
        "case_id": case["official_test_case"]["case_id"],
        "operation": case["official_test_case"]["operation"],
        "receptor_id": case["official_test_case"]["receptor_id"],
        "constructed_segment_count": len(segments),
        "published_segment_count": len(reference),
        "endpoint_error_mean_m": sum(errors) / len(errors),
        "endpoint_error_max_m": max(errors),
        "segment_count_matches": len(segments) == len(reference),
        "scope": "Segment nodes are constructed only from A-sheet fixed profile, route, and runway fields using Doc 29 Volume 2 §§3.6.1, 3.6.4, 3.6.5, and 3.6.6. B-2 geometry is read only after construction for comparison. This verifies fixed-profile segmentation geometry, not aircraft performance synthesis or acoustic segment SEL.",
        "reference_workbook_sha256": case["source"]["workbook_sha256"],
        "segments": comparison,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", type=Path, default=Path(__file__).parent / "reference" / "jetfas_reference_case.json")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    result = run(args.case)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(json.dumps({k: result[k] for k in (
        "constructed_segment_count", "published_segment_count",
        "endpoint_error_mean_m", "endpoint_error_max_m", "segment_count_matches",
    )}, indent=2))


if __name__ == "__main__":
    main()
