"""Conditional, direct-path moving-source propagation for siren scenarios.

The input spectrum is a received 1/3-octave SPL at an explicit reference
distance and heading, not an identified siren sound-power level. The model
transfers that measured reference by inverse distance, caller-supplied angular
directivity, and explicit air/ground/shielding losses. It is a quasi-static
energy model: Doppler, refraction, reflections, diffraction, and ambient sound
are outside its scope.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from .event_exposure import ExposureError, _finite, combine_sel_db


class PropagationError(ValueError):
    """Raised when a conditional scenario is physically underspecified."""


@dataclass(frozen=True)
class TrajectoryPoint:
    time_s: float
    x_m: float
    y_m: float
    z_m: float
    heading_deg: float


@dataclass(frozen=True)
class Receiver:
    x_m: float
    y_m: float
    z_m: float


@dataclass(frozen=True)
class ReferenceSpectrum:
    """Received unweighted band SPLs at a stated range and source-relative azimuth."""

    band_center_hz: tuple[float, ...]
    level_db: tuple[float, ...]
    reference_distance_m: float
    reference_azimuth_deg: float
    spectrum_complete: bool
    source_label: str


@dataclass(frozen=True)
class PropagationAssumptions:
    """Per-band transfer from the received reference path to a target path.

    Air absorption is applied as a distance difference from the reference
    measurement. Ground and shielding adjustments are target-path excess
    losses minus corresponding excess losses already present in the reference
    measurement. Unknown reference-path losses must not be guessed.
    """

    air_absorption_db_per_km: tuple[float, ...]
    reference_ground_excess_attenuation_db: tuple[float, ...]
    target_ground_excess_attenuation_db: tuple[float, ...]
    reference_shielding_attenuation_db: tuple[float, ...]
    target_shielding_attenuation_db: tuple[float, ...]
    max_step_s: float = 0.02
    integration_relative_tolerance: float = 1e-6
    integration_absolute_tolerance_s: float = 1e-12


@dataclass(frozen=True)
class BandExposure:
    band_center_hz: float
    received_sel_db_re_1s: float
    a_weighted_sel_db_re_1s: float


@dataclass(frozen=True)
class MovingSourceResult:
    status: str
    qualification: str
    duration_s: float
    min_range_m: float
    max_range_m: float
    band_exposures: tuple[BandExposure, ...]
    inband_a_weighted_sel_db_re_1s: float
    complete_a_weighted_sel_db_re_1s: float | None
    complete_a_weighted_laeq_db: float | None
    sampled_point_count: int
    integration_evaluation_count: int
    maximum_accepted_local_relative_error_estimate: float
    limitations: tuple[str, ...]


# IEC-style analytical A-weighting response evaluated at each band center.
def a_weighting_db(frequency_hz: float) -> float:
    f = _finite(frequency_hz, "frequency_hz")
    if f <= 0:
        raise PropagationError("frequency_hz must be greater than zero")
    f2 = f * f
    ra = (12194.0**2 * f**4) / (
        (f2 + 20.6**2)
        * math.sqrt((f2 + 107.7**2) * (f2 + 737.9**2))
        * (f2 + 12194.0**2)
    )
    return 20.0 * math.log10(ra) + 2.0


def _validate_spectrum(spectrum: ReferenceSpectrum) -> int:
    count = len(spectrum.band_center_hz)
    if count == 0 or len(spectrum.level_db) != count:
        raise PropagationError("spectrum frequencies and levels must be non-empty and paired")
    if not spectrum.source_label.strip():
        raise PropagationError("spectrum source_label is required")
    reference_azimuth = _finite(spectrum.reference_azimuth_deg, "reference_azimuth_deg")
    if reference_azimuth < 0 or reference_azimuth > 180:
        raise PropagationError("reference_azimuth_deg must be within [0, 180]")
    distance = _finite(spectrum.reference_distance_m, "reference_distance_m")
    if distance <= 0:
        raise PropagationError("reference_distance_m must be greater than zero")
    frequencies = [_finite(v, "band_center_hz") for v in spectrum.band_center_hz]
    if any(v <= 0 for v in frequencies) or frequencies != sorted(set(frequencies)):
        raise PropagationError("band centers must be positive, unique, and sorted")
    for level in spectrum.level_db:
        _finite(level, "reference band level")
    return count


def _validate_assumptions(assumptions: PropagationAssumptions, count: int) -> None:
    for name, values in (
        ("air_absorption_db_per_km", assumptions.air_absorption_db_per_km),
        ("reference_ground_excess_attenuation_db", assumptions.reference_ground_excess_attenuation_db),
        ("target_ground_excess_attenuation_db", assumptions.target_ground_excess_attenuation_db),
        ("reference_shielding_attenuation_db", assumptions.reference_shielding_attenuation_db),
        ("target_shielding_attenuation_db", assumptions.target_shielding_attenuation_db),
    ):
        if len(values) != count:
            raise PropagationError(f"{name} must have one value per spectral band")
        for value in values:
            if _finite(value, name) < 0:
                raise PropagationError(f"{name} values must be non-negative")
    step = _finite(assumptions.max_step_s, "max_step_s")
    if step <= 0 or step > 1.0:
        raise PropagationError("max_step_s must be in (0, 1] seconds")
    rel_tol = _finite(assumptions.integration_relative_tolerance, "integration_relative_tolerance")
    abs_tol = _finite(assumptions.integration_absolute_tolerance_s, "integration_absolute_tolerance_s")
    if rel_tol <= 0 or rel_tol > 0.01:
        raise PropagationError("integration_relative_tolerance must be in (0, 0.01]")
    if abs_tol <= 0:
        raise PropagationError("integration_absolute_tolerance_s must be positive")


def _validate_directivity(knots: Sequence[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    normalized = tuple((_finite(angle, "directivity angle"), _finite(gain, "directivity gain")) for angle, gain in knots)
    if not normalized or normalized[0][0] != 0.0:
        raise PropagationError("directivity must include a 0-degree reference knot")
    if any(angle < 0 or angle > 180 for angle, _ in normalized):
        raise PropagationError("directivity angles must be within [0, 180] degrees")
    if any(normalized[i][0] >= normalized[i + 1][0] for i in range(len(normalized) - 1)):
        raise PropagationError("directivity angles must be strictly increasing")
    if abs(normalized[0][1]) > 1e-9:
        raise PropagationError("directivity gain at 0 degrees must be the reference 0 dB")
    return normalized


def _directivity_gain(angle_deg: float, knots: Sequence[tuple[float, float]]) -> float:
    angle = abs(angle_deg)
    if angle > knots[-1][0] + 1e-9:
        raise PropagationError(f"trajectory uses unmeasured directivity angle {angle:.3f} degrees")
    if angle <= knots[0][0]:
        return knots[0][1]
    for (a0, g0), (a1, g1) in zip(knots, knots[1:]):
        if angle <= a1:
            proportion = (angle - a0) / (a1 - a0)
            return g0 + proportion * (g1 - g0)
    return knots[-1][1]


def _interpolate_track(a: TrajectoryPoint, b: TrajectoryPoint, time_s: float) -> tuple[float, float, float, float]:
    ratio = (time_s - a.time_s) / (b.time_s - a.time_s)
    x = a.x_m + ratio * (b.x_m - a.x_m)
    y = a.y_m + ratio * (b.y_m - a.y_m)
    z = a.z_m + ratio * (b.z_m - a.z_m)
    delta_heading = (b.heading_deg - a.heading_deg + 180.0) % 360.0 - 180.0
    heading = (a.heading_deg + ratio * delta_heading) % 360.0
    return x, y, z, heading


def _sample_times(track: Sequence[TrajectoryPoint], max_step_s: float) -> list[tuple[float, int]]:
    samples: list[tuple[float, int]] = []
    for segment_index, (a, b) in enumerate(zip(track, track[1:])):
        duration = b.time_s - a.time_s
        steps = max(1, math.ceil(duration / max_step_s))
        first = 0 if segment_index == 0 else 1
        for i in range(first, steps + 1):
            samples.append((a.time_s + duration * i / steps, segment_index))
    return samples


def integrate_moving_source(
    spectrum: ReferenceSpectrum,
    trajectory: Sequence[TrajectoryPoint],
    receiver: Receiver,
    *,
    directivity_knots_db: Sequence[tuple[float, float]],
    assumptions: PropagationAssumptions,
) -> MovingSourceResult:
    """Integrate received per-band energy along an explicit piecewise-linear path.

    Headings are degrees counter-clockwise from +x (east). Directivity knots
    are absolute azimuth from the forward direction, symmetric left/right, in
    dB relative to the reference heading. The method uses adaptive Simpson integration of linear acoustic energy,
    initially panelled no wider than ``max_step_s``. It rejects trajectories outside the measured directivity
    angular support instead of extrapolating.
    """
    count = _validate_spectrum(spectrum)
    _validate_assumptions(assumptions, count)
    directivity = _validate_directivity(directivity_knots_db)
    if len(trajectory) < 2:
        raise PropagationError("trajectory requires at least two timed points")
    normalized_track: list[TrajectoryPoint] = []
    for point in trajectory:
        try:
            normalized = TrajectoryPoint(
                _finite(point.time_s, "time_s"),
                _finite(point.x_m, "x_m"),
                _finite(point.y_m, "y_m"),
                _finite(point.z_m, "z_m"),
                _finite(point.heading_deg, "heading_deg"),
            )
        except AttributeError as error:
            raise PropagationError("trajectory entries must be TrajectoryPoint values") from error
        if normalized.z_m < 0:
            raise PropagationError("source height z_m must be non-negative")
        normalized_track.append(normalized)
    if any(normalized_track[i].time_s >= normalized_track[i + 1].time_s for i in range(len(normalized_track) - 1)):
        raise PropagationError("trajectory times must be strictly increasing")
    try:
        normalized_receiver = Receiver(
            _finite(receiver.x_m, "receiver.x_m"),
            _finite(receiver.y_m, "receiver.y_m"),
            _finite(receiver.z_m, "receiver.z_m"),
        )
    except AttributeError as error:
        raise PropagationError("receiver must be a Receiver value") from error
    if normalized_receiver.z_m < 0:
        raise PropagationError("receiver height z_m must be non-negative")

    # The Euclidean distance to a linear 3-D segment is convex, so its maximum
    # is at a segment endpoint and its minimum is the clamped projection.
    # Use that exact minimum both for the singularity gate and the reported
    # closest range; sampled points alone can miss a narrow passby.
    closest_fractions: list[float] = []
    segment_min_ranges: list[float] = []
    for a, b in zip(normalized_track, normalized_track[1:]):
        dx, dy, dz = b.x_m - a.x_m, b.y_m - a.y_m, b.z_m - a.z_m
        length_squared = dx * dx + dy * dy + dz * dz
        fraction = 0.0 if length_squared == 0 else (
            (normalized_receiver.x_m - a.x_m) * dx
            + (normalized_receiver.y_m - a.y_m) * dy
            + (normalized_receiver.z_m - a.z_m) * dz
        ) / length_squared
        fraction = min(1.0, max(0.0, fraction))
        closest = (a.x_m + fraction * dx, a.y_m + fraction * dy, a.z_m + fraction * dz)
        nearest_range = math.sqrt(
            (normalized_receiver.x_m - closest[0]) ** 2
            + (normalized_receiver.y_m - closest[1]) ** 2
            + (normalized_receiver.z_m - closest[2]) ** 2
        )
        closest_fractions.append(fraction)
        segment_min_ranges.append(nearest_range)
        if nearest_range <= 1e-9:
            raise PropagationError("trajectory segment intersects the receiver")
    evaluation_count = 0
    maximum_error_ratio = 0.0
    band_integrals: list[float] = []
    duration = normalized_track[-1].time_s - normalized_track[0].time_s

    def transfer_energy(band: int, segment: int, time_s: float) -> float:
        nonlocal evaluation_count
        evaluation_count += 1
        a, b = normalized_track[segment], normalized_track[segment + 1]
        sx, sy, sz, heading = _interpolate_track(a, b, time_s)
        dx, dy, dz = normalized_receiver.x_m - sx, normalized_receiver.y_m - sy, normalized_receiver.z_m - sz
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance <= 0:
            raise PropagationError("source trajectory intersects the receiver")
        bearing = math.degrees(math.atan2(dy, dx)) % 360.0
        relative_angle = (bearing - heading + 180.0) % 360.0 - 180.0
        gain = _directivity_gain(relative_angle, directivity) - _directivity_gain(spectrum.reference_azimuth_deg, directivity)
        transfer_db = (
            20.0 * math.log10(spectrum.reference_distance_m / distance)
            + gain
            - assumptions.air_absorption_db_per_km[band] * (distance - spectrum.reference_distance_m) / 1000.0
            - (assumptions.target_ground_excess_attenuation_db[band] - assumptions.reference_ground_excess_attenuation_db[band])
            - (assumptions.target_shielding_attenuation_db[band] - assumptions.reference_shielding_attenuation_db[band])
        )
        exponent = transfer_db / 10.0
        if exponent > 300.0:
            raise PropagationError("trajectory transfer exceeds stable energy integration range")
        return 10.0**exponent

    def adaptive_simpson(function, left: float, right: float, abs_tol: float, rel_tol: float, max_depth: int = 24) -> tuple[float, float]:
        mid = (left + right) / 2.0
        f_left, f_mid, f_right = function(left), function(mid), function(right)
        whole = (right - left) * (f_left + 4.0 * f_mid + f_right) / 6.0

        def recurse(lo, hi, flo, fmid, fhi, estimate, tolerance, depth):
            nonlocal maximum_error_ratio
            center = (lo + hi) / 2.0
            left_mid, right_mid = (lo + center) / 2.0, (center + hi) / 2.0
            f_left_mid, f_right_mid = function(left_mid), function(right_mid)
            left_est = (center - lo) * (flo + 4.0 * f_left_mid + fmid) / 6.0
            right_est = (hi - center) * (fmid + 4.0 * f_right_mid + fhi) / 6.0
            combined = left_est + right_est
            error = abs(combined - estimate) / 15.0
            allowed = tolerance + rel_tol * abs(combined)
            if error <= allowed:
                maximum_error_ratio = max(maximum_error_ratio, error / max(abs(combined), 1e-300))
                return combined + (combined - estimate) / 15.0
            if depth <= 0:
                raise PropagationError("adaptive energy integration did not converge within 24 refinements")
            return recurse(lo, center, flo, f_left_mid, fmid, left_est, tolerance / 2.0, depth - 1) + recurse(center, hi, fmid, f_right_mid, fhi, right_est, tolerance / 2.0, depth - 1)

        return recurse(left, right, f_left, f_mid, f_right, whole, abs_tol, max_depth), whole

    # Integrate separately on every straight-track segment, with an exact
    # closest-approach split and max_step_s initial panels. Adaptive Simpson
    # refinement then resolves fast, close passbys without relying on a lucky
    # time sample at the peak.
    for band in range(count):
        integral = 0.0
        for segment, (a, b) in enumerate(zip(normalized_track, normalized_track[1:])):
            segment_duration = b.time_s - a.time_s
            closest_time = a.time_s + closest_fractions[segment] * segment_duration
            cuts = sorted({a.time_s, b.time_s, closest_time})
            fn = lambda t, band=band, segment=segment: transfer_energy(band, segment, t)
            for cut_left, cut_right in zip(cuts, cuts[1:]):
                if cut_right <= cut_left:
                    continue
                panels = max(1, math.ceil((cut_right - cut_left) / assumptions.max_step_s))
                for panel in range(panels):
                    left = cut_left + (cut_right - cut_left) * panel / panels
                    right = cut_left + (cut_right - cut_left) * (panel + 1) / panels
                    panel_integral, _ = adaptive_simpson(
                        fn,
                        left,
                        right,
                        assumptions.integration_absolute_tolerance_s / max(1, panels * (len(normalized_track) - 1)),
                        assumptions.integration_relative_tolerance,
                    )
                    integral += panel_integral
        if not math.isfinite(integral) or integral <= 0:
            raise PropagationError("integrated energy is not finite and positive")
        band_integrals.append(integral)

    band_results: list[BandExposure] = []
    for index, integral in enumerate(band_integrals):
        sel = spectrum.level_db[index] + 10.0 * math.log10(integral)
        band_results.append(BandExposure(spectrum.band_center_hz[index], sel, sel + a_weighting_db(spectrum.band_center_hz[index])))
    inband_sel = combine_sel_db([band.a_weighted_sel_db_re_1s for band in band_results])
    complete_sel = inband_sel if spectrum.spectrum_complete else None
    complete_laeq = complete_sel - 10.0 * math.log10(duration) if complete_sel is not None else None

    return MovingSourceResult(
        status="computed",
        qualification="complete_spectrum" if spectrum.spectrum_complete else "partial_spectrum_no_total_A_weighted_result",
        duration_s=duration,
        min_range_m=min(segment_min_ranges),
        max_range_m=max(
            math.dist(
                (point.x_m, point.y_m, point.z_m),
                (normalized_receiver.x_m, normalized_receiver.y_m, normalized_receiver.z_m),
            )
            for point in normalized_track
        ),
        band_exposures=tuple(band_results),
        inband_a_weighted_sel_db_re_1s=inband_sel,
        complete_a_weighted_sel_db_re_1s=complete_sel,
        complete_a_weighted_laeq_db=complete_laeq,
        sampled_point_count=evaluation_count,
        integration_evaluation_count=evaluation_count,
        maximum_accepted_local_relative_error_estimate=maximum_error_ratio,
        limitations=(
            "Input spectrum is a received reference measurement, not identified source power.",
            "Quasi-static levels omit Doppler and retarded-time corrections.",
            "No terrain/building diffraction, reflections, or ambient background is modeled.",
            "No activity rate is inferred; the caller supplies each trajectory and timing.",
        ),
    )
