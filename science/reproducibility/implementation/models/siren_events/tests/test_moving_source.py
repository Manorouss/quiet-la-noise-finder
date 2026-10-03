import math
import unittest

from implementation.models.siren_events.moving_source import (
    PropagationAssumptions,
    PropagationError,
    Receiver,
    ReferenceSpectrum,
    TrajectoryPoint,
    integrate_moving_source,
)


def assumptions(*, step=0.01, air=0.0, ground=0.0, shield=0.0):
    return PropagationAssumptions(
        air_absorption_db_per_km=(air,),
        reference_ground_excess_attenuation_db=(0.0,),
        target_ground_excess_attenuation_db=(ground,),
        reference_shielding_attenuation_db=(0.0,),
        target_shielding_attenuation_db=(shield,),
        max_step_s=step,
    )


class MovingSourceTests(unittest.TestCase):
    def test_constant_speed_free_field_passby_matches_analytic_integral(self):
        # For received reference SPL Lr at r=b, the free-field energy integral
        # is 2*b/v * atan(A/b) over x=-A..A.
        spectrum = ReferenceSpectrum((1000.0,), (80.0,), 10.0, 0.0, False, "test vector")
        result = integrate_moving_source(
            spectrum,
            (TrajectoryPoint(0, -100, 0, 1, 0), TrajectoryPoint(10, 100, 0, 1, 0)),
            Receiver(0, 10, 1),
            directivity_knots_db=((0, 0), (180, 0)),
            assumptions=assumptions(step=0.002),
        )
        expected_energy_seconds = 2.0 * 10.0 / 20.0 * math.atan(10.0)
        expected_sel = 80.0 + 10.0 * math.log10(expected_energy_seconds)
        self.assertAlmostEqual(result.band_exposures[0].received_sel_db_re_1s, expected_sel, places=3)
        self.assertIsNone(result.complete_a_weighted_sel_db_re_1s)
        self.assertIsNone(result.complete_a_weighted_laeq_db)

    def test_distance_doubling_gives_inverse_distance_level_change(self):
        spectrum = ReferenceSpectrum((1000.0,), (80.0,), 10.0, 0.0, True, "test vector")
        path = (TrajectoryPoint(0, 0, 0, 1, 0), TrajectoryPoint(1, 0, 0, 1, 0))
        near = integrate_moving_source(spectrum, path, Receiver(0, 10, 1), directivity_knots_db=((0, 0), (180, 0)), assumptions=assumptions())
        far = integrate_moving_source(spectrum, path, Receiver(0, 20, 1), directivity_knots_db=((0, 0), (180, 0)), assumptions=assumptions())
        self.assertAlmostEqual(near.band_exposures[0].received_sel_db_re_1s - far.band_exposures[0].received_sel_db_re_1s, 6.0206, places=2)

    def test_explicit_band_losses_are_applied(self):
        spectrum = ReferenceSpectrum((1000.0,), (80.0,), 10.0, 0.0, True, "test vector")
        path = (TrajectoryPoint(0, 0, 0, 1, 0), TrajectoryPoint(1, 0, 0, 1, 0))
        result = integrate_moving_source(spectrum, path, Receiver(0, 20, 1), directivity_knots_db=((0, 0), (180, 0)), assumptions=assumptions(step=0.01, air=1000, ground=2, shield=3))
        self.assertAlmostEqual(result.band_exposures[0].received_sel_db_re_1s, 80.0 - 6.0206 - 10.0 - 2.0 - 3.0, places=2)

    def test_nonzero_air_absorption_preserves_reference_level_at_reference_range(self):
        spectrum = ReferenceSpectrum((1000.0,), (80.0,), 10.0, 0.0, True, "test vector")
        path = (TrajectoryPoint(0, 0, 0, 1, 0), TrajectoryPoint(1, 0, 0, 1, 0))
        scenario = PropagationAssumptions(
            air_absorption_db_per_km=(120.0,),
            reference_ground_excess_attenuation_db=(1.5,),
            target_ground_excess_attenuation_db=(1.5,),
            reference_shielding_attenuation_db=(4.0,),
            target_shielding_attenuation_db=(4.0,),
            max_step_s=0.01,
        )
        result = integrate_moving_source(spectrum, path, Receiver(10, 0, 1), directivity_knots_db=((0, 0), (180, -20)), assumptions=scenario)
        self.assertAlmostEqual(result.band_exposures[0].received_sel_db_re_1s, 80.0)

    def test_refuses_unmeasured_angles_and_bad_track_values(self):
        spectrum = ReferenceSpectrum((1000.0,), (80.0,), 10.0, 0.0, False, "test vector")
        path = (TrajectoryPoint(0, -10, 0, 1, 0), TrajectoryPoint(1, 10, 0, 1, 0))
        with self.assertRaisesRegex(PropagationError, "unmeasured directivity"):
            integrate_moving_source(spectrum, path, Receiver(0, 10, 1), directivity_knots_db=((0, 0), (90, -20)), assumptions=assumptions())
        numeric_string_path = (TrajectoryPoint("0", 0, 0, 1, 0), TrajectoryPoint("1", 1, 0, 1, 0))
        result = integrate_moving_source(spectrum, numeric_string_path, Receiver(0, 10, 1), directivity_knots_db=((0, 0), (180, 0)), assumptions=assumptions())
        self.assertEqual(result.duration_s, 1.0)
        bad_path = (TrajectoryPoint(None, 0, 0, 1, 0), TrajectoryPoint(1, 1, 0, 1, 0))
        with self.assertRaises(ValueError):
            integrate_moving_source(spectrum, bad_path, Receiver(0, 10, 1), directivity_knots_db=((0, 0), (180, 0)), assumptions=assumptions())

    def test_detects_receiver_intersection_between_odd_number_of_samples(self):
        spectrum = ReferenceSpectrum((1000.0,), (80.0,), 10.0, 0.0, False, "test vector")
        # One second / 0.4 s gives three integration steps, so t=0.5 is not
        # sampled. Exact segment geometry must still detect the intersection.
        path = (TrajectoryPoint(0, -1, 0, 1, 0), TrajectoryPoint(1, 1, 0, 1, 0))
        with self.assertRaisesRegex(PropagationError, "segment intersects"):
            integrate_moving_source(spectrum, path, Receiver(0, 0, 1), directivity_knots_db=((0, 0), (180, 0)), assumptions=assumptions(step=0.4))

    def test_adaptive_integration_resolves_fast_close_passby_and_reports_exact_range(self):
        spectrum = ReferenceSpectrum((1000.0,), (80.0,), 10.0, 0.0, False, "analytic close-passby vector")
        # The closest approach occurs at t=4.37 s, away from the coarse 0.8 s
        # panel boundaries. The exact free-field energy integral is available
        # for this constant-speed straight passby.
        path = (TrajectoryPoint(0, -2500, 0, 1, 0), TrajectoryPoint(10, 2500, 0, 1, 0))
        receiver = Receiver(-315, 0.2, 1)  # -315 m is the source x at t=4.37 s
        coarse = integrate_moving_source(
            spectrum,
            path,
            receiver,
            directivity_knots_db=((0, 0), (180, 0)),
            assumptions=assumptions(step=0.8),
        )
        fine = integrate_moving_source(
            spectrum,
            path,
            receiver,
            directivity_knots_db=((0, 0), (180, 0)),
            assumptions=assumptions(step=0.07),
        )
        speed = 500.0
        half_length = 2185.0
        impact = 0.2
        energy_seconds = 10.0**2 * 2.0 / (speed * impact) * math.atan(half_length / impact)
        expected_sel = 80.0 + 10.0 * math.log10(energy_seconds)
        self.assertAlmostEqual(coarse.band_exposures[0].received_sel_db_re_1s, expected_sel, places=3)
        self.assertAlmostEqual(fine.band_exposures[0].received_sel_db_re_1s, expected_sel, places=3)
        self.assertAlmostEqual(coarse.min_range_m, impact, places=10)
        self.assertAlmostEqual(fine.min_range_m, impact, places=10)
        self.assertLess(abs(coarse.band_exposures[0].received_sel_db_re_1s - fine.band_exposures[0].received_sel_db_re_1s), 1e-4)
        self.assertLessEqual(coarse.maximum_accepted_local_relative_error_estimate, 1e-6)


if __name__ == "__main__":
    unittest.main()
