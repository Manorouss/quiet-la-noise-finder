"""Run the first straight-flight Doc 29 verification aggregation (not full Doc 29)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .doc29 import energy_sum_db


def run(case_path: Path) -> dict:
    data = json.loads(case_path.read_text())
    c = data["official_test_case"]
    segments = data["published_segment_results"]
    if not segments or any(r["case_ID"] != c["case_id"] or r["receptor_ID"] != c["receptor_id"] for r in segments):
        raise ValueError("reference segment set is empty or mixes case/receptor")
    # This deliberately exercises only the event energy-sum step using ECAC's
    # independently published per-segment corrected SEL values.
    result = energy_sum_db(float(r["segment_SEL(dB)"]) for r in segments)
    expected = float(c["expected_total_sel_db"])
    residual = result - expected
    return {
        "schema": "quiet_la_doc29_reference_aggregation_result_v1",
        "case_id": c["case_id"],
        "aircraft_id": c["aircraft_id"],
        "operation": c["operation"],
        "route_id": c["route_id"],
        "route_description": c["route_description"],
        "receptor_id": c["receptor_id"],
        "segment_count": len(segments),
        "metric": c["expected_metric"],
        "aggregation": "10 log10(sum(10^(segment SEL/10)))",
        "computed_from_published_segment_sel_db": result,
        "ecac_published_expected_total_sel_db": expected,
        "residual_db": residual,
        "within_rounding_tolerance_0_01_db": abs(residual) <= 0.01,
        "implementation_scope": "Energy aggregation only. Segment geometry, NPD interpolation, and corrections in the supplied segment rows are official workbook outputs, not computed by this code; this is not a full Doc 29 test or validation.",
        "source_workbook_sha256": data["source"]["workbook_sha256"],
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
    print(text, end="")


if __name__ == "__main__":
    main()
