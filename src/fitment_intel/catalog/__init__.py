"""Synthetic aftermarket catalog: generate the supplier feed, then validate and publish it."""

from __future__ import annotations

import logging

import duckdb

from fitment_intel.catalog.generator import generate, write_feed
from fitment_intel.catalog.validate import publish
from fitment_intel.config import LIGHT_VEHICLE_MAKES, Settings

log = logging.getLogger(__name__)


def build_catalog(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    revision: int = 1,
    seed: int = 7,
    as_of_year: int | None = None,
) -> dict:
    vehicles = con.execute(
        """
        select distinct upper(trim(make_name)) as make, trim(model_name) as model,
               cast(model_year as integer) as model_year
        from bronze.vpic_models
        where model_name is not null and upper(trim(make_name)) in ?
        order by 1, 2, 3
        """,
        [LIGHT_VEHICLE_MAKES],
    ).df()
    parts, fitment = generate(vehicles, seed=seed, revision=revision, as_of_year=as_of_year)
    write_feed(parts, fitment, settings.catalog_dir)
    results = publish(con, settings.catalog_dir)
    return {r.feed: r.metrics for r in results}
