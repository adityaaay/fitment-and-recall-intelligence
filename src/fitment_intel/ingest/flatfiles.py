"""Land NHTSA ODI flat files (recalls, complaints) into the bronze layer.

Design notes
------------
* Incremental by file: each source remembers its ETag/Last-Modified and content hash in
  ``meta.ingest_manifest``. A 304 or an identical hash skips the load entirely.
* Idempotent: a changed file replaces only its own rows in the bronze table
  (delete-by-``_source`` then insert, in one transaction), so re-runs never duplicate.
* Schema-drift tolerant: files are read positionally; extra trailing columns that NHTSA
  appends over time are ignored and counted, missing ones are padded with NULL.
* PII never lands: columns flagged in config are excluded from the insert.
"""

from __future__ import annotations

import logging
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from fitment_intel.config import FLAT_FILE_SOURCES, FlatFileSource, Settings
from fitment_intel.ingest.http import download, sha256_file
from fitment_intel.warehouse import last_manifest_entry, table_exists

log = logging.getLogger(__name__)

CSV_OPTIONS = (
    "delim='\\t', header=false, quote='', escape='', all_varchar=true, "
    "null_padding=true, strict_mode=false, sample_size=-1"
)


def _extract_member(zip_path: Path, member: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / member
    with zipfile.ZipFile(zip_path) as zf, zf.open(member) as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    return target


def _positional_select(con: duckdb.DuckDBPyConnection, txt: Path, src: FlatFileSource):
    """Build a SELECT that maps positional columns to spec names. Returns (sql, n_seen)."""
    relation = f"read_csv('{txt.as_posix()}', {CSV_OPTIONS})"
    seen = [row[0] for row in con.execute(f"describe select * from {relation}").fetchall()]
    exprs = []
    for idx, name in enumerate(src.columns):
        if name in src.drop_columns:
            continue
        if idx < len(seen):
            exprs.append(f'nullif(trim("{seen[idx]}"), \'\') as {name}')
        else:
            exprs.append(f"cast(null as varchar) as {name}")
    return f"select {', '.join(exprs)} from {relation}", len(seen)


def load_file(
    con: duckdb.DuckDBPyConnection,
    src: FlatFileSource,
    zip_path: Path,
    work_dir: Path,
    batch_id: str,
) -> tuple[int, int]:
    txt = _extract_member(zip_path, src.member, work_dir)
    try:
        select_sql, n_seen = _positional_select(con, txt, src)
        loaded_at = datetime.now(UTC).replace(tzinfo=None)
        con.execute("begin")
        if not table_exists(con, "bronze", src.table):
            con.execute(
                f"create table bronze.{src.table} as "
                f"select *, ''::varchar as _source, ''::varchar as _batch_id, "
                f"now()::timestamp as _loaded_at from ({select_sql}) limit 0"
            )
        con.execute(f"delete from bronze.{src.table} where _source = ?", [src.name])
        con.execute(
            f"insert into bronze.{src.table} by name "
            f"select *, ? as _source, ? as _batch_id, ?::timestamp as _loaded_at "
            f"from ({select_sql})",
            [src.name, batch_id, loaded_at],
        )
        rows = con.execute(
            f"select count(*) from bronze.{src.table} where _source = ?", [src.name]
        ).fetchone()[0]
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    finally:
        txt.unlink(missing_ok=True)
    if n_seen != len(src.columns):
        log.warning(
            "schema drift in %s: expected %d columns, file has %d",
            src.name, len(src.columns), n_seen,
        )
    return rows, n_seen


def _has_rows(con: duckdb.DuckDBPyConnection, src: FlatFileSource) -> bool:
    if not table_exists(con, "bronze", src.table):
        return False
    return bool(
        con.execute(
            f"select count(*) from bronze.{src.table} where _source = ?", [src.name]
        ).fetchone()[0]
    )


def _record(con, batch_id, src, etag, last_modified, sha, rows, n_seen, status) -> None:
    con.execute(
        "insert into meta.ingest_manifest values (?,?,?,?,?,?,?,?,?,?,?)",
        [
            batch_id, src.name, src.url, etag, last_modified, sha, rows, n_seen,
            len(src.columns), status, datetime.now(UTC).replace(tzinfo=None),
        ],
    )


def ingest_flat_files(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    sample: bool = False,
    force: bool = False,
    sources: list[FlatFileSource] | None = None,
) -> list[dict]:
    """Download (or, in sample mode, read fixtures for) each source and land it in bronze."""
    batch_id = uuid.uuid4().hex[:12]
    landing = settings.landing_dir / "nhtsa"
    work_dir = settings.landing_dir / "_work"
    summary = []
    for src in sources or FLAT_FILE_SOURCES:
        previous = None if force else last_manifest_entry(con, src.name)
        if previous and not _has_rows(con, src):
            previous = None  # manifest says loaded but bronze was reset: reload
        if sample:
            zip_path = settings.fixtures_dir / "nhtsa" / Path(src.url).name
            if not zip_path.exists():
                log.info("no fixture for %s, skipping", src.name)
                continue
            etag = last_modified = None
            sha = sha256_file(zip_path)
        else:
            result = download(
                src.url,
                landing / Path(src.url).name,
                etag=previous and previous["etag"],
                last_modified=previous and previous["last_modified"],
            )
            if result.not_modified:
                _record(con, batch_id, src, result.etag, result.last_modified, None, 0, 0,
                        "not_modified")
                summary.append({"source": src.name, "status": "not_modified", "rows": 0})
                continue
            zip_path, etag, last_modified, sha = (
                result.path, result.etag, result.last_modified, result.sha256
            )

        if previous and previous["sha256"] == sha:
            _record(con, batch_id, src, etag, last_modified, sha, 0, 0, "unchanged")
            summary.append({"source": src.name, "status": "unchanged", "rows": 0})
            continue

        rows, n_seen = load_file(con, src, zip_path, work_dir, batch_id)
        _record(con, batch_id, src, etag, last_modified, sha, rows, n_seen, "loaded")
        log.info("loaded %s: %s rows", src.name, f"{rows:,}")
        summary.append({"source": src.name, "status": "loaded", "rows": rows})
    return summary
