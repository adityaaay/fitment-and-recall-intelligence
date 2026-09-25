"""Deterministic generator for a synthetic aftermarket parts catalog (PIM export).

Real aftermarket catalogs are proprietary, so this module fabricates one - but on top of
the *real* vPIC vehicle universe, with the structure and failure modes of a real feed:

* ACES-style fitment: one row per part x make/model x model-year range, with position.
* Parts are engineered per vehicle "generation" (a contiguous run of model years), and a
  part is sometimes interchangeable across a sibling model (badge-engineered twins).
* Coverage is biased the way the aftermarket actually is: thin on the newest model years
  (parts lag OE introduction by 1-3 years) and on low-volume / EV-first brands.
* A small, known share of rows carries defects typical of supplier feeds - casing and
  alias noise, reversed or impossible year ranges, orphans, duplicates, bad prices -
  so the validation and standardisation layers have something real to catch.

The same (seed, revision) always produces byte-identical files. Revision 2+ applies a
price update and some discontinuations on top of revision 1, which is what drives the
SCD2 snapshot of the parts dimension.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PartType:
    name: str
    category: str
    code: str
    base_price: float
    coverage: float  # probability a given make/model carries this part type
    positions: tuple[str, ...] = ("N/A",)


PART_TYPES: list[PartType] = [
    PartType("Ignition Coil", "Electrical & Ignition", "IC", 58, 0.55),
    PartType("Crankshaft Position Sensor", "Electrical & Ignition", "CPS", 34, 0.50),
    PartType("Camshaft Position Sensor", "Electrical & Ignition", "CMP", 31, 0.48),
    PartType("Ignition Switch", "Electrical & Ignition", "ISW", 44, 0.30),
    PartType("Window Regulator Motor", "Electrical & Ignition", "WRM", 72, 0.35,
             ("Front Left", "Front Right")),
    PartType("Mass Air Flow Sensor", "Engine & Cooling", "MAF", 96, 0.42),
    PartType("Oxygen Sensor", "Engine & Cooling", "OXS", 48, 0.60, ("Upstream", "Downstream")),
    PartType("Knock Sensor", "Engine & Cooling", "KS", 29, 0.40),
    PartType("EGR Valve", "Engine & Cooling", "EGR", 118, 0.28),
    PartType("Engine Coolant Temperature Sensor", "Engine & Cooling", "ECT", 18, 0.55),
    PartType("Thermostat Housing", "Engine & Cooling", "TH", 41, 0.45),
    PartType("Radiator Fan Motor", "Engine & Cooling", "RFM", 88, 0.33),
    PartType("Fuel Pump Module", "Fuel System", "FPM", 164, 0.45),
    PartType("Fuel Injector", "Fuel System", "FI", 62, 0.40),
    PartType("Fuel Pressure Sensor", "Fuel System", "FPS", 57, 0.30),
    PartType("Fuel Tank Sending Unit", "Fuel System", "FSU", 71, 0.22),
    PartType("Transmission Speed Sensor", "Powertrain", "TSS", 32, 0.42),
    PartType("Transmission Control Solenoid", "Powertrain", "TCS", 54, 0.30),
    PartType("Shift Interlock Solenoid", "Powertrain", "SIS", 38, 0.18),
    PartType("ABS Wheel Speed Sensor", "Brakes", "ABS", 36, 0.62, ("Front", "Rear")),
    PartType("Brake Master Cylinder", "Brakes", "BMC", 104, 0.34),
    PartType("Brake Light Switch", "Brakes", "BLS", 16, 0.50),
    PartType("Power Steering Pressure Switch", "Steering", "PSP", 27, 0.25),
    PartType("Steering Angle Sensor", "Steering", "SAS", 89, 0.28),
    PartType("Tie Rod End", "Steering", "TRE", 33, 0.50, ("Outer", "Inner")),
    PartType("Wheel Hub Bearing Assembly", "Suspension & Wheels", "HUB", 92, 0.52,
             ("Front", "Rear")),
    PartType("TPMS Sensor", "Suspension & Wheels", "TPM", 39, 0.58),
    PartType("Ride Height Sensor", "Suspension & Wheels", "RHS", 77, 0.15),
    PartType("Headlight Switch", "Lighting", "HLS", 42, 0.32),
    PartType("Headlamp Leveling Motor", "Lighting", "HLM", 66, 0.12),
    PartType("Windshield Wiper Motor", "Visibility & Wipers", "WWM", 84, 0.38,
             ("Front", "Rear")),
    PartType("Washer Fluid Pump", "Visibility & Wipers", "WFP", 19, 0.40),
    PartType("Parking Assist Sensor", "Driver Assist & Stability", "PAS", 49, 0.26),
    PartType("Blind Spot Radar Sensor", "Driver Assist & Stability", "BSR", 212, 0.08),
    PartType("Rear View Camera", "Driver Assist & Stability", "RVC", 138, 0.14),
    PartType("Cruise Control Switch", "Driver Assist & Stability", "CCS", 47, 0.24),
    PartType("Door Lock Actuator", "Body & Access", "DLA", 46, 0.44,
             ("Front Left", "Front Right", "Rear Left", "Rear Right")),
    PartType("Trunk Lid Latch", "Body & Access", "TLL", 53, 0.20),
    PartType("Hybrid Battery Cooling Fan", "Hybrid & EV", "HBF", 126, 0.06),
    PartType("Inverter Coolant Pump", "Hybrid & EV", "ICP", 158, 0.05),
]

# Relative catalog depth by make: aftermarket programmes follow vehicles-in-operation,
# so high-volume domestic/Asian makes are deep and low-volume or EV-first makes are thin.
MAKE_DEPTH = {
    "FORD": 1.25, "CHEVROLET": 1.25, "TOYOTA": 1.2, "HONDA": 1.2, "NISSAN": 1.1, "RAM": 1.1,
    "JEEP": 1.1, "DODGE": 1.05, "GMC": 1.1, "HYUNDAI": 1.0, "KIA": 1.0, "SUBARU": 1.0,
    "CHRYSLER": 0.95, "BUICK": 0.9, "MAZDA": 0.9, "VOLKSWAGEN": 0.85, "LEXUS": 0.8,
    "ACURA": 0.8, "CADILLAC": 0.8, "LINCOLN": 0.8, "MITSUBISHI": 0.75, "INFINITI": 0.7,
    "BMW": 0.7, "MERCEDES-BENZ": 0.7, "AUDI": 0.65, "VOLVO": 0.55, "MINI": 0.5,
    "LAND ROVER": 0.4, "PORSCHE": 0.35, "JAGUAR": 0.35, "FIAT": 0.4, "GENESIS": 0.3,
    "ALFA ROMEO": 0.25, "TESLA": 0.15,
}

BRAND_LINES = ["Apex OE", "Apex Value"]
PART_COLUMNS = [
    "part_number", "brand", "part_type", "part_category", "description", "list_price",
    "status", "introduced_date", "superseded_by",
]
FITMENT_COLUMNS = ["part_number", "make", "model", "year_start", "year_end", "position", "note"]

MAKE_NOISE = {
    "CHEVROLET": ["Chevy", "chevrolet", " CHEVROLET"],
    "MERCEDES-BENZ": ["Mercedes Benz", "MERCEDES BENZ"],
    "VOLKSWAGEN": ["VW", "Volkswagen"],
    "LAND ROVER": ["LandRover", "Land Rover "],
    "TOYOTA": ["toyota", "Toyota"],
    "HONDA": ["Honda"],
}
UNFIXABLE_MAKES = ["CHEVORLET", "TOYTOA", "UNKNOWN", ""]


def _generations(years: list[int], rng: np.random.Generator) -> list[tuple[int, int]]:
    """Split a model's sorted model years into contiguous generations of 3-7 years."""
    runs, start = [], years[0]
    for prev, cur in zip(years, years[1:], strict=False):
        if cur != prev + 1:
            runs.append((start, prev))
            start = cur
    runs.append((start, years[-1]))
    out = []
    for lo, hi in runs:
        y = lo
        while y <= hi:
            span = int(rng.integers(3, 8))
            out.append((y, min(hi, y + span - 1)))
            y += span
    return out


def generate(
    vehicles: pd.DataFrame,
    seed: int = 7,
    revision: int = 1,
    defect_rate: float = 0.03,
    as_of_year: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (parts, fitment) feed frames for ``vehicles`` (make, model, model_year)."""
    rng = np.random.default_rng(seed)
    as_of_year = as_of_year or date.today().year
    models = (
        vehicles[["make", "model", "model_year"]]
        .drop_duplicates()
        .sort_values(["make", "model", "model_year"])
        .groupby(["make", "model"])["model_year"]
        .apply(list)
    )
    siblings = {make: sorted(group.index.get_level_values(1))
                for make, group in models.groupby(level=0)}

    parts, fitment, seq = [], [], 10_000
    for (make, model), years in models.items():
        depth = MAKE_DEPTH.get(make, 0.5)
        for pt in PART_TYPES:
            if rng.random() > min(0.95, pt.coverage * depth):
                continue
            for lo, hi in _generations(years, rng):
                # Aftermarket lag: the newest model years are often not yet covered.
                lag = int(rng.choice([1, 2, 3], p=[0.3, 0.45, 0.25]))
                hi = min(hi, as_of_year - lag)
                if hi < lo:
                    continue
                for position in pt.positions:
                    seq += 1
                    brand = BRAND_LINES[int(rng.random() < 0.3)]
                    price = round(float(pt.base_price * rng.lognormal(0, 0.25))
                                  * (0.72 if brand == "Apex Value" else 1.0), 2)
                    pn = f"{pt.code}{seq}"
                    intro = date(min(lo + lag, as_of_year), 1, 1) + timedelta(
                        days=int(rng.integers(0, 330)))
                    desc = f"{pt.name}" + ("" if position == "N/A" else f" - {position}")
                    parts.append([pn, brand, pt.name, pt.category, desc, price, "Active",
                                  intro.isoformat(), ""])
                    fitment.append([pn, make, model, lo, hi, position, ""])
                    # Interchange: the same part also fits a sibling model of the make.
                    pool = [m for m in siblings[make] if m != model]
                    if pool and rng.random() < 0.12:
                        twin = pool[int(rng.integers(0, len(pool)))]
                        twin_years = [y for y in models[(make, twin)] if lo <= y <= hi]
                        if twin_years:
                            fitment.append([pn, make, twin, min(twin_years), max(twin_years),
                                            position, "Interchange"])

    parts_df = pd.DataFrame(parts, columns=PART_COLUMNS)
    fit_df = pd.DataFrame(fitment, columns=FITMENT_COLUMNS)

    for rev in range(2, revision + 1):
        parts_df = _apply_revision(parts_df, np.random.default_rng(seed * 1000 + rev))

    parts_df, fit_df = _inject_defects(parts_df, fit_df, rng, defect_rate)
    return parts_df, fit_df


def _apply_revision(parts: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    parts = parts.copy()
    n = len(parts)
    repriced = rng.random(n) < 0.04
    parts.loc[repriced, "list_price"] = (
        parts.loc[repriced, "list_price"] * rng.uniform(1.03, 1.12, repriced.sum())
    ).round(2)
    active = parts["status"] == "Active"
    discontinued = active & (rng.random(n) < 0.01)
    parts.loc[discontinued, "status"] = "Discontinued"
    return parts


def _inject_defects(parts, fitment, rng, rate):
    """Corrupt a known fraction of rows in the ways real supplier feeds go wrong."""
    parts = parts.astype({"list_price": object}).copy()
    fitment = fitment.astype({"year_start": object, "year_end": object}).copy()
    nf, npart = len(fitment), len(parts)

    def pick(n, share):
        k = max(1, int(n * share))
        return rng.choice(n, size=min(k, n), replace=False)

    # Standardisable noise (fixed downstream, counted as "standardised").
    for i in pick(nf, rate * 1.5):
        make = fitment.at[i, "make"]
        variants = MAKE_NOISE.get(make, [make.title(), f" {make} "])
        fitment.at[i, "make"] = variants[int(rng.integers(0, len(variants)))]
    for i in pick(nf, rate * 0.5):
        fitment.at[i, "model"] = f"  {str(fitment.at[i, 'model']).lower()} "

    # Unfixable defects (quarantined).
    for i in pick(nf, rate * 0.2):
        fitment.at[i, "make"] = UNFIXABLE_MAKES[int(rng.integers(0, len(UNFIXABLE_MAKES)))]
    for i in pick(nf, rate * 0.2):
        fitment.at[i, "year_start"], fitment.at[i, "year_end"] = (
            fitment.at[i, "year_end"], fitment.at[i, "year_start"] - 3)
    for i in pick(nf, rate * 0.1):
        fitment.at[i, "year_end"] = rng.choice([2099, 1899, "20l5", ""])
    orphans = fitment.iloc[pick(nf, rate * 0.1)].copy()
    orphans["part_number"] = [f"ZZ{900000 + k}" for k in range(len(orphans))]
    dupes = fitment.iloc[pick(nf, rate * 0.3)].copy()
    fitment = pd.concat([fitment, orphans, dupes], ignore_index=True)

    for i in pick(npart, rate * 0.15):
        parts.at[i, "list_price"] = rng.choice([0, -12.5, "N/A"])
    for i in pick(npart, rate * 0.1):
        parts.at[i, "part_number"] = str(parts.at[i, "part_number"]).lower() + "-"
    for i in pick(npart, rate * 0.1):
        parts.at[i, "part_category"] = ""
    conflicting = parts.iloc[pick(npart, rate * 0.05)].copy()
    conflicting["list_price"] = [round(float(p) * 1.5, 2) if isinstance(p, float) else p
                                 for p in conflicting["list_price"]]
    parts = pd.concat([parts, conflicting], ignore_index=True)

    fitment = fitment.sample(frac=1.0, random_state=int(rng.integers(0, 2**31))).reset_index(
        drop=True)
    return parts, fitment


def write_feed(parts: pd.DataFrame, fitment: pd.DataFrame, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    parts_path, fit_path = out_dir / "parts.csv", out_dir / "fitment.csv"
    parts.to_csv(parts_path, index=False, quoting=csv.QUOTE_MINIMAL)
    fitment.to_csv(fit_path, index=False, quoting=csv.QUOTE_MINIMAL)
    log.info("catalog feed: %s parts, %s fitment rows -> %s",
             f"{len(parts):,}", f"{len(fitment):,}", out_dir)
    return parts_path, fit_path
