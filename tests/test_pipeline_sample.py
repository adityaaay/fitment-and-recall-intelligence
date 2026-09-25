"""End to end: bundled NHTSA extracts -> bronze -> validated catalog -> dbt build.

Runs the real CLI stages against a temporary warehouse, so every dbt model, snapshot and
data test (including the reconciliation tests) executes on real-shaped data.
"""

import duckdb
import pytest

from fitment_intel import cli
from fitment_intel.config import get_settings
from fitment_intel.transform.dbt_runner import run_dbt

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("pipeline")
    mp = pytest.MonkeyPatch()
    mp.setenv("FITMENT_DATA_DIR", str(root / "data"))
    mp.setenv("FITMENT_WAREHOUSE", str(root / "wh.duckdb"))
    settings = get_settings()
    cli.cmd_run(settings, sample=True, force=False, full_refresh=True)
    yield settings
    mp.undo()


def _q(settings, sql):
    # Same configuration as dbt-duckdb's in-process connection (DuckDB refuses a second
    # connection to one file with different options inside a process).
    con = duckdb.connect(str(settings.warehouse_path))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_every_dbt_node_succeeded(built):
    rows = _q(built, """
        select status, count(*) from meta.dbt_run_results
        where invocation_id = (select invocation_id from meta.dbt_run_results
                               order by recorded_at desc limit 1)
        group by 1
    """)
    statuses = dict(rows)
    assert set(statuses) <= {"success", "pass", "warn", "no-op"}, statuses  # no-op: exposure
    assert statuses.get("pass", 0) >= 40  # data tests


def test_marts_are_populated_and_join(built):
    (complaints, vehicles, gaps, fitments), = _q(built, """
        select (select count(*) from marts.fct_complaint),
               (select count(*) from marts.dim_vehicle),
               (select count(*) from marts.mart_coverage_gap),
               (select count(*) from marts.fct_part_fitment)
    """)
    assert complaints > 1000 and vehicles > 100 and gaps > 100 and fitments > 1000
    (uncovered,), = _q(built, """
        select count(*) from marts.mart_coverage_gap
        where coverage_status = 'uncovered' and opportunity_score > 0
    """)
    assert uncovered > 0


def test_pipeline_stages_recorded(built):
    stages = [r[0] for r in _q(built, "select stage from meta.pipeline_runs order by finished_at")]
    assert stages == ["ingest", "catalog", "transform"]


def test_rerun_is_incremental_and_idempotent(built):
    before = _q(built, "select count(*) from marts.fct_complaint")[0][0]
    summary = cli.cmd_ingest(built, sample=True, force=False)
    assert {f["status"] for f in summary["flat_files"]} == {"unchanged"}
    run_dbt(built, "build", "--select", "fct_complaint+")
    assert _q(built, "select count(*) from marts.fct_complaint")[0][0] == before


def test_catalog_revision_creates_scd2_history(built):
    cli.cmd_catalog(built, revision=2)
    run_dbt(built, "build", "--select", "snap_catalog_parts+")
    (versioned, repriced), = _q(built, """
        select count(*) filter (where version_count > 1),
               (select count(distinct part_number) from snapshots.snap_catalog_parts
                where dbt_valid_to is not null)
        from marts.dim_part
    """)
    assert versioned > 0 and versioned == repriced
