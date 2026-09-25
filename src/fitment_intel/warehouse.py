"""Thin helpers around the DuckDB warehouse file."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import duckdb

SCHEMAS = ("bronze", "meta", "quarantine")

MANIFEST_DDL = """
create table if not exists meta.ingest_manifest (
    batch_id        varchar,
    source          varchar,
    url             varchar,
    etag            varchar,
    last_modified   varchar,
    sha256          varchar,
    rows_loaded     bigint,
    columns_seen    integer,
    columns_expected integer,
    status          varchar,   -- loaded | unchanged | not_modified
    loaded_at       timestamp
)
"""


def connect(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


@contextmanager
def session(path: Path):
    con = connect(path)
    try:
        for schema in SCHEMAS:
            con.execute(f"create schema if not exists {schema}")
        con.execute("set enable_progress_bar = false")
        con.execute(MANIFEST_DDL)
        yield con
    finally:
        con.close()


def last_manifest_entry(con: duckdb.DuckDBPyConnection, source: str) -> dict | None:
    row = con.execute(
        """
        select etag, last_modified, sha256
        from meta.ingest_manifest
        where source = ? and status = 'loaded'
        order by loaded_at desc
        limit 1
        """,
        [source],
    ).fetchone()
    if row is None:
        return None
    return {"etag": row[0], "last_modified": row[1], "sha256": row[2]}


def table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    return bool(
        con.execute(
            "select count(*) from information_schema.tables where table_schema=? and table_name=?",
            [schema, table],
        ).fetchone()[0]
    )
