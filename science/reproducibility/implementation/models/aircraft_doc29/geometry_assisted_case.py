"""Calculate all JETFAS/R18 segment corrections using published B-2 geometry.

This is a component check against ECAC's per-segment geometry table. It is not
an independent flight path/segment construction test because B-2 supplies the
NPD interpolation geometry and speed corrections.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .doc29 import energy_sum_db, npd_level
from .segment_reference import (
    FT_TO_M, _finite_segment_correction, _impedance_adjustment,
    _installation_fuselage, _lateral_attenuation, _table,
)


def run(case_path: Path) -> dict:
    data = json.loads(case_path.read_text())
    c = data["official_test_case"]
    sel_table = _table(data["npd_arrival_sel_curves"], "SEL")
    max_table = _table(data["npd_arrival_lamax_curves"], "LAmax")
    met = data["meteorology"]
    impedance = _impedance_adjustment(float(met["Temperature (degC)"]),
                                      float(met["Pressure (mmHg)"]))
    calculated = []
    for row in data["published_segment_results"]:
        dist_m = float(row["NPD_interpolation_distance(ft)"]) * FT_TO_M
        power = float(row["NPD_interpolation_thrust(lb/e)"])
        sel = npd_level(sel_table, aircraft_id="JETF", npd_id="JETF", metric="SEL",
                        operation="A", power=power, power_unit="lb_per_engine",
                        distance_m=dist_m, allow_power_extrapolation=True)
        lamax = npd_level(max_table, aircraft_id="JETF", npd_id="JETF", metric="LAmax",
                          operation="A", power=power, power_unit="lb_per_engine",
                          distance_m=dist_m, allow_power_extrapolation=True)
        delta_f = _finite_segment_correction(
            sel, lamax, float(row["distance_q(ft)"]),
            float(row["segment_length(ft)"]),
        )
        install = _installation_fuselage(float(row["angle_phi(°)"]))
        lateral = _lateral_attenuation(float(row["angle_beta(°)"]),
                                       float(row["lateral_displacement(ft)"]) * FT_TO_M)
        # The workbook supplies speed correction; this component report checks
        # the remaining terms from NPD tables, geometry, and ambient inputs.
        speed = float(row["speed_corr(dB)"])
        losb = 0.0  # The published flat reference case does not apply terrain blockage.
        segment = sel + impedance + speed + install - lateral + delta_f + losb
        published = float(row["segment_SEL(dB)"])
        calculated.append({
            "segment_id": int(row["segment_ID"]),
            "computed_npd_sel_db": sel,
            "computed_npd_lamax_db": lamax,
            "computed_finite_segment_db": delta_f,
            "computed_installation_db": install,
            "computed_lateral_attenuation_db": lateral,
            "imported_reference_speed_correction_db": speed,
            "computed_impedance_db": impedance,
            "computed_segment_sel_db": segment,
            "published_segment_sel_db": published,
            "residual_db": segment - published,
        })
    total = energy_sum_db(r["computed_segment_sel_db"] for r in calculated)
    expected = float(c["expected_total_sel_db"])
    return {
        "schema": "quiet_la_doc29_geometry_assisted_component_case_v1",
        "case_id": c["case_id"], "aircraft_id": c["aircraft_id"],
        "operation": c["operation"], "receptor_id": c["receptor_id"],
        "metric": c["expected_metric"], "segment_count": len(calculated),
        "computed_movement_sel_db": total,
        "ecac_published_expected_movement_sel_db": expected,
        "movement_residual_db": total - expected,
        "all_segment_residuals_within_0_001_db": all(abs(r["residual_db"]) <= 0.001 for r in calculated),
        "segments": calculated,
        "scope": "B-2 supplies segment geometry, NPD distance/power interpolation inputs, and speed corrections. The code independently recomputes both NPD metrics, finite-segment, installation, and lateral corrections from those published inputs and aggregates corrected segments. This is a geometry-assisted component verification, not an independently constructed trajectory or a full Doc 29 reference-case pass.",
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
