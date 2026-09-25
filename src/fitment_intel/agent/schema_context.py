"""Semantic context for the analyst, generated from dbt docs + the live warehouse schema.

The model/column descriptions written once in dbt YAML are the single source of truth:
they document the warehouse for humans *and* ground the LLM, so the two cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

EXPOSED_SCHEMA = "marts"


def _manifest_docs(manifest_path: Path) -> dict[str, dict]:
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs = {}
    for node in manifest.get("nodes", {}).values():
        if node.get("resource_type") == "model" and node.get("schema") == EXPOSED_SCHEMA:
            docs[node["name"]] = {
                "description": " ".join((node.get("description") or "").split()),
                "columns": {
                    name: " ".join((col.get("description") or "").split())
                    for name, col in node.get("columns", {}).items()
                },
            }
    return docs


def build_schema_context(warehouse: Path, manifest_path: Path) -> str:
    docs = _manifest_docs(manifest_path)
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        columns = con.execute(
            """
            select table_name, column_name, data_type
            from information_schema.columns
            where table_schema = ?
            order by table_name, ordinal_position
            """,
            [EXPOSED_SCHEMA],
        ).fetchall()
        counts = {
            t: con.execute(f"select count(*) from {EXPOSED_SCHEMA}.{t}").fetchone()[0]
            for t in sorted({c[0] for c in columns})
        }
    finally:
        con.close()

    by_table: dict[str, list[tuple[str, str]]] = {}
    for table, column, dtype in columns:
        by_table.setdefault(table, []).append((column, dtype))

    lines = []
    for table, cols in by_table.items():
        doc = docs.get(table, {})
        lines.append(f"### {EXPOSED_SCHEMA}.{table}  (~{counts[table]:,} rows)")
        if doc.get("description"):
            lines.append(doc["description"])
        for column, dtype in cols:
            desc = doc.get("columns", {}).get(column, "")
            lines.append(f"- {column} {dtype}" + (f": {desc}" if desc else ""))
        lines.append("")
    return "\n".join(lines).strip()
