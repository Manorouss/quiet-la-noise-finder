"""Analytic checks for the proposed exact-zero dp guard; no engine is run."""
import math
import unittest


def formula(dp, zs, zr, k, w, gm, patched):
    amin = -3.0 * (1.0 - gm)
    cf = dp * (1.0 + 3.0 * w * dp * math.exp(-math.sqrt(w * dp))) / (1.0 + w * dp)
    if patched and dp == 0.0:
        return amin
    # Use IEEE arithmetic (the math module raises on division by zero).
    ratio = math.inf if dp == 0.0 else 4.0 * k * k / (dp * dp)
    source = zs * zs - math.sqrt(2.0 * cf / k) * zs + cf / k
    receiver = zr * zr - math.sqrt(2.0 * cf / k) * zr + cf / k
    q = ratio * source * receiver
    with_nan = q
    if math.isnan(with_nan) or with_nan < 0.0:
        raw = math.nan
    elif math.isinf(with_nan):
        raw = -math.inf
    elif with_nan == 0.0:
        raw = math.inf
    else:
        raw = -10.0 * math.log10(with_nan)
    return max(raw, amin)


class AgroundHZeroPlanDistanceTests(unittest.TestCase):
    def test_zero_dp_source_above_ground_receiver_on_ground_returns_clamp(self):
        k, w, gm = 2.0 * math.pi * 2000 / 340.0, 1e-4, 0.5
        self.assertTrue(math.isnan(formula(0.0, 0.05, 0.0, k, w, gm, False)))
        self.assertEqual(formula(0.0, 0.05, 0.0, k, w, gm, True), -1.5)

    def test_dp_to_zero_positive_limit_for_zero_receiver_height(self):
        k, w, gm = 2.0 * math.pi * 2000 / 340.0, 1e-4, 0.5
        values = [formula(dp, 0.05, 0.0, k, w, gm, False) for dp in (1e-3, 1e-6, 1e-9)]
        self.assertEqual(values, [-1.5, -1.5, -1.5])

    def test_both_heights_zero_limit_is_still_clamped(self):
        k, w, gm = 2.0 * math.pi * 2000 / 340.0, 1e-4, 0.5
        dp = 1e-9
        cf = dp * (1.0 + 3.0 * w * dp * math.exp(-math.sqrt(w * dp))) / (1.0 + w * dp)
        q = 4.0 * k * k / (dp * dp) * (cf / k) * (cf / k)
        self.assertAlmostEqual(-10.0 * math.log10(q), -10.0 * math.log10(4.0), places=7)
        self.assertEqual(formula(dp, 0.0, 0.0, k, w, gm, False), -1.5)
        self.assertEqual(formula(0.0, 0.0, 0.0, k, w, gm, True), -1.5)

    def test_positive_dp_regression_is_unchanged_by_guard(self):
        # 2 kHz, G=0.5, dp=100 m, zs=zr=1 m; this lies above the clamp.
        args = (100.0, 1.0, 1.0, 2.0 * math.pi * 2000 / 340.0,
                0.40959290036737456, 0.5)
        before = formula(*args, patched=False)
        after = formula(*args, patched=True)
        self.assertEqual(before, after)
        self.assertAlmostEqual(after, 5.929104673348816, places=12)


if __name__ == '__main__':
    unittest.main()
