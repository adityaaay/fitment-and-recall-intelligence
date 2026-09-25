"""Standardise, validate and publish the catalog feed: nothing unvalidated reaches bronze.

Pipeline per feed:  raw CSV -> standardise -> contract (pandera, lazy) -> split
    * valid rows       -> bronze.catalog_parts / bronze.catalog_fitment
    * rejected rows    -> quarantine.catalog_parts / quarantine.catalog_fitment (+ reasons)
    * run metrics      -> meta.catalog_dq_runs (one row per feed per run)

Standardisation fixes what can be fixed deterministically (whitespace, casing, known make
aliases) and flags every row it touched, so "records standardised" is a measured number.
"""

from __future__ import annotations

import csv
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pandas as pd
import pandera.pandas as pa

from fitment_intel.config import PROJECT_ROOT

log = logging.getLogger(__name__)

SEEDS = PROJECT_ROOT / "dbt" / "seeds"
PART_NUMBER_PATTERN = r"^[A-Z]{2,4}\d{5,6}$"
PART_STATUSES = {"Active", "Discontinued", "Superseded"}
MIN_MODEL_YEAR = 1980


def load_make_aliases() -> dict[str, str]:
    with (SEEDS / "make_aliases.csv").open(newline="") as fh:
        return {row["raw_make"]: row["canonical_make"] for row in csv.DictReader(fh)}


def load_part_categories() -> set[str]:
    with (SEEDS / "component_taxonomy.csv").open(newline="") as fh:
        return {row["part_category"] for row in csv.DictReader(fh)
                if row["aftermarket_serviceable"] == "true"}


@dataclass
class FeedResult:
    feed: str
    valid: pd.DataFrame
    quarantined: pd.DataFrame
    metrics: dict = field(default_factory=dict)


def _to_int(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def _reasons_from(schema: pa.DataFrameSchema, df: pd.DataFrame) -> pd.Series:
    """Run the contract lazily and fold failure cases into a reason string per row."""
    reasons = pd.Series([[] for _ in range(len(df))], index=df.index, dtype=object)
    try:
        schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as err:
        cases = err.failure_cases.dropna(subset=["index"])
        for idx, check in zip(cases["index"], cases["check"], strict=True):
            if check not in reasons.at[idx]:
                reasons.at[idx].append(check)
    return reasons


def _check(fn, name: str) -> pa.Check:
    return pa.Check(fn, name=name, element_wise=False)


# --------------------------------------------------------------------------- parts
def validate_parts(raw: pd.DataFrame, categories: set[str]) -> FeedResult:
    df = raw.copy()
    df["part_number"] = df["part_number"].astype("string").str.strip()
    # Sentinels instead of NA: pandera skips nulls in nullable columns, and a missing
    # price or year must fail its check rather than slip through.
    df["list_price_num"] = pd.to_numeric(df["list_price"], errors="coerce").fillna(-1)

    schema = pa.DataFrameSchema(
        {
            "part_number": pa.Column(checks=_check(
                lambda s: s.fillna("").str.fullmatch(PART_NUMBER_PATTERN), "malformed_part_number"),
                nullable=True),
            "part_category": pa.Column(checks=_check(
                lambda s: s.isin(categories), "unknown_part_category"), nullable=True),
            "list_price_num": pa.Column(checks=_check(
                lambda s: s > 0, "invalid_price")),
            "status": pa.Column(checks=_check(
                lambda s: s.isin(PART_STATUSES), "invalid_status"), nullable=True),
        }
    )
    reasons = _reasons_from(schema, df)

    # First occurrence of a part number wins; later conflicting copies are quarantined.
    dup = df["part_number"].duplicated(keep="first")
    for idx in df.index[dup]:
        reasons.at[idx].append("duplicate_part_number")

    bad = reasons.map(bool)
    valid = df.loc[~bad].assign(list_price=df.loc[~bad, "list_price_num"]).drop(
        columns="list_price_num")
    valid["introduced_date"] = pd.to_datetime(valid["introduced_date"], errors="coerce").dt.date
    quarantined = raw.loc[bad].astype("string").assign(
        _reasons=reasons[bad].map(lambda r: ",".join(sorted(r))))
    return FeedResult("parts", valid, quarantined, _metrics(raw, valid, quarantined))


# ------------------------------------------------------------------------- fitment
def validate_fitment(
    raw: pd.DataFrame,
    valid_part_numbers: set[str],
    aliases: dict[str, str],
    as_of_year: int | None = None,
) -> FeedResult:
    max_year = (as_of_year or date.today().year) + 2
    df = raw.copy()

    # --- standardise -------------------------------------------------------------
    make_raw = df["make"].astype("string").fillna("")
    make_clean = make_raw.str.strip().str.upper().str.replace(r"\s+", " ", regex=True)
    df["make"] = make_clean.map(lambda m: aliases.get(m, m))
    model_raw = df["model"].astype("string").fillna("")
    model_clean = model_raw.str.strip().str.replace(r"\s+", " ", regex=True)
    df["model"] = model_clean
    df["make_standardized"] = df["make"] != make_raw
    df["model_standardized"] = df["model"] != model_raw
    df["year_start"] = _to_int(df["year_start"]).fillna(-1)
    df["year_end"] = _to_int(df["year_end"]).fillna(-1)
    df["position"] = df["position"].astype("string").fillna("N/A")
    df["part_number"] = df["part_number"].astype("string").str.strip()

    canonical = set(aliases.values())
    in_range = lambda s: s.between(MIN_MODEL_YEAR, max_year)  # noqa: E731
    schema = pa.DataFrameSchema(
        {
            "make": pa.Column(checks=_check(lambda s: s.isin(canonical), "unknown_make")),
            "model": pa.Column(checks=_check(lambda s: s.str.len() > 0, "missing_model")),
            "year_start": pa.Column(checks=_check(in_range, "invalid_year")),
            "year_end": pa.Column(checks=_check(in_range, "invalid_year")),
            "part_number": pa.Column(checks=_check(
                lambda s: s.isin(valid_part_numbers), "orphan_part_number"), nullable=True),
        },
        checks=[_check(lambda d: (d["year_start"] <= d["year_end"])
                       | (d["year_start"] < 0) | (d["year_end"] < 0), "reversed_year_range")],
    )
    reasons = _reasons_from(schema, df)
    bad = reasons.map(bool)

    valid = df.loc[~bad]
    key = ["part_number", "make", "model", "year_start", "year_end", "position"]
    dupes = valid.duplicated(subset=key, keep="first")
    deduped = int(dupes.sum())
    valid = valid.loc[~dupes].copy()
    valid["year_start"] = valid["year_start"].astype(int)
    valid["year_end"] = valid["year_end"].astype(int)

    quarantined = raw.loc[bad].astype("string").assign(
        _reasons=reasons[bad].map(lambda r: ",".join(sorted(r))))
    metrics = _metrics(raw, valid, quarantined)
    metrics["deduplicated"] = deduped
    metrics["standardized"] = int(
        (valid["make_standardized"] | valid["model_standardized"]).sum())
    return FeedResult("fitment", valid, quarantined, metrics)


def _metrics(raw, valid, quarantined) -> dict:
    counts: dict[str, int] = {}
    for reasons in quarantined.get("_reasons", pd.Series(dtype="string")):
        for reason in str(reasons).split(","):
            counts[reason] = counts.get(reason, 0) + 1
    return {
        "rows_in": len(raw),
        "rows_valid": len(valid),
        "rows_quarantined": len(quarantined),
        "standardized": 0,
        "deduplicated": 0,
        "reasons": counts,
    }


# ------------------------------------------------------------------------- publish
DQ_DDL = """
create table if not exists meta.catalog_dq_runs (
    run_id varchar, feed varchar, rows_in bigint, rows_valid bigint,
    rows_quarantined bigint, standardized bigint, deduplicated bigint,
    reason varchar, reason_count bigint, run_at timestamp
)
"""


def _replace(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame) -> None:
    con.register("_frame", frame)
    con.execute(f"create or replace table {table} as select * from _frame")
    con.unregister("_frame")


def publish(con: duckdb.DuckDBPyConnection, feed_dir: Path) -> list[FeedResult]:
    run_id = uuid.uuid4().hex[:12]
    run_at = datetime.now(UTC).replace(tzinfo=None)
    read = lambda name: pd.read_csv(feed_dir / name, dtype=str, keep_default_na=False)  # noqa: E731

    parts = validate_parts(read("parts.csv"), load_part_categories())
    fitment = validate_fitment(
        read("fitment.csv"), set(parts.valid["part_number"]), load_make_aliases())

    loaded_at = run_at
    con.execute("begin")
    try:
        _replace(con, "bronze.catalog_parts", parts.valid.assign(_loaded_at=loaded_at))
        _replace(con, "bronze.catalog_fitment", fitment.valid.assign(_loaded_at=loaded_at))
        _replace(con, "quarantine.catalog_parts", parts.quarantined.assign(_run_id=run_id))
        _replace(con, "quarantine.catalog_fitment", fitment.quarantined.assign(_run_id=run_id))
        con.execute(DQ_DDL)
        for result in (parts, fitment):
            m = result.metrics
            rows = [(r, c) for r, c in sorted(m["reasons"].items())] or [(None, 0)]
            for reason, count in rows:
                con.execute(
                    "insert into meta.catalog_dq_runs values (?,?,?,?,?,?,?,?,?,?)",
                    [run_id, result.feed, m["rows_in"], m["rows_valid"],
                     m["rows_quarantined"], m["standardized"], m["deduplicated"],
                     reason, count, run_at],
                )
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    for result in (parts, fitment):
        m = result.metrics
        log.info("%s: %s in, %s valid, %s quarantined, %s standardised, %s deduplicated",
                 result.feed, m["rows_in"], m["rows_valid"], m["rows_quarantined"],
                 m["standardized"], m["deduplicated"])
    return [parts, fitment]
