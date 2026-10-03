"""Reconstruct the first published JETFAS/R18 airborne segment.

This reconstructs the NPD baseline and corrections for one airborne segment
and compares the result with ECAC's independent reference workbook.
"""
from __future__ import annotations

import argparse
import json
from math import acos, atan, cos, degrees, exp, expm1, fsum, isfinite, log, log10, pi, radians, sin, sqrt
from pathlib import Path

from .doc29 import NPDTable, airborne_segment_sel, npd_level

FT_TO_M = 0.3048
KNOT_TO_MPS = 0.5144444444444445
P0_MMHG = 760.0


def _table(curves: list[dict], metric: str, aircraft_id: str = "JETF", operation: str = "A") -> NPDTable:
    if not curves:
        raise ValueError("reference case has no arrival SEL NPD curves")
    columns = sorted(
        ((float(k[2:-4]), k) for k in curves[0] if k.startswith("L_") and k.endswith("(ft)")),
        key=lambda pair: pair[0],
    )
    powers = tuple(float(row["Power Setting (lb)"]) for row in curves)
    levels = tuple(tuple(float(row[key]) for _, key in columns) for row in curves)
    return NPDTable(
        aircraft_id=aircraft_id, npd_id=aircraft_id, metric=metric, operation=operation,
        power_unit="lb_per_engine", power_settings=powers,
        distances_m=tuple(ft * FT_TO_M for ft, _ in columns), levels_db=levels,
    )


def _installation_fuselage(phi_deg: float) -> float:
    # Doc 29 5e Eq. (4-15), fuselage-mounted jet coefficients.
    if phi_deg < 0:
        phi_deg = 0.0  # §4.5.3 recommendation for negative depression angles.
    phi = radians(phi_deg)
    numerator = (0.1225 * cos(phi) ** 2 + sin(phi) ** 2) ** 0.329
    denominator = sin(2.0 * phi) ** 2 + cos(2.0 * phi) ** 2
    return 10.0 * log10(numerator / denominator)


def _lateral_attenuation(beta_deg: float, lateral_m: float) -> float:
    # Doc 29 5e Eqs. (4-18)/(4-19). Beta outside the stated domain is rejected.
    if not (0 <= beta_deg <= 90) or lateral_m < 0:
        raise ValueError("lateral attenuation requires beta in [0,90] and nonnegative lateral distance")
    if beta_deg >= 50:
        return 0.0
    gamma = 1.089 * (1.0 - exp(-0.00274 * lateral_m)) if lateral_m <= 914.0 else 1.0
    return gamma * (1.137 - 0.0229 * beta_deg + 9.72 * exp(-0.142 * beta_deg))


def _finite_segment_correction(sel_infinite_db: float, lamax_infinite_db: float,
                               q: float, segment_length: float) -> float:
    """Doc 29 5e Eq. (4-20); supports ahead/alongside/behind geometry."""
    if any(not isfinite(v) for v in (sel_infinite_db, lamax_infinite_db, q, segment_length)):
        raise ValueError("finite-segment inputs must be finite")
    if segment_length <= 0:
        raise ValueError("finite-segment length must be positive")
    reference_speed = 270.05  # Doc 29 §4.5.6, ft/s.
    scaled_distance = (2.0 / pi) * reference_speed * 10.0 ** ((sel_infinite_db - lamax_infinite_db) / 10.0)
    alpha1 = -q / scaled_distance
    alpha2 = -(q - segment_length) / scaled_distance
    if alpha2 <= alpha1:
        raise ValueError("finite-segment geometry must preserve the ordered endpoints")
    if alpha1 > 10.0:
        delta = _tail_integral_difference(alpha1, alpha2)
    elif alpha2 < -10.0:
        delta = _tail_integral_difference(-alpha2, -alpha1)
    else:
        delta = (_primitive(alpha2) - _primitive(alpha1))
    fraction = delta / pi
    if fraction <= 0 or not isfinite(fraction):
        raise ValueError("finite-segment energy fraction must be positive and finite")
    return max(-150.0, 10.0 * log10(fraction))


def _takeoff_groundroll_finite_correction(sel_infinite_db: float, lamax_infinite_db: float,
                                          segment_length_ft: float) -> float:
    """Doc 29 Vol. 2 §4.5.6 Eq. (4-21a), q=0 reference-point fraction."""
    reference_speed = 270.05  # ft/s
    d_lambda = (2.0 / pi) * reference_speed * 10.0 ** ((sel_infinite_db - lamax_infinite_db) / 10.0)
    alpha = segment_length_ft / d_lambda
    fraction = (alpha / (1.0 + alpha * alpha) + atan(alpha)) / pi
    if fraction <= 0 or not isfinite(fraction):
        raise ValueError("takeoff ground-roll energy fraction must be positive and finite")
    return 10.0 * log10(fraction)


def _start_of_roll_jet(q_ft: float, lateral_ft: float) -> tuple[float, float, float]:
    """Doc 29 Vol. 2 §4.5.7 Eq. (4-22), (4-24a), (4-25) for turbofan jets.

    q_ft is negative for a receiver behind this ground-roll segment's start.
    Returns ΔSOR, dSOR (m), and ψ (degrees). The formula is undefined outside
    90° ≤ ψ < 180° and is rejected there rather than extrapolated.
    """
    from math import acos, exp, log, pi
    d_sor_ft = sqrt(q_ft * q_ft + lateral_ft * lateral_ft)
    if d_sor_ft <= 0 or q_ft >= 0:
        return 0.0, d_sor_ft * FT_TO_M, 90.0
    psi = degrees(acos(max(-1.0, min(1.0, q_ft / d_sor_ft))))
    if not (90.0 <= psi < 180.0):
        raise ValueError("jet ΔSOR requires 90° ≤ ψ < 180°")
    log_term = log(pi * psi / 180.0)
    delta0 = (2329.44 - 8.0573 * psi
              + 11.51 * exp(pi * psi / 180.0)
              - (3.4601 * psi / log_term)
              - (17403338.3 * log_term / (psi * psi)))
    delta = delta0 * min(1.0, 2500.0 / d_sor_ft)
    return delta, d_sor_ft * FT_TO_M, psi


def _start_of_roll_turboprop(q_ft: float, lateral_ft: float) -> tuple[float, float, float]:
    """Doc 29 Vol. 2 §4.5.7 Eqs. (4-22), (4-24b), (4-25)."""
    from math import acos
    d_sor_ft = sqrt(q_ft * q_ft + lateral_ft * lateral_ft)
    if d_sor_ft <= 0 or q_ft >= 0:
        return 0.0, d_sor_ft * FT_TO_M, 90.0
    psi = degrees(acos(max(-1.0, min(1.0, q_ft / d_sor_ft))))
    if not (90.0 <= psi < 180.0):
        raise ValueError("turboprop ΔSOR requires 90° ≤ ψ < 180°")
    delta0 = (-34643.898 + 30722162.0 / psi - 11491573931.0 / psi**2
              + 2.34928567e12 / psi**3 - 2.83584442e14 / psi**4
              + 2.02271504e16 / psi**5 - 7.90084471e17 / psi**6
              + 1.30506872e19 / psi**7)
    delta = delta0 * min(1.0, 2500.0 / d_sor_ft)
    return delta, d_sor_ft * FT_TO_M, psi


def _primitive(value: float) -> float:
    return value / (1.0 + value * value) + atan(value)


def _tail_integral_difference(low: float, high: float) -> float:
    """Stable T(low)-T(high) for T(x)=integral_x^inf 2/(1+t²)^2 dt."""
    if not (10.0 < low <= high):
        raise ValueError("tail bounds must satisfy 10 < low <= high")
    # T(x) = sum_{n>=1} (-1)^(n+1) 2n/(2n+1) x^(-(2n+1)).
    terms = []
    for n in range(1, 13):
        power = 2 * n + 1
        coefficient = (1.0 if n % 2 else -1.0) * (2.0 * n / power)
        difference = low ** (-power) * (-expm1(-power * log(high / low)))
        terms.append(coefficient * difference)
    return fsum(terms)


def _impedance_adjustment(temp_c: float, pressure_mmhg: float) -> float:
    if not isfinite(temp_c) or pressure_mmhg <= 0 or not isfinite(pressure_mmhg):
        raise ValueError("finite temperature and positive pressure are required")
    theta = (temp_c + 273.15) / (15.0 + 273.15)
    delta = pressure_mmhg / P0_MMHG
    impedance = 416.86 * delta / theta**0.5
    return 10.0 * log10(impedance / 409.81)


def run(case_path: Path) -> dict:
    data = json.loads(case_path.read_text())
    ref = data["official_test_case"]
    published = data["published_segment_results"][0]
    profile = data["fixed_point_profile"]
    table = _table(data["npd_arrival_sel_curves"], "SEL")
    lamax_table = _table(data["npd_arrival_lamax_curves"], "LAmax")
    npd_distance_m = float(published["NPD_interpolation_distance(ft)"]) * FT_TO_M
    power = float(published["NPD_interpolation_thrust(lb/e)"])
    baseline = npd_level(
        table, aircraft_id="JETF", npd_id="JETF", metric="SEL",
        operation="A", power=power, power_unit="lb_per_engine",
        distance_m=npd_distance_m, allow_power_extrapolation=True,
    )
    # The nearest endpoint corresponds to fixed profile point 1.
    speed_mps = float(profile[0]["True Airspeed (m/s)"])
    reference_speed_mps = 160.0 * KNOT_TO_MPS  # Doc 29 §4.5.6 reference speed.
    duration = 10.0 * log10(reference_speed_mps / speed_mps)
    installation = _installation_fuselage(float(published["angle_phi(°)"]))
    lateral = _lateral_attenuation(float(published["angle_beta(°)"]),
                                   float(published["lateral_displacement(ft)"]) * FT_TO_M)
    met = data["meteorology"]
    impedance = _impedance_adjustment(float(met["Temperature (degC)"]),
                                      float(met["Pressure (mmHg)"]))
    lamax = npd_level(
        lamax_table, aircraft_id="JETF", npd_id="JETF", metric="LAmax",
        operation="A", power=power, power_unit="lb_per_engine",
        distance_m=npd_distance_m, allow_power_extrapolation=True,
    )
    finite_segment = _finite_segment_correction(
        baseline, lamax, float(published["distance_q(ft)"]),
        float(published["segment_length(ft)"]),
    )
    computed = airborne_segment_sel(
        baseline_sel_db=baseline,
        impedance_adjustment_db=impedance,
        duration_correction_db=duration,
        installation_correction_db=installation,
        lateral_attenuation_db=lateral,
        finite_segment_correction_db=finite_segment,
        line_of_sight_blockage_db=0.0,
    )
    expected = float(published["segment_SEL(dB)"])
    checks = {
        "npd_baseline_matches_published_0_001_db": abs(baseline - float(published["baseline_SEL(dB)"])) <= 0.001,
        "npd_lamax_matches_published_0_001_db": abs(lamax - float(published["Lmax_noise_fraction"])) <= 0.001,
        "duration_matches_published_0_001_db": abs(duration - float(published["speed_corr(dB)"])) <= 0.001,
        "installation_matches_published_0_001_db": abs(installation - float(published["engine_Install_correction(dB)"])) <= 0.001,
        "lateral_matches_published_0_001_db": abs(lateral - float(published["lateral_attenuation(dB)"])) <= 0.001,
        "impedance_matches_published_0_001_db": abs(impedance - float(published["acoustic_impedance_adjustment(dB)"])) <= 0.001,
        "finite_segment_matches_published_0_001_db": abs(finite_segment - float(published["noise_fraction"])) <= 0.001,
        "segment_sel_matches_published_0_001_db": abs(computed - expected) <= 0.001,
    }
    return {
        "schema": "quiet_la_doc29_partial_segment_result_v1",
        "case_id": ref["case_id"], "aircraft_id": ref["aircraft_id"],
        "operation": ref["operation"], "receptor_id": ref["receptor_id"],
        "segment_id": 1, "metric": "A-weighted SEL",
        "terms_db": {
            "npd_baseline_computed": baseline,
            "npd_lamax_computed_for_finite_segment": lamax,
            "acoustic_impedance_computed": impedance,
            "duration_computed_from_nearest_endpoint_and_160kt_reference": duration,
            "fuselage_installation_computed_from_phi": installation,
            "lateral_attenuation_computed_from_beta_and_offset": lateral,
            "finite_segment_correction_computed_from_eq_4_20": finite_segment,
            "line_of_sight_blockage_assumed_zero_for_flat_reference_case": 0.0,
        },
        "computed_segment_sel_db": computed,
        "ecac_published_segment_sel_db": expected,
        "residual_db": computed - expected,
        "checks": checks,
        "all_checks_pass": all(checks.values()),
        "scope": "Independent reconstruction of one airborne segment's NPD levels and corrections, checked against independent ECAC B-2 values; not a complete straight-flight/event verification.",
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
