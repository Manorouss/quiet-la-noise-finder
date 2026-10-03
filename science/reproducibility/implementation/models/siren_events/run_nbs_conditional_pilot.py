"""Reproduce a conditional NBS-spectrum approach scenario; not an LA forecast."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from implementation.models.siren_events.moving_source import (
    PropagationAssumptions,
    Receiver,
    ReferenceSpectrum,
    TrajectoryPoint,
    integrate_moving_source,
)


ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "implementation/work/autonomous_delivery_2026_10_02/siren_model/sources/nbs_sp480_28_figure23_digitization.json"
OUTPUT = ROOT / "implementation/work/autonomous_delivery_2026_10_02/siren_model/pilots/nbs_sp480_28_conditional_approach.json"


def main() -> None:
    document = json.loads(DATA.read_text())
    bands = tuple(float(value) for value in document["band_center_hz"])
    spectrum = ReferenceSpectrum(
        band_center_hz=bands,
        level_db=tuple(float(value) for value in document["profiles"]["yelp"]),
        reference_distance_m=float(document["geometry"]["source_to_mic_distance_m"]),
        reference_azimuth_deg=0.0,
        spectrum_complete=False,
        source_label="NBS SP 480-28 Figure 23 approximate vehicle-mounted yelp digitization",
    )
    directivity = tuple(zip(
        (float(value) for value in document["directivity_data_separate"]["measured_angles_abs_deg"]),
        (float(value) for value in document["directivity_data_separate"]["relative_gain_db"]),
    ))
    assumptions = PropagationAssumptions(
        air_absorption_db_per_km=tuple(0.0 for _ in bands),
        reference_ground_excess_attenuation_db=tuple(0.0 for _ in bands),
        target_ground_excess_attenuation_db=tuple(0.0 for _ in bands),
        reference_shielding_attenuation_db=tuple(0.0 for _ in bands),
        target_shielding_attenuation_db=tuple(0.0 for _ in bands),
        max_step_s=0.01,
    )
    trajectory = (
        TrajectoryPoint(0.0, -88.0, 0.0, 1.2, 0.0),
        TrajectoryPoint(8.8, 0.0, 0.0, 1.2, 0.0),
    )
    receiver = Receiver(0.0, 12.2, 1.2)
    result = integrate_moving_source(
        spectrum,
        trajectory,
        receiver,
        directivity_knots_db=directivity,
        assumptions=assumptions,
    )
    payload = {
        "schema": "conditional_moving_siren_scenario_v1",
        "scenario_id": "nbs_sp480_28_yelp_approach_external_analogue",
        "interpretation": "Conditional method demonstration using an external NBS vehicle-mounted siren reference, not a Los Angeles siren measurement, route, activity rate, or forecast.",
        "source_data": {
            "path": str(DATA.relative_to(ROOT)),
            "sha256": hashlib.sha256(DATA.read_bytes()).hexdigest(),
            "source_report_sha256": document["pdf_sha256"],
            "source_profile": "yelp",
            "digitization_uncertainty_db": 2.0,
            "unplotted_bands": "missing; total A-weighted level remains unavailable",
        },
        "inputs": {
            "reference_spectrum": asdict(spectrum),
            "separate_directivity_transfer": {
                "knots_abs_angle_deg_relative_to_forward": [list(pair) for pair in directivity],
                "caveat": document["directivity_data_separate"]["limits"],
            },
            "trajectory_points": [asdict(point) for point in trajectory],
            "trajectory_interpretation": "Synthetic straight approach at 10 m/s from 88 m upstream to closest approach; this is an explicit scenario only, not an observed FS39 path.",
            "receiver": asdict(receiver),
            "propagation_assumptions": asdict(assumptions),
            "air_ground_shielding_zero": "Direct-path free-field limiting case; zero values are scenario choices, not measured site conditions.",
            "omissions": ["Doppler", "retarded-time effects", "terrain/building diffraction", "reflections", "meteorology", "ambient sound", "vehicle body/source placement changes", "activity rate"],
        },
        "result": asdict(result),
        "qualification_gates": {
            "local_source_spectrum": "not established",
            "local_route_or_speed": "not established",
            "local_activation_rate": "not established",
            "full_spectrum_total_laeq": "unavailable because plotted bands are partial",
            "use": "reproducible conditional propagation demonstration only",
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(OUTPUT)
    print(json.dumps(payload["result"], indent=2))


if __name__ == "__main__":
    main()
