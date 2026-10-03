#!/usr/bin/env python3
"""Run the source-only regression subset without private reference datasets."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AIRCRAFT_TESTS = ROOT / "implementation/models/aircraft_doc29/tests"
sys.path.insert(0, str(AIRCRAFT_TESTS))

import test_adsblol_ingest
import test_doc29
import test_vny_event_windows
import test_vny_operation_inference

CASES = (
    (test_doc29, test_doc29.EnergyMetricTests, (
        "test_stable_energy_sum",
        "test_laeq_divides_by_explicit_actual_window",
        "test_empty_and_mixed_metric_inputs_are_rejected",
        "test_cnel_applies_regulatory_energy_weights_in_la_timezone",
        "test_dst_local_date_uses_actual_elapsed_duration_for_laeq",
    )),
    (test_doc29, test_doc29.OfficialReferenceCaseTests, (
        "test_doc29_height_split_precedes_speed_split_and_keeps_short_nodes",
        "test_doc29_generalized_speed_breaks_use_integer_n_and_equal_time",
        "test_inclined_path_equivalent_level_angles_behind_alongside_ahead",
        "test_far_tail_finite_segment_correction_is_finite_and_clamped",
    )),
    (test_vny_event_windows, test_vny_event_windows.VNYEventWindowTests, (
        "test_touch_and_go_has_two_temporally_witnessed_events",
        "test_distant_surface_contact_does_not_match_old_approach",
        "test_departure_rejects_incompatible_height_datum",
    )),
    (test_vny_operation_inference, test_vny_operation_inference.VNYInferredOperationTests, (
        "test_arrival_candidate_requires_aligned_inbound_then_ground_state",
        "test_departure_rejects_mixed_or_missing_geometric_height",
        "test_no_operation_from_proximity_without_aligned_track",
    )),
    (test_adsblol_ingest, test_adsblol_ingest.ReadsbAltitudeSemanticsTests, (
        "test_published_shape_flags_zero_keeps_baro_and_independent_geometric",
        "test_flag_8_marks_primary_altitude_geometric_without_requiring_raw10",
        "test_ground_and_null_altitude_preserve_independent_channel",
    )),
)

suite = unittest.TestSuite(
    unittest.defaultTestLoader.loadTestsFromName(f"{case.__name__}.{name}", module)
    for module, case, names in CASES
    for name in names
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
