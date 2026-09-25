"""Central configuration: paths, source definitions and the in-scope vehicle universe.

Everything that changes between environments comes from environment variables so the
same code runs locally, in CI (sample mode) and on a scheduled full refresh.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _path_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).resolve() if value else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: _path_env("FITMENT_DATA_DIR", PROJECT_ROOT / "data")
    )
    warehouse_path: Path = field(
        default_factory=lambda: _path_env(
            "FITMENT_WAREHOUSE", PROJECT_ROOT / "data" / "warehouse" / "fitment.duckdb"
        )
    )
    dbt_project_dir: Path = PROJECT_ROOT / "dbt"
    fixtures_dir: Path = PROJECT_ROOT / "tests" / "fixtures"
    first_model_year: int = int(os.environ.get("FITMENT_FIRST_MODEL_YEAR", "2008"))
    last_model_year: int = int(os.environ.get("FITMENT_LAST_MODEL_YEAR", "2026"))
    http_concurrency: int = int(os.environ.get("FITMENT_HTTP_CONCURRENCY", "8"))

    @property
    def landing_dir(self) -> Path:
        return self.data_dir / "landing"

    @property
    def catalog_dir(self) -> Path:
        return self.data_dir / "landing" / "catalog"


def get_settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# NHTSA Office of Defects Investigation flat files (public domain, refreshed daily).
# Field layouts: https://static.nhtsa.gov/odi/ffdd/rcl/RCL.txt and .../cmpl/CMPL.txt
# ---------------------------------------------------------------------------

RECALL_COLUMNS = [
    "record_id", "campno", "maketxt", "modeltxt", "yeartxt", "mfgcampno", "compname",
    "mfgname", "bgman", "endman", "rcltypecd", "potaff", "odate", "influenced_by",
    "mfgtxt", "rcdate", "datea", "rpno", "fmvss", "desc_defect", "consequence_defect",
    "corrective_action", "notes", "rcl_cmpt_id", "mfr_comp_name", "mfr_comp_desc",
    "mfr_comp_ptno", "do_not_drive", "park_outside",
]

COMPLAINT_COLUMNS = [
    "cmplid", "odino", "mfr_name", "maketxt", "modeltxt", "yeartxt", "crash", "faildate",
    "fire", "injured", "deaths", "compdesc", "city", "state", "vin", "datea", "ldate",
    "miles", "occurences", "cdescr", "cmpl_type", "police_rpt_yn", "purch_dt",
    "orig_owner_yn", "anti_brakes_yn", "cruise_cont_yn", "num_cyls", "drive_train",
    "fuel_sys", "fuel_type", "trans_type", "veh_speed", "dot", "tire_size", "loc_of_tire",
    "tire_fail_type", "orig_equip_yn", "manuf_dt", "seat_type", "restraint_type",
    "dealer_name", "dealer_tel", "dealer_city", "dealer_state", "dealer_zip", "prod_type",
    "repaired_yn", "medical_attn", "vehicles_towed_yn", "state_of_incident",
    "vehicle_operator",
]

# Personal or quasi-identifying fields. They are dropped at landing and never persisted
# in any warehouse layer - governance by construction rather than by access control.
COMPLAINT_PII_COLUMNS = frozenset(
    {"city", "vin", "dealer_name", "dealer_tel", "dealer_city", "dealer_state",
     "dealer_zip", "vehicle_operator"}
)


@dataclass(frozen=True)
class FlatFileSource:
    name: str
    url: str
    member: str
    table: str
    columns: list[str]
    drop_columns: frozenset[str] = frozenset()

    @property
    def keep_columns(self) -> list[str]:
        return [c for c in self.columns if c not in self.drop_columns]


_ODI = "https://static.nhtsa.gov/odi/ffdd"

FLAT_FILE_SOURCES: list[FlatFileSource] = [
    FlatFileSource(
        name="recalls_post_2010",
        url=f"{_ODI}/rcl/FLAT_RCL_POST_2010.zip",
        member="FLAT_RCL_POST_2010.txt",
        table="nhtsa_recalls",
        columns=RECALL_COLUMNS,
    ),
    *[
        FlatFileSource(
            name=f"complaints_{window.replace('-', '_')}",
            url=f"{_ODI}/cmpl/COMPLAINTS_RECEIVED_{window}.zip",
            member=f"COMPLAINTS_RECEIVED_{window}.txt",
            table="nhtsa_complaints",
            columns=COMPLAINT_COLUMNS,
            drop_columns=COMPLAINT_PII_COLUMNS,
        )
        for window in ("2015-2019", "2020-2024", "2025-2026")
    ],
]

VPIC_BASE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles"
VPIC_VEHICLE_TYPES = ["passenger car", "truck", "multipurpose passenger vehicle (mpv)"]

# US light-vehicle makes in scope. vPIC is queried per make/year/type; the canonical
# spelling here must match vPIC's Make_Name (aliases live in dbt/seeds/make_aliases.csv).
LIGHT_VEHICLE_MAKES = [
    "ACURA", "ALFA ROMEO", "AUDI", "BMW", "BUICK", "CADILLAC", "CHEVROLET", "CHRYSLER",
    "DODGE", "FIAT", "FORD", "GENESIS", "GMC", "HONDA", "HYUNDAI", "INFINITI", "JAGUAR",
    "JEEP", "KIA", "LAND ROVER", "LEXUS", "LINCOLN", "MAZDA", "MERCEDES-BENZ", "MINI",
    "MITSUBISHI", "NISSAN", "PORSCHE", "RAM", "SUBARU", "TESLA", "TOYOTA", "VOLKSWAGEN",
    "VOLVO",
]
