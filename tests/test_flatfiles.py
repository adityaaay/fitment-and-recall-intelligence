import zipfile
from dataclasses import replace

import pytest

from fitment_intel.config import FLAT_FILE_SOURCES, Settings
from fitment_intel.ingest.flatfiles import ingest_flat_files
from fitment_intel.warehouse import session

COMPLAINTS = next(s for s in FLAT_FILE_SOURCES if s.table == "nhtsa_complaints")


def _row(cmplid: str, extra_columns: int = 0) -> str:
    values = [""] * len(COMPLAINTS.columns)
    values[COMPLAINTS.columns.index("cmplid")] = cmplid
    values[COMPLAINTS.columns.index("odino")] = "11" + cmplid
    values[COMPLAINTS.columns.index("maketxt")] = "HONDA"
    values[COMPLAINTS.columns.index("city")] = "SPRINGFIELD"
    values[COMPLAINTS.columns.index("vin")] = "1HGCV2F38JA"
    values[COMPLAINTS.columns.index("vehicle_operator")] = "JANE DOE"
    values[COMPLAINTS.columns.index("prod_type")] = "V"
    return "\t".join(values + ["x"] * extra_columns)


def _write_fixture(settings: Settings, rows: list[str], name: str) -> None:
    folder = settings.fixtures_dir / "nhtsa"
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as zf:
        zf.writestr(COMPLAINTS.member, "\n".join(rows) + "\n")


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", warehouse_path=tmp_path / "wh.duckdb",
                    fixtures_dir=tmp_path / "fixtures")


def test_load_is_idempotent_and_drops_pii(settings):
    name = COMPLAINTS.url.rsplit("/", 1)[1]
    _write_fixture(settings, [_row("1"), _row("2"), _row("3")], name)
    with session(settings.warehouse_path) as con:
        first = ingest_flat_files(con, settings, sample=True, sources=[COMPLAINTS])
        second = ingest_flat_files(con, settings, sample=True, sources=[COMPLAINTS])
        assert first[0]["status"] == "loaded" and first[0]["rows"] == 3
        assert second[0]["status"] == "unchanged"
        assert con.execute("select count(*) from bronze.nhtsa_complaints").fetchone()[0] == 3
        cols = {r[0] for r in con.execute("describe bronze.nhtsa_complaints").fetchall()}
        assert not cols & COMPLAINTS.drop_columns
        assert {"cmplid", "odino", "maketxt", "_source", "_loaded_at"} <= cols


def test_changed_file_replaces_only_its_rows(settings):
    name = COMPLAINTS.url.rsplit("/", 1)[1]
    other = replace(COMPLAINTS, name="complaints_other", url=COMPLAINTS.url + "x")
    _write_fixture(settings, [_row("1"), _row("2")], name)
    _write_fixture(settings, [_row("9")], name + "x")
    with session(settings.warehouse_path) as con:
        ingest_flat_files(con, settings, sample=True, sources=[COMPLAINTS, other])
        _write_fixture(settings, [_row("1"), _row("2"), _row("4")], name)
        result = ingest_flat_files(con, settings, sample=True, sources=[COMPLAINTS, other])
        assert [r["status"] for r in result] == ["loaded", "unchanged"]
        by_source = dict(con.execute(
            "select _source, count(*) from bronze.nhtsa_complaints group by 1").fetchall())
        assert by_source == {COMPLAINTS.name: 3, "complaints_other": 1}


def test_schema_drift_is_tolerated_and_recorded(settings):
    name = COMPLAINTS.url.rsplit("/", 1)[1]
    _write_fixture(settings, [_row("1", extra_columns=2), _row("2", extra_columns=2)], name)
    with session(settings.warehouse_path) as con:
        ingest_flat_files(con, settings, sample=True, sources=[COMPLAINTS])
        seen, expected = con.execute(
            "select columns_seen, columns_expected from meta.ingest_manifest").fetchone()
        assert (seen, expected) == (len(COMPLAINTS.columns) + 2, len(COMPLAINTS.columns))
        assert con.execute("select count(*) from bronze.nhtsa_complaints").fetchone()[0] == 2
