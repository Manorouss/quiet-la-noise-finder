"""Energy accounting for explicitly timed, received emergency-vehicle events.

This module does not model propagation, routes, siren activation, or dispatch
frequency. Inputs must already be A-weighted event-equivalent levels at the
receiver and cover a stated, finite event window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
from typing import Iterable


class ExposureError(ValueError):
    """Raised when an event lacks a defensible metric or time basis."""


def _finite(value: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ExposureError(f"{label} must be numeric and finite") from error
    if not math.isfinite(number):
        raise ExposureError(f"{label} must be finite")
    return number


def received_sel_db(laeq_db: float, duration_s: float, *, metric: str = "LAeq", weighting: str = "A") -> float:
    """Convert a received LAeq over an event window to standard 1-second SEL.

    SEL = LAeq,T + 10 log10(T / 1 second). `Leq` without an explicit
    A-weighted label, Lmax, percentile levels, and the Appendix E "1/10 s SEL"
    field are intentionally rejected as inputs.
    """
    if metric != "LAeq":
        raise ExposureError("metric must be explicitly reported as LAeq")
    if weighting != "A":
        raise ExposureError("weighting must be explicitly reported as A")
    level = _finite(laeq_db, "LAeq")
    duration = _finite(duration_s, "duration_s")
    if duration <= 0:
        raise ExposureError("duration_s must be greater than zero")
    return level + 10.0 * math.log10(duration)


def laeq_from_sel_db(sel_db: float, duration_s: float) -> float:
    """Recover constant-window LAeq from standard 1-second SEL and duration."""
    sel = _finite(sel_db, "SEL")
    duration = _finite(duration_s, "duration_s")
    if duration <= 0:
        raise ExposureError("duration_s must be greater than zero")
    return sel - 10.0 * math.log10(duration)


def combine_sel_db(sel_levels_db: Iterable[float]) -> float:
    """Combine independent acoustic contributions in energy, not dB arithmetic.

    Callers must establish that the inputs are separate physical sources or
    model components. This is not safe for overlapping received passby
    recordings, which should be represented once and passed through
    :func:`timed_laeq_db`.
    """
    values = [_finite(value, "SEL") for value in sel_levels_db]
    if not values:
        return float("-inf")
    maximum = max(values)
    return maximum + 10.0 * math.log10(sum(10.0 ** ((value - maximum) / 10.0) for value in values))


@dataclass(frozen=True)
class ReceivedEvent:
    """One received recording window at a receiver, with explicit time bounds.

    The reported window may contain siren, vehicle, and background sound. It
    is never interpreted as siren-only exposure by this module.
    """

    event_id: str
    start: datetime
    duration_s: float
    laeq_db: float
    metric: str = "LAeq"
    weighting: str = "A"

    def validated_bounds(self) -> tuple[datetime, datetime]:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ExposureError("event_id must be non-empty")
        if not isinstance(self.start, datetime):
            raise ExposureError(f"{self.event_id}: start must be a timezone-aware datetime")
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ExposureError(f"{self.event_id}: start must include a timezone")
        duration = _finite(self.duration_s, f"{self.event_id} duration_s")
        if duration <= 0:
            raise ExposureError(f"{self.event_id}: duration_s must be greater than zero")
        received_sel_db(self.laeq_db, duration, metric=self.metric, weighting=self.weighting)
        start_utc = self.start.astimezone(timezone.utc)
        try:
            end_utc = start_utc + timedelta(seconds=duration)
        except OverflowError as error:
            raise ExposureError(f"{self.event_id}: duration is outside supported datetime range") from error
        return start_utc, end_utc


def timed_laeq_db(events: Iterable[ReceivedEvent], period_start: datetime, period_end: datetime) -> float:
    """Calculate LAeq,T from events wholly inside one timezone-aware period.

    The recorded-window SEL energies are added, then divided by period
    seconds. Each recording can contain siren, vehicle, and background sound.
    Overlapping windows and repeated IDs are rejected so a mixed recording is
    not double-counted as separate siren activity. Events crossing a period
    edge are rejected rather than silently truncated. Unmeasured background
    in the rest of the requested period is excluded; this is not a siren-only
    or complete ambient LAeq.
    """
    if not isinstance(period_start, datetime) or period_start.tzinfo is None or period_start.utcoffset() is None:
        raise ExposureError("period_start must include a timezone")
    if not isinstance(period_end, datetime) or period_end.tzinfo is None or period_end.utcoffset() is None:
        raise ExposureError("period_end must include a timezone")
    start_utc = period_start.astimezone(timezone.utc)
    end_utc = period_end.astimezone(timezone.utc)
    period_s = (end_utc - start_utc).total_seconds()
    if not math.isfinite(period_s) or period_s <= 0:
        raise ExposureError("period_end must be after period_start")

    event_sels: list[float] = []
    seen_ids: set[str] = set()
    ordered_bounds: list[tuple[datetime, datetime, str]] = []
    for event in events:
        start, end = event.validated_bounds()
        if event.event_id in seen_ids:
            raise ExposureError(f"duplicate event_id: {event.event_id}")
        seen_ids.add(event.event_id)
        if start < start_utc or end > end_utc:
            raise ExposureError(f"{event.event_id}: event must fit wholly within the requested period")
        sel = received_sel_db(event.laeq_db, event.duration_s, metric=event.metric, weighting=event.weighting)
        event_sels.append(sel)
        ordered_bounds.append((start, end, event.event_id))
    ordered_bounds.sort()
    for previous, current in zip(ordered_bounds, ordered_bounds[1:]):
        if current[0] < previous[1]:
            raise ExposureError(f"received recording windows overlap: {previous[2]} and {current[2]}")
    if not event_sels:
        return float("-inf")
    max_sel = max(event_sels)
    scaled_energy = sum(10.0 ** ((sel - max_sel) / 10.0) for sel in event_sels)
    return max_sel + 10.0 * math.log10(scaled_energy / period_s)
