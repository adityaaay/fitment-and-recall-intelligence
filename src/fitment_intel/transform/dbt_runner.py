"""Run dbt in-process and persist its results into the warehouse for observability."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

import pandas as pd

from fitment_intel.config import Settings
from fitment_intel.warehouse import session

log = logging.getLogger(__name__)


class DbtFailure(RuntimeError):
    pass


def run_dbt(settings: Settings, *args: str) -> None:
    """Invoke ``dbt <args>`` against this project's warehouse; raise on failure."""
    from dbt.cli.main import dbtRunner  # imported lazily: dbt start-up is slow

    os.environ["FITMENT_WAREHOUSE"] = str(settings.warehouse_path)
    project = str(settings.dbt_project_dir)
    cli_args = [*args, "--project-dir", project, "--profiles-dir", project]
    log.info("dbt %s", " ".join(args))
    result = dbtRunner().invoke(cli_args)
    if args and args[0] in {"build", "test", "run", "snapshot", "seed"}:
        record_run_results(settings, args[0])
    if not result.success:
        raise DbtFailure(f"dbt {' '.join(args)} failed: {result.exception or 'see log above'}")


def record_run_results(settings: Settings, command: str) -> int:
    path = settings.dbt_project_dir / "target" / "run_results.json"
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    invocation = payload["metadata"]["invocation_id"]
    rows = [
        {
            "invocation_id": invocation,
            "command": command,
            "unique_id": r["unique_id"],
            "resource_type": r["unique_id"].split(".")[0],
            "status": r["status"],
            "failures": r.get("failures"),
            "execution_time_s": round(r.get("execution_time") or 0.0, 3),
            "message": (r.get("message") or "")[:500],
            "recorded_at": datetime.now(UTC).replace(tzinfo=None),
        }
        for r in payload["results"]
    ]
    frame = pd.DataFrame(rows)
    with session(settings.warehouse_path) as con:
        con.register("_results", frame)
        con.execute(
            "create table if not exists meta.dbt_run_results as select * from _results limit 0"
        )
        con.execute("insert into meta.dbt_run_results by name select * from _results")
        con.unregister("_results")
    return len(rows)
