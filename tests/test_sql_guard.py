import duckdb
import pytest

from fitment_intel.agent.sql_guard import ReadOnlyWarehouse, UnsafeQuery, check_sql


@pytest.mark.parametrize(
    "sql",
    [
        "select 1",
        "  WITH x AS (select 1 as a) select a from x;",
        "select 'drop table x; delete' as note",  # keywords inside strings are data
        "-- leading comment\nselect make from marts.dim_vehicle",
        "select * from t order by 1 offset 5",
    ],
)
def test_accepts_read_queries(sql):
    assert check_sql(sql)


@pytest.mark.parametrize(
    "sql, reason",
    [
        ("", "empty"),
        ("drop table marts.dim_vehicle", "only SELECT"),
        ("select 1; select 2", "single statement"),
        ("select 1; drop table x", "single statement"),
        ("with x as (select 1) delete from y", "DELETE"),
        ("select * from t /* sneaky */; attach 'x.db'", "single statement"),
        ("select 1 from t where exists (select 1) union all select * from t; copy t to 'f'",
         "single statement"),
        ("select 1 as a, (pragma version) as b", "PRAGMA"),
        ("insert into t select 1", "only SELECT"),
    ],
)
def test_rejects_unsafe(sql, reason):
    with pytest.raises(UnsafeQuery, match=reason):
        check_sql(sql)


@pytest.fixture
def warehouse(tmp_path):
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute("create table marts.t as select range as n from range(1000)")
    con.close()
    return ReadOnlyWarehouse(path, max_rows=100)


def test_row_cap_and_truncation_flag(warehouse):
    result = warehouse.query("select n from marts.t order by n")
    assert len(result.frame) == 100
    assert result.truncated


def test_connection_is_read_only_even_if_guard_is_bypassed(warehouse):
    con = warehouse._connect()
    try:
        with pytest.raises(duckdb.Error):
            con.execute("create table marts.x (a int)")
    finally:
        con.close()


def test_external_access_is_disabled(warehouse, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n")
    with pytest.raises(duckdb.Error):
        warehouse.query(f"select * from read_csv('{secret.as_posix()}')")
