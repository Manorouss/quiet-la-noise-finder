"""Small Doc 29 5th Edition metric/interpolation helpers; not a full model."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite, log10
from typing import Iterable, Literal, Sequence
from zoneinfo import ZoneInfo

Metric = Literal["SEL", "LAmax", "EPNL", "PNLTM"]
Operation = Literal["A", "D"]


class Doc29InputError(ValueError):
    """Raised when an input is underspecified or metric-incompatible."""


@dataclass(frozen=True)
class NPDTable:
    aircraft_id: str
    npd_id: str
    metric: Metric
    operation: Operation
    power_unit: Literal["lb_per_engine", "percent_per_engine", "hp_per_engine", "rpm_per_engine"]
    power_settings: tuple[float, ...]
    distances_m: tuple[float, ...]
    levels_db: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        if not self.aircraft_id.strip() or not self.npd_id.strip():
            raise Doc29InputError("aircraft_id and npd_id must be explicit")
        if self.metric not in ("SEL", "LAmax", "EPNL", "PNLTM"):
            raise Doc29InputError("unsupported or unspecified NPD metric")
        if self.operation not in ("A", "D"):
            raise Doc29InputError("operation must be A (arrival) or D (departure)")
        if self.power_unit not in ("lb_per_engine", "percent_per_engine", "hp_per_engine", "rpm_per_engine"):
            raise Doc29InputError("power units must be explicit and supported")
        if len(self.power_settings) < 2 or len(self.distances_m) < 2:
            raise Doc29InputError("NPD interpolation requires at least two power and distance points")
        if len(self.levels_db) != len(self.power_settings) or any(len(r) != len(self.distances_m) for r in self.levels_db):
            raise Doc29InputError("NPD table dimensions do not match its axes")
        if not _strictly_increasing(self.power_settings) or not _strictly_increasing(self.distances_m):
            raise Doc29InputError("NPD power and distance axes must be strictly increasing")
        if any(d <= 0 for d in self.distances_m):
            raise Doc29InputError("NPD distance points must be positive")
        if any(not isfinite(v) for row in self.levels_db for v in row):
            raise Doc29InputError("NPD values must be finite dB values")


@dataclass(frozen=True)
class SELObservation:
    timestamp: datetime
    level_db: float
    metric: Metric
    aircraft_id: str
    operation: Operation
    provenance: str


def _strictly_increasing(values: Sequence[float]) -> bool:
    return all(isfinite(v) for v in values) and all(a < b for a, b in zip(values, values[1:]))


def _bracket(axis: Sequence[float], value: float, *, extrapolate: bool) -> tuple[int, int]:
    if len(axis) < 2 or not isfinite(value):
        raise Doc29InputError("interpolation requires finite value and two or more axis points")
    if value < axis[0]:
        if not extrapolate:
            raise Doc29InputError("value below NPD axis; power extrapolation is disabled")
        return 0, 1
    if value > axis[-1]:
        if not extrapolate:
            raise Doc29InputError("value above NPD axis; power extrapolation is disabled")
        return len(axis) - 2, len(axis) - 1
    for i in range(len(axis) - 1):
        if axis[i] <= value <= axis[i + 1]:
            return i, i + 1
    raise Doc29InputError("failed to bracket interpolation value")


def _linear_interpolate(axis: Sequence[float], vals: Sequence[float], x: float, *, extrapolate: bool) -> float:
    lo, hi = _bracket(axis, x, extrapolate=extrapolate)
    fraction = (x - axis[lo]) / (axis[hi] - axis[lo])
    return vals[lo] + fraction * (vals[hi] - vals[lo])


def npd_level(table: NPDTable, *, aircraft_id: str, npd_id: str, metric: Metric,
              operation: Operation, power: float, power_unit: str, distance_m: float,
              allow_power_extrapolation: bool = False,
              minimum_distance_m: float = 30.0) -> float:
    """Return NPD level using Doc 29 5e eqs (4-3) and (4-4)/(4-5).

    Power interpolates linearly and is limited to tabulated settings. Distance
    interpolates in log-distance and uses adjacent-edge extrapolation. Doc 29
    Vol. 2 §4.3's recommended 30 m minimum is the default and can be changed
    only by an explicit reference-policy run.
    """
    if (aircraft_id, npd_id, metric, operation, power_unit) != (
        table.aircraft_id, table.npd_id, table.metric, table.operation, table.power_unit
    ):
        raise Doc29InputError("aircraft, NPD identifier, operation, metric, or power units do not match this table")
    if not isfinite(power) or not isfinite(distance_m) or distance_m <= 0:
        raise Doc29InputError("power and positive distance must be finite")
    negative_power_reset = power < 0
    if negative_power_reset:
        power = 1.0  # Doc 29 §4.2: reset negative power to one pound/engine.
        if power_unit != "lb_per_engine":
            raise Doc29InputError("Doc 29 negative-power floor applies to lb/engine data only")
    outside_power = power < table.power_settings[0] or power > table.power_settings[-1]
    if outside_power and not (allow_power_extrapolation or negative_power_reset):
        raise Doc29InputError("power outside NPD table; extrapolation can be material and is disabled")
    if not isfinite(minimum_distance_m) or minimum_distance_m <= 0:
        raise Doc29InputError("minimum NPD distance must be finite and positive")
    d = max(distance_m, minimum_distance_m)
    log_axis = tuple(log10(x) for x in table.distances_m)
    log_d = log10(d)
    # First interpolate/extrapolate each listed power curve in log distance,
    # then linearly interpolate levels in power, matching the Doc 29 definition.
    by_power = []
    for row in table.levels_db:
        by_power.append(_linear_interpolate(log_axis, row, log_d, extrapolate=True))
    level = _linear_interpolate(table.power_settings, by_power, power, extrapolate=outside_power)
    if outside_power:
        nearest = by_power[0] if power < table.power_settings[0] else by_power[-1]
        if abs(level - nearest) > 5.0:
            raise Doc29InputError("power extrapolation exceeds the Doc 29 5 dB caution threshold")
    return level


def airborne_segment_sel(*, baseline_sel_db: float, impedance_adjustment_db: float,
                         duration_correction_db: float, installation_correction_db: float,
                         lateral_attenuation_db: float, finite_segment_correction_db: float,
                         line_of_sight_blockage_db: float) -> float:
    """Apply all explicit terms in Doc 29 5e Eq. (4-8b) for an airborne segment.

    Acoustic impedance adjusts the NPD baseline. The LOS term must be supplied
    explicitly as a measured/modelled value or 0.0 if no blockage is represented.
    This does not calculate any correction term.
    """
    components = (baseline_sel_db, impedance_adjustment_db, duration_correction_db,
                  installation_correction_db, lateral_attenuation_db,
                  finite_segment_correction_db, line_of_sight_blockage_db)
    if any(not isfinite(float(v)) for v in components):
        raise Doc29InputError("all airborne segment correction terms must be explicit finite dB values")
    return (baseline_sel_db + impedance_adjustment_db + duration_correction_db
            + installation_correction_db - lateral_attenuation_db
            + finite_segment_correction_db + line_of_sight_blockage_db)


def energy_sum_db(levels_db: Iterable[float]) -> float:
    """Stable dB energy sum, equivalent to 10 log10(sum(10**(L/10)))."""
    levels = [float(v) for v in levels_db]
    if not levels:
        raise Doc29InputError("an energy sum requires at least one sound level")
    if any(not isfinite(v) for v in levels):
        raise Doc29InputError("energy sum levels must be finite")
    pivot = max(levels)
    return pivot + 10.0 * log10(sum(10.0 ** ((v - pivot) / 10.0) for v in levels))


def _require_aware(t: datetime) -> None:
    if t.tzinfo is None or t.utcoffset() is None:
        raise Doc29InputError("timestamps must include a timezone/UTC offset")


def _validate_sel_events(events: Sequence[SELObservation]) -> None:
    if not events:
        raise Doc29InputError("no SEL events supplied; empty input is not treated as a measured zero")
    for ev in events:
        _require_aware(ev.timestamp)
        if ev.metric != "SEL":
            raise Doc29InputError(f"expected A-weighted SEL events; got {ev.metric}")
        if not isfinite(ev.level_db):
            raise Doc29InputError("event SEL must be finite")
        if not ev.aircraft_id or ev.operation not in ("A", "D") or not ev.provenance:
            raise Doc29InputError("aircraft id, arrival/departure mode, and provenance are required")


def laeq_from_sel(events: Sequence[SELObservation], start: datetime, end: datetime) -> float:
    """Time-average SEL energy in [start,end) over actual elapsed seconds.

    Event times are instants representing event occurrence (normally takeoff or
    landing time). SEL exposure is assigned to the event timestamp; crossing a
    reporting boundary is therefore resolved by that explicit event-time rule.
    """
    _validate_sel_events(events)
    _require_aware(start); _require_aware(end)
    elapsed = (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()
    if elapsed <= 0:
        raise Doc29InputError("window must have positive elapsed duration")
    selected = [e for e in events if start.astimezone(timezone.utc) <= e.timestamp.astimezone(timezone.utc) < end.astimezone(timezone.utc)]
    if not selected:
        raise Doc29InputError("window contains no supplied SEL event; background sound is not represented")
    total = energy_sum_db(e.level_db for e in selected)
    return total - 10.0 * log10(elapsed)


def daily_cnel_from_sel(events: Sequence[SELObservation], local_date: date,
                        timezone_name: str = "America/Los_Angeles") -> float:
    """Regulatory event-energy CNEL for a local calendar date.

    Implements Title 21 CCR §5001(f): hourly noise energy summed over a nominal
    24-hour denominator, with evening factor 3 and night factor 10. Events are
    assigned by their timezone-converted occurrence timestamp. This is an
    event-level aircraft calculation, not LAWA's undisclosed daily processing.
    """
    _validate_sel_events(events)
    zone = ZoneInfo(timezone_name)
    start_local = datetime.combine(local_date, time.min, tzinfo=zone)
    end_local = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone)
    start_utc, end_utc = start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
    selected = [e for e in events if start_utc <= e.timestamp.astimezone(timezone.utc) < end_utc]
    if not selected:
        raise Doc29InputError("local date contains no supplied SEL event; background sound is not represented")
    weighted = []
    for ev in selected:
        local = ev.timestamp.astimezone(zone)
        hour = local.hour + local.minute / 60.0 + local.second / 3600.0
        factor = 10.0 if hour >= 22.0 or hour < 7.0 else 3.0 if hour >= 19.0 else 1.0
        weighted.append(ev.level_db + 10.0 * log10(factor))
    return energy_sum_db(weighted) - 10.0 * log10(24.0 * 3600.0)
