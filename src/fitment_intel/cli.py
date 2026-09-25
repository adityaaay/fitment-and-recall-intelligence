"""Command line entry point: ``fitment <command>``.

    fitment run [--sample]        ingest -> catalog -> dbt build (the whole pipeline)
    fitment ingest [--sample]     land NHTSA flat files + vPIC into bronze
    fitment catalog [--revision]  generate, validate and publish the catalog feed
    fitment transform             dbt build (seeds, snapshot, models, tests)
    fitment ask "question"        ask the warehouse in plain English (needs Claude API access)
    fitment eval                  score the NL-to-SQL analyst against the golden question set
    fitment dashboard             launch the Streamlit app
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from fitment_intel.config import PROJECT_ROOT, Settings, get_settings
from fitment_intel.warehouse import session

log = logging.getLogger("fitment")

PIPELINE_RUNS_DDL = """
create table if not exists meta.pipeline_runs (
    run_id varchar, stage varchar, status varchar, seconds double,
    detail varchar, finished_at timestamp
)
"""


def _stage(settings: Settings, run_id: str, name: str, fn: Callable[[], object]) -> object:
    started = time.perf_counter()
    status, detail = "success", ""
    try:
        result = fn()
        detail = str(result)[:1000] if result is not None else ""
        return result
    except Exception as exc:
        status, detail = "failed", repr(exc)[:1000]
        raise
    finally:
        seconds = round(time.perf_counter() - started, 2)
        log.info("stage %-10s %-7s %6.1fs", name, status, seconds)
        with session(settings.warehouse_path) as con:
            con.execute(PIPELINE_RUNS_DDL)
            con.execute(
                "insert into meta.pipeline_runs values (?,?,?,?,?,?)",
                [run_id, name, status, seconds, detail,
                 datetime.now(UTC).replace(tzinfo=None)],
            )


def cmd_ingest(settings: Settings, sample: bool, force: bool) -> dict:
    from fitment_intel.ingest.flatfiles import ingest_flat_files
    from fitment_intel.ingest.vpic import ingest_vpic

    with session(settings.warehouse_path) as con:
        files = ingest_flat_files(con, settings, sample=sample, force=force)
        vpic_rows = ingest_vpic(con, settings, sample=sample)
    return {"vpic_rows": vpic_rows, "flat_files": files}


def cmd_catalog(settings: Settings, revision: int) -> dict:
    from fitment_intel.catalog import build_catalog

    with session(settings.warehouse_path) as con:
        return build_catalog(con, settings, revision=revision)


def cmd_transform(settings: Settings, full_refresh: bool) -> None:
    from fitment_intel.transform.dbt_runner import run_dbt

    args = ["build"] + (["--full-refresh"] if full_refresh else [])
    run_dbt(settings, *args)


def cmd_run(settings: Settings, sample: bool, force: bool, full_refresh: bool) -> None:
    run_id = uuid.uuid4().hex[:12]
    _stage(settings, run_id, "ingest", lambda: cmd_ingest(settings, sample, force))
    _stage(settings, run_id, "catalog", lambda: cmd_catalog(settings, revision=1))
    _stage(settings, run_id, "transform", lambda: cmd_transform(settings, full_refresh))


def cmd_ask(settings: Settings, question: str) -> None:
    from fitment_intel.agent.analyst import Analyst

    answer = Analyst.from_warehouse(settings).ask(question)
    print(answer.text)
    if answer.sql:
        print("\n-- SQL\n" + answer.sql)


def cmd_eval(settings: Settings, limit: int | None) -> None:
    from fitment_intel.agent.evaluate import run_eval

    report = run_eval(settings, limit=limit)
    print(report.summary())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fitment", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="full pipeline")
    p_ing = sub.add_parser("ingest", help="land sources into bronze")
    for p in (p_run, p_ing):
        p.add_argument("--sample", action="store_true",
                       help="use the bundled fixture extracts instead of downloading")
        p.add_argument("--force", action="store_true", help="reload even if unchanged")
    p_run.add_argument("--full-refresh", action="store_true")

    p_cat = sub.add_parser("catalog", help="generate + validate the catalog feed")
    p_cat.add_argument("--revision", type=int, default=1)

    p_tr = sub.add_parser("transform", help="dbt build")
    p_tr.add_argument("--full-refresh", action="store_true")

    p_ask = sub.add_parser("ask", help="ask the warehouse a question")
    p_ask.add_argument("question")

    p_eval = sub.add_parser("eval", help="evaluate the NL-to-SQL analyst")
    p_eval.add_argument("--limit", type=int)

    sub.add_parser("dashboard", help="launch the Streamlit app")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = get_settings()

    if args.command == "run":
        cmd_run(settings, args.sample, args.force, args.full_refresh)
    elif args.command == "ingest":
        print(cmd_ingest(settings, args.sample, args.force))
    elif args.command == "catalog":
        print(cmd_catalog(settings, args.revision))
    elif args.command == "transform":
        cmd_transform(settings, args.full_refresh)
    elif args.command == "ask":
        cmd_ask(settings, args.question)
    elif args.command == "eval":
        cmd_eval(settings, args.limit)
    elif args.command == "dashboard":
        app = PROJECT_ROOT / "app" / "Home.py"
        # Run from the project root so Streamlit picks up .streamlit/config.toml.
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)],
                               cwd=PROJECT_ROOT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
