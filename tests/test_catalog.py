import pandas as pd
import pytest

from fitment_intel.catalog.generator import PART_TYPES, generate
from fitment_intel.catalog.validate import (
    load_make_aliases,
    load_part_categories,
    validate_fitment,
    validate_parts,
)


@pytest.fixture(scope="module")
def vehicles():
    rows = []
    for make, models in {
        "HONDA": ["Civic", "Accord", "CR-V", "Pilot"],
        "CHEVROLET": ["Silverado 1500", "Equinox", "Malibu"],
        "TOYOTA": ["Camry", "RAV4", "Tacoma"],
        "MERCEDES-BENZ": ["C-Class", "GLC"],
    }.items():
        for model in models:
            rows += [(make, model, year) for year in range(2008, 2027)]
    return pd.DataFrame(rows, columns=["make", "model", "model_year"])


@pytest.fixture(scope="module")
def feed(vehicles):
    return generate(vehicles, seed=11, as_of_year=2026, defect_rate=0.05)


def test_generator_is_deterministic(vehicles, feed):
    parts, fitment = generate(vehicles, seed=11, as_of_year=2026, defect_rate=0.05)
    pd.testing.assert_frame_equal(parts, feed[0])
    pd.testing.assert_frame_equal(fitment, feed[1])


def test_part_categories_exist_in_taxonomy():
    assert {pt.category for pt in PART_TYPES} <= load_part_categories()


def test_aftermarket_lag_leaves_newest_years_uncovered(feed):
    _, fitment = feed
    years = pd.to_numeric(fitment["year_end"], errors="coerce")
    assert years[years < 2100].max() <= 2025  # lag of at least one year behind 2026


def test_validation_catches_injected_defects(feed):
    parts, fitment = feed
    parts_result = validate_parts(parts, load_part_categories())
    fit_result = validate_fitment(fitment, set(parts_result.valid["part_number"]),
                                  load_make_aliases(), as_of_year=2026)

    part_reasons = set(parts_result.metrics["reasons"])
    assert {"invalid_price", "malformed_part_number", "unknown_part_category",
            "duplicate_part_number"} <= part_reasons
    fit_reasons = set(fit_result.metrics["reasons"])
    assert {"unknown_make", "reversed_year_range", "invalid_year",
            "orphan_part_number"} <= fit_reasons

    # Everything that survived is clean.
    assert parts_result.valid["part_number"].is_unique
    assert (parts_result.valid["list_price"] > 0).all()
    valid = fit_result.valid
    assert (valid["year_start"] <= valid["year_end"]).all()
    assert set(valid["make"]) <= set(load_make_aliases().values())
    assert set(valid["part_number"]) <= set(parts_result.valid["part_number"])
    key = ["part_number", "make", "model", "year_start", "year_end", "position"]
    assert not valid.duplicated(subset=key).any()

    # Nothing is lost: every input row is either valid, quarantined or a removed duplicate.
    m = fit_result.metrics
    assert m["rows_in"] == m["rows_valid"] + m["rows_quarantined"] + m["deduplicated"]
    assert m["standardized"] > 0 and m["deduplicated"] > 0


def test_alias_standardisation():
    raw = pd.DataFrame({
        "part_number": ["IC10001"] * 4,
        "make": ["Chevy", " mercedes benz ", "VW", "TOYTOA"],
        "model": ["Malibu", "C-Class", " Jetta  ", "Camry"],
        "year_start": ["2015"] * 4,
        "year_end": ["2018"] * 4,
        "position": ["N/A"] * 4,
        "note": [""] * 4,
    })
    result = validate_fitment(raw, {"IC10001"}, load_make_aliases(), as_of_year=2026)
    assert result.valid["make"].tolist() == ["CHEVROLET", "MERCEDES-BENZ", "VOLKSWAGEN"]
    assert result.valid["model"].tolist() == ["Malibu", "C-Class", "Jetta"]
    assert result.valid["make_standardized"].all()
    assert result.quarantined["_reasons"].tolist() == ["unknown_make"]


def test_revision_reprices_some_parts_only(vehicles, feed):
    rev2, _ = generate(vehicles, seed=11, as_of_year=2026, defect_rate=0.05, revision=2)
    merged = feed[0].merge(rev2, on="part_number", suffixes=("_r1", "_r2"))
    merged = merged[merged.part_number.duplicated(keep=False) == False]  # noqa: E712
    changed = merged["list_price_r1"].astype(str) != merged["list_price_r2"].astype(str)
    assert 0 < changed.mean() < 0.15
