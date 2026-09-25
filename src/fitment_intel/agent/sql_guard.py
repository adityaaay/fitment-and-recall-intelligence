"""Read-only, sandboxed SQL execution for LLM-generated queries.

Defence in depth - any one layer is enough to stop a write, together they also stop
exfiltration and runaway queries:
  1. Static check: exactly one statement, SELECT/WITH only, no DDL/DML/admin keywords.
  2. The DuckDB connection is opened read_only with external access disabled and the
     configuration locked, so file/network table functions and SET are unavailable.
  3. Results are capped at ``max_rows`` and queries are interrupted after ``timeout_s``.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|create|drop|alter|truncate|copy|attach|detach|export|"
    r"import|install|load|pragma|set|reset|call|checkpoint|vacuum|grant|revoke|use)\b",
    re.IGNORECASE,
)
COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
STRINGS = re.compile(r"'(?:[^']|'')*'")


class UnsafeQuery(ValueError):
    pass


def check_sql(sql: str) -> str:
    """Return the cleaned statement or raise UnsafeQuery."""
    body = COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    if not body:
        raise UnsafeQuery("empty query")
    code_only = STRINGS.sub("''", body)  # keywords inside string literals are fine
    if ";" in code_only:
        raise UnsafeQuery("only a single statement is allowed")
    if not re.match(r"^\s*(select|with|from)\b", code_only, re.IGNORECASE):
        raise UnsafeQuery("only SELECT queries are allowed")
    match = FORBIDDEN.search(code_only)
    if match:
        raise UnsafeQuery(f"keyword not allowed: {match.group(0).upper()}")
    return body


@dataclass
class QueryResult:
    sql: str
    frame: pd.DataFrame
    truncated: bool


class ReadOnlyWarehouse:
    def __init__(self, path: Path, max_rows: int = 500, timeout_s: float = 30.0):
        self.path = path
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(
            str(self.path),
            read_only=True,
            config={"enable_external_access": False, "lock_configuration": True},
        )

    def query(self, sql: str) -> QueryResult:
        body = check_sql(sql)
        con = self._connect()
        timer = threading.Timer(self.timeout_s, con.interrupt)
        timer.start()
        try:
            frame = con.execute(
                f"select * from ({body}) as q limit {self.max_rows + 1}"
            ).df()
        except duckdb.InterruptException as exc:
            raise UnsafeQuery(f"query exceeded {self.timeout_s:.0f}s and was cancelled") from exc
        finally:
            timer.cancel()
            con.close()
        truncated = len(frame) > self.max_rows
        return QueryResult(body, frame.head(self.max_rows), truncated)
