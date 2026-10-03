"""Load private legacy ANP v2.3 tables into explicitly unit-tagged records.

No ANP rows are emitted to public/output data. EASA terms still govern use.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path
from zipfile import ZipFile

from .doc29 import Doc29InputError, Metric, NPDTable, Operation

FT_TO_M = 0.3048


@dataclass(frozen=True)
class AircraftNoiseMatch:
    requested_aircraft_id: str
    npd_id: str
    power_parameter: str
    operation: Operation
    metric: Metric
    match_kind: str
    terms_note: str = "EASA legacy ANP v2.3; internal use only, subject to EASA terms"


def _rows(zf: ZipFile, filename: str):
    with zf.open(filename) as raw:
        # Legacy archive files use Windows-1252 and semicolon delimiters.
        text = TextIOWrapper(raw, encoding="cp1252", newline="")
        yield from csv.DictReader(text, delimiter=";")


def load_anp_npd_table(archive: Path, aircraft_id: str, metric: Metric,
                       operation: Operation) -> tuple[AircraftNoiseMatch, NPDTable]:
    """Resolve aircraft->NPD ID and load exactly one operation/metric table."""
    if operation not in ("A", "D"):
        raise Doc29InputError("operation must be A (arrival) or D (departure)")
    with ZipFile(archive) as zf:
        aircraft = [r for r in _rows(zf, "ANP2.3_Aircraft.csv") if r["ACFT_ID"].strip() == aircraft_id]
        if len(aircraft) != 1:
            raise Doc29InputError(f"expected exactly one ANP aircraft row for {aircraft_id!r}")
        a = aircraft[0]
        npd_id = a["NPD_ID"].strip()
        metric_rows = [r for r in _rows(zf, "ANP2.3_NPD_data.csv")
                       if r["NPD_ID"].strip() == npd_id and r["Noise Metric"].strip() == metric and r["Op Mode"].strip() == operation]
        powers = sorted({float(r["Power Setting"]) for r in metric_rows})
        if len(powers) < 2:
            raise Doc29InputError(f"ANP table lacks >=2 power points for {aircraft_id}/{metric}/{operation}")
        # NPD distance headers are explicitly in feet. Convert to metres at load.
        headers = metric_rows[0].keys()
        distance_columns = []
        for name in headers:
            if name.startswith("L_") and name.endswith("ft"):
                distance_ft = float(name[2:-2])
                distance_columns.append((distance_ft * FT_TO_M, name))
        distance_columns.sort()
        by_power = {p: {} for p in powers}
        for row in metric_rows:
            power = float(row["Power Setting"])
            for distance_m, column in distance_columns:
                raw = row[column].strip()
                if raw:
                    by_power[power][distance_m] = float(raw)
        if not distance_columns or any(set(by_power[p]) != {d for d, _ in distance_columns} for p in powers):
            raise Doc29InputError("ANP NPD curve has missing levels; interpolation table must be rectangular")
        ordered_distances = tuple(d for d, _ in distance_columns)
        levels = tuple(tuple(by_power[p][d] for d in ordered_distances) for p in powers)
        power_parameter = a["Power Parameter"].strip()
        if "lb" in power_parameter.lower():
            power_unit = "lb_per_engine"
        elif "%" in power_parameter:
            power_unit = "percent_per_engine"
        elif "hp" in power_parameter.lower():
            power_unit = "hp_per_engine"
        elif "rpm" in power_parameter.lower():
            power_unit = "rpm_per_engine"
        else:
            raise Doc29InputError(f"unsupported ANP power parameter: {power_parameter!r}")
        # Match the ANP aircraft identifier. NPD_ID remains explicit provenance;
        # it is not substituted as aircraft identity.
        table = NPDTable(aircraft_id, npd_id, metric, operation, power_unit,
                         tuple(powers), ordered_distances, levels)
        match = AircraftNoiseMatch(aircraft_id, npd_id, power_parameter, operation,
                                   metric, "exact ANP aircraft identifier")
        return match, table
