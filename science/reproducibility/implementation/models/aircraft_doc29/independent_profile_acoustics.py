"""Calculate JETFAS/R18 segment SEL from independently built A-sheet geometry.

This report computes path/observer geometry, CPA power/speed, NPD levels and
segment corrections from A-sheet profile/route/receptor inputs. B-2 values are
loaded only after those calculations, as independent comparison targets.
"""
from __future__ import annotations

import argparse
import json
from math import atan2, degrees, hypot, log10, sqrt
from pathlib import Path

from .doc29 import NPDTable, energy_sum_db, npd_level
from .profile_geometry import segments_from_fixed_profile
from .segment_reference import (
    FT_TO_M, KNOT_TO_MPS, _finite_segment_correction, _impedance_adjustment,
    _lateral_attenuation,
    _start_of_roll_jet, _start_of_roll_turboprop, _takeoff_groundroll_finite_correction,
)


def _aircraft_npd_table(curves: list[dict], metric: str, aircraft: dict, operation: str) -> NPDTable:
    if not curves:
        raise ValueError(f"reference case has no {operation} {metric} NPD curves")
    parameter = str(aircraft.get("Power Parameter", ""))
    if "Shaft_Horse_Power_(%)" in parameter:
        power_unit = "percent_per_engine"
    elif "Corrected_Net_Thrust" in parameter or "Net_Thrust" in parameter:
        power_unit = "lb_per_engine"
    else:
        raise ValueError(f"unsupported NPD power parameter for aircraft {aircraft.get('Aircraft Identifier')}: {parameter}")
    distance_columns = sorted(
        ((float(k[2:-4]), k) for k in curves[0] if k.startswith("L_") and k.endswith("(ft)")),
        key=lambda pair: pair[0],
    )
    powers = tuple(float(row["Power Setting (lb)"]) for row in curves)
    levels = tuple(tuple(float(row[key]) for _, key in distance_columns) for row in curves)
    return NPDTable(
        aircraft_id=str(aircraft["Aircraft Identifier"]).strip(),
        npd_id=str(aircraft["NPD Identifier"]).strip(), metric=metric,
        operation=operation, power_unit=power_unit, power_settings=powers,
        distances_m=tuple(ft * FT_TO_M for ft, _ in distance_columns), levels_db=levels,
    )


def _installation_correction(phi_deg: float, identifier: str) -> float:
    if identifier.strip().lower() in ("prop", "propeller"):
        return 0.0
    from math import cos, log10, radians, sin
    phi = radians(max(0.0, phi_deg))
    if identifier.strip().lower() in ("wing", "wing-mounted"):
        a, b, c = 0.0039, 0.062, 0.8786
    else:
        a, b, c = 0.1225, 0.329, 1.0
    return 10.0 * log10(((a*cos(phi)**2 + sin(phi)**2)**b)
                        / (c*sin(2*phi)**2 + cos(2*phi)**2))


def _segment_geometry(segment: dict, receptor: tuple[float, float, float], operation: str = "A") -> dict:
    a, b = segment["start"], segment["end"]
    s1, z1 = float(a["s_m"]), max(1.0, float(a["z_m"]))
    s2, z2 = float(b["s_m"]), max(1.0, float(b["z_m"]))
    dx, dy, dz = s2 - s1, 0.0, z2 - z1
    length = sqrt(dx * dx + dz * dz)
    ux, uz = dx / length, dz / length
    ox, oy, oz = receptor
    q = (ox - s1) * ux + (oz - z1) * uz
    cx, cz = s1 + q * ux, z1 + q * uz
    cross_track = abs(oy)
    # Keep infinite-path and finite-segment points separate. Doc 29 Vol. 2
    # §4.4.1 uses dp for airborne exposure, but ds for ground-roll exposure
    # when the observer is behind/ahead; maximum-level geometry also uses ds.
    infinite_slant = sqrt((ox - cx) ** 2 + oy**2 + (oz - cz) ** 2)
    runway_segment = float(a["z_m"]) <= 1.0 and float(b["z_m"]) <= 1.0
    if q < 0.0:
        nearest_x, nearest_z = s1, z1
    elif q > length:
        nearest_x, nearest_z = s2, z2
    else:
        nearest_x, nearest_z = cx, cz
    endpoint_slant = sqrt((ox - nearest_x) ** 2 + oy**2 + (oz - nearest_z) ** 2)
    use_nearest_endpoint_distance = runway_segment and (
        (operation == "D" and q < 0.0) or (operation == "A" and q > length)
    )
    slant = endpoint_slant if use_nearest_endpoint_distance else infinite_slant
    # For a runway ground-roll segment behind/ahead of the observer, lateral
    # displacement is the ground distance to its nearest endpoint. Airborne
    # exposure uses the cross-track displacement to the equivalent infinite
    # level path (Doc 29 §§4.4.1, 4.5.5).
    lateral = (sqrt((ox - nearest_x) ** 2 + oy**2) if use_nearest_endpoint_distance
               else cross_track)
    infinite_signed_height = cz - oz
    signed_height = (nearest_z if q < 0.0 or q > length else cz) - oz
    cos_gamma = abs(dx) / length
    if cos_gamma <= 0:
        raise ValueError("Doc 29 equivalent-level construction requires nonvertical track geometry")
    outside = q < 0.0 or q > length
    if use_nearest_endpoint_distance:
        # Ground-roll beta is taken at the nearest endpoint, using its slant
        # distance and horizontal distance to the observer (§4.5.5).
        beta_height, beta_lambda = signed_height, lateral
    elif outside:
        # Behind/ahead airborne beta uses the nearest endpoint projected to an
        # equivalent level path: h=(z_endpoint-z_receiver)/cos(gamma).
        beta_height, beta_lambda = signed_height / cos_gamma, cross_track
    else:
        # For an alongside exposure the perpendicular h from the ground track
        # to the inclined path is also transformed by cos(gamma).
        beta_height, beta_lambda = infinite_signed_height / cos_gamma, cross_track
    beta = 0.0 if beta_height <= 0 else degrees(atan2(beta_height, beta_lambda))
    # Depression angle uses the signed perpendicular height on the infinite
    # path, rotated to the equivalent level construction (§4.5.5).
    phi_height = infinite_signed_height / cos_gamma
    if cross_track == 0:
        phi = 90.0 if phi_height > 0 else 0.0
    else:
        phi = max(0.0, degrees(atan2(phi_height, cross_track)))
    bank = float(a.get("bank_deg", 0.0))
    phi = max(0.0, phi - bank)
    ratio = min(1.0, max(0.0, q / length))
    # Doc 29 profile interpolation uses squared-speed and squared-power
    # interpolation over the segment. At an exterior CPA, this clamps to the
    # corresponding endpoint.
    runway_segment = float(a["z_m"]) <= 1.0 and float(b["z_m"]) <= 1.0
    if runway_segment:
        # Doc 29 Vol. 2 §4.5.1 Eq. (4-13b): use mean endpoint speed on runway.
        speed = (float(a["speed_mps"]) + float(b["speed_mps"])) / 2.0
    else:
        speed = sqrt(max(0.0, float(a["speed_mps"]) ** 2 + ratio * (
            float(b["speed_mps"]) ** 2 - float(a["speed_mps"]) ** 2)))
    p1, p2 = float(a["power"]), float(b["power"])
    if p1 >= 0 and p2 >= 0:
        power = sqrt(max(0.0, p1 * p1 + ratio * (p2 * p2 - p1 * p1)))
    elif p1 <= 0 and p2 <= 0:
        power = -sqrt(max(0.0, p1 * p1 + ratio * (p2 * p2 - p1 * p1)))
    elif p1 < 0 < p2:
        power = p1 + (p2 - p1) * sqrt(ratio)
    else:
        power = p2 + (p1 - p2) * sqrt(1.0 - ratio)
    return {
        "q_m": q, "length_m": length, "perpendicular_distance_m": slant,
        "lateral_displacement_m": lateral, "cross_track_m": cross_track,
        "signed_cpa_height_m": signed_height,
        "beta_deg": beta, "beta_p_deg": phi, "bank_deg": bank, "phi_deg": phi,
        "power_lb_per_engine": power, "speed_mps": speed,
    }


def run(case_path: Path, *, minimum_distance_m: float = 30.0) -> dict:
    data = json.loads(case_path.read_text())
    case = data["official_test_case"]
    receptor_row = data["receptor"]
    def value(key: str) -> float:
        raw = receptor_row[key]
        return float(raw.replace(",", "") if isinstance(raw, str) else raw)

    receptor = (value("X coordinate (m)"), value("Y coordinate (m)"), value("Height (m)"))
    # A-sheet profile and reference curve inputs only for model construction.
    segments = segments_from_fixed_profile(data)
    aircraft_id = case["aircraft_id"]
    operation = case["operation"]
    sel_table = _aircraft_npd_table(data["npd_arrival_sel_curves"], "SEL", data["aircraft"], operation)
    max_table = _aircraft_npd_table(data["npd_arrival_lamax_curves"], "LAmax", data["aircraft"], operation)
    met = data["meteorology"]
    impedance = _impedance_adjustment(float(met["Temperature (degC)"]),
                                      float(met["Pressure (mmHg)"]))
    reference_speed = 160.0 * KNOT_TO_MPS
    calculated = []
    for segment in segments:
        g = _segment_geometry(segment, receptor, operation)
        # Doc 29 Vol. 2 §3.5 sets a 1 m minimum source height above runway;
        # §4.3's 30 m minimum is handled inside npd_level by default.
        dist = g["perpendicular_distance_m"]
        power = g["power_lb_per_engine"]
        sel = npd_level(sel_table, aircraft_id=aircraft_id, npd_id=sel_table.npd_id, metric="SEL",
                        operation=operation, power=power, power_unit=sel_table.power_unit,
                        distance_m=dist, allow_power_extrapolation=True,
                        minimum_distance_m=minimum_distance_m)
        lamax = npd_level(max_table, aircraft_id=aircraft_id, npd_id=max_table.npd_id, metric="LAmax",
                          operation=operation, power=power, power_unit=max_table.power_unit,
                          distance_m=dist, allow_power_extrapolation=True,
                          minimum_distance_m=minimum_distance_m)
        duration = 10.0 * log10(reference_speed / g["speed_mps"])
        installation_id = str(data["aircraft"].get("Lateral Directivity Identifier", "Fuselage"))
        installation = _installation_correction(g["phi_deg"], installation_id)
        lateral = _lateral_attenuation(g["beta_deg"], g["lateral_displacement_m"])
        runway_segment = segment["start"]["z_m"] <= 1.0 and segment["end"]["z_m"] <= 1.0
        q_ft = g["q_m"] / FT_TO_M
        length_ft = g["length_m"] / FT_TO_M
        if runway_segment and operation == "D" and q_ft <= 0:
            finite = _takeoff_groundroll_finite_correction(sel, lamax, length_ft)
            finite_method = "Doc29_4-21a_takeoff_groundroll_q_zero_reference_point"
        else:
            finite = _finite_segment_correction(sel, lamax, q_ft, length_ft)
            finite_method = "Doc29_4-20_generic_finite_segment"
        sor = 0.0
        sor_distance_m = None
        sor_azimuth_deg = None
        if runway_segment and operation == "D" and q_ft < 0:
            engine_type = str(data["aircraft"].get("Engine Type", ""))
            if engine_type == "Jet":
                sor, sor_distance_m, sor_azimuth_deg = _start_of_roll_jet(q_ft, g["cross_track_m"] / FT_TO_M)
            elif engine_type == "Turboprop":
                sor, sor_distance_m, sor_azimuth_deg = _start_of_roll_turboprop(q_ft, g["cross_track_m"] / FT_TO_M)
        level = sel + impedance + duration + installation - lateral + finite + sor
        calculated.append({
            "segment_id": segment["segment_id"], **g,
            "geometric_dp_m": g["perpendicular_distance_m"],
            "npd_distance_used_m": max(dist, minimum_distance_m),
            "npd_minimum_distance_applied": minimum_distance_m > dist,
            "npd_sel_db": sel, "npd_lamax_db": lamax,
            "duration_correction_db": duration,
            "installation_correction_db": installation,
            "lateral_attenuation_db": lateral,
            "impedance_adjustment_db": impedance,
            "finite_segment_correction_db": finite,
            "finite_segment_method": finite_method,
            "start_of_roll_correction_db": sor,
            "start_of_roll_distance_m": sor_distance_m,
            "start_of_roll_azimuth_deg": sor_azimuth_deg,
            "segment_sel_db": level,
        })
    # B-2 values appear only here, downstream of the independently calculated
    # quantities, and are comparison targets rather than model inputs.
    references = data["published_segment_results"]
    for actual, reference in zip(calculated, references):
        actual["reference_segment_sel_db"] = float(reference["segment_SEL(dB)"])
        actual["segment_sel_residual_db"] = actual["segment_sel_db"] - actual["reference_segment_sel_db"]
        actual["npd_sel_residual_db"] = actual["npd_sel_db"] - float(reference["baseline_SEL(dB)"])
        actual["npd_lamax_residual_db"] = actual["npd_lamax_db"] - float(reference["Lmax_noise_fraction"])
        actual["duration_correction_residual_db"] = actual["duration_correction_db"] - float(reference["speed_corr(dB)"])
        actual["installation_correction_residual_db"] = actual["installation_correction_db"] - float(reference["engine_Install_correction(dB)"])
        actual["lateral_attenuation_residual_db"] = actual["lateral_attenuation_db"] - float(reference["lateral_attenuation(dB)"])
        actual["finite_segment_correction_residual_db"] = actual["finite_segment_correction_db"] - float(reference["noise_fraction"])
        actual["start_of_roll_correction_residual_db"] = actual["start_of_roll_correction_db"] - float(reference["start_of_roll_correction(dB)"])
        actual["impedance_adjustment_residual_db"] = actual["impedance_adjustment_db"] - float(reference["acoustic_impedance_adjustment(dB)"])
        actual["reference_q_m"] = float(reference["distance_q(ft)"]) * FT_TO_M
        actual["q_residual_m"] = actual["q_m"] - actual["reference_q_m"]
        actual["reference_geometric_dp_m"] = float(reference["NPD_interpolation_distance(ft)"]) * FT_TO_M
        actual["geometric_dp_residual_m"] = actual["geometric_dp_m"] - actual["reference_geometric_dp_m"]
    total = energy_sum_db(r["segment_sel_db"] for r in calculated)
    return {
        "schema": "quiet_la_doc29_a_sheet_geometry_acoustic_case_v1",
        "case_id": case["case_id"], "aircraft_id": case["aircraft_id"],
        "operation": case["operation"], "receptor_id": receptor_row["receptor_ID"],
        "metric": "SEL", "segment_count": len(calculated),
        "npd_minimum_distance_policy_m": minimum_distance_m,
        "computed_movement_sel_db": total,
        "reference_movement_sel_db": float(case["expected_total_sel_db"]),
        "movement_sel_residual_db": total - float(case["expected_total_sel_db"]),
        "all_segment_sel_residuals_within_0_001_db": all(abs(r["segment_sel_residual_db"]) <= 0.001 for r in calculated),
        "mean_q_residual_m": sum(r["q_residual_m"] for r in calculated) / len(calculated),
        "max_abs_q_residual_m": max(abs(r["q_residual_m"]) for r in calculated),
        "max_abs_geometric_dp_residual_m": max(abs(r["geometric_dp_residual_m"]) for r in calculated),
        "source_workbook_sha256": data["source"]["workbook_sha256"],
        "npd_power_unit": sel_table.power_unit,
        "scope": "Aircraft segment geometry, q, CPA distance, CPA speed/power, and corrections are calculated from A-sheet fixed-profile/route/runway/receptor data and Doc 29 equations. Published B-2 is used only after calculation for residuals. This is a reconstructed fixed-profile reference calculation; it does not synthesize profiles from ANP procedures, turns, dispersion, terrain blockage, or airport traffic.",
        "segments": calculated,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", type=Path, default=Path(__file__).parent / "reference" / "jetfas_reference_case.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--minimum-distance-m", type=float, default=30.0,
                        help="explicit NPD distance floor; use 30 m for Doc 29 §4.3 default")
    args = parser.parse_args()
    result = run(args.case, minimum_distance_m=args.minimum_distance_m)
    text = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(json.dumps({k: result[k] for k in (
        "segment_count", "computed_movement_sel_db", "movement_sel_residual_db",
        "all_segment_sel_residuals_within_0_001_db", "mean_q_residual_m",
        "max_abs_q_residual_m", "max_abs_geometric_dp_residual_m",
    )}, indent=2))


if __name__ == "__main__":
    main()
