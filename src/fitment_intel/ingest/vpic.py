"""Build the canonical vehicle universe from NHTSA's vPIC API.

Two request shapes keep the call count low (~ makes x (years + 3)):
  * GetModelsForMakeYear/make/{make}/modelyear/{year}   -> which models exist per year
  * GetModelsForMakeYear/make/{make}/vehicletype/{type} -> each model's vehicle type

vPIC sits behind a WAF that blocks bursty clients, so requests go through a global
rate limiter (FITMENT_VPIC_RPS, default 1.5/s) with bounded concurrency, and 403/429/5xx
responses back off exponentially. Responses are cached as JSON in the landing zone; a
re-run only calls the API for cache misses and for model years that can still change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

import duckdb
import httpx
import pandas as pd
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from fitment_intel.config import LIGHT_VEHICLE_MAKES, VPIC_BASE_URL, VPIC_VEHICLE_TYPES, Settings
from fitment_intel.ingest.http import TIMEOUT, USER_AGENT

log = logging.getLogger(__name__)

# vPIC lists some models under several types (e.g. Accord also as MPV); precedence
# car > MPV > truck matches how the aftermarket catalogs them in the common cases.
TYPE_PRIORITY = {"Passenger Car": 1, "Multipurpose Passenger Vehicle (MPV)": 2, "Truck": 3}


class RateLimiter:
    """Spaces request start times at least 1/rps seconds apart across all tasks."""

    def __init__(self, rps: float):
        self.interval = 1.0 / rps
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            await asyncio.sleep(delay)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (403, 429) or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception(_retryable),
    wait=wait_exponential_jitter(initial=5, max=120),
    stop=stop_after_attempt(6),
    reraise=True,
)
async def _fetch(client: httpx.AsyncClient, limiter: RateLimiter, url: str) -> dict:
    await limiter.wait()
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


def _slug(*parts: object) -> str:
    text = "_".join(str(p) for p in parts).lower()
    return "".join(ch if ch.isalnum() else "_" for ch in text) + ".json"


def year_job(make: str, year: int, cache_dir: Path) -> tuple[str, Path]:
    url = f"{VPIC_BASE_URL}/GetModelsForMakeYear/make/{quote(make)}/modelyear/{year}?format=json"
    return url, cache_dir / "years" / _slug(make, year)


def type_job(make: str, vtype: str, cache_dir: Path) -> tuple[str, Path]:
    url = (f"{VPIC_BASE_URL}/GetModelsForMakeYear/make/{quote(make)}"
           f"/vehicletype/{quote(vtype)}?format=json")
    return url, cache_dir / "types" / _slug(make, vtype)


async def _fetch_all(jobs: list[tuple[str, Path]], concurrency: int, rps: float) -> None:
    semaphore = asyncio.Semaphore(concurrency)
    limiter = RateLimiter(rps)
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:

        async def run(url: str, path: Path) -> None:
            async with semaphore:
                payload = await _fetch(client, limiter, url)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload), encoding="utf-8")

        await asyncio.gather(*(run(url, path) for url, path in jobs))


def collect(settings: Settings, cache_dir: Path) -> int:
    this_year = date.today().year
    jobs = []
    for make in LIGHT_VEHICLE_MAKES:
        for year in range(settings.first_model_year, settings.last_model_year + 1):
            url, path = year_job(make, year, cache_dir)
            if not path.exists() or year >= this_year:
                jobs.append((url, path))
        for vtype in VPIC_VEHICLE_TYPES:
            url, path = type_job(make, vtype, cache_dir)
            if not path.exists():
                jobs.append((url, path))
    if jobs:
        rps = float(os.environ.get("FITMENT_VPIC_RPS", "1.5"))
        log.info("vPIC: %d requests at <= %.1f req/s", len(jobs), rps)
        asyncio.run(_fetch_all(jobs, min(settings.http_concurrency, 2), rps))
    return len(jobs)


def _results(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")).get("Results") or []


def to_frame(cache_dir: Path) -> pd.DataFrame:
    types: dict[int, str] = {}
    for path in sorted((cache_dir / "types").glob("*.json")):
        for item in _results(path):
            vtype, model_id = item.get("VehicleTypeName"), item.get("Model_ID")
            current = types.get(model_id)
            if vtype and (current is None or TYPE_PRIORITY.get(vtype, 9) < TYPE_PRIORITY.get(
                    current, 9)):
                types[model_id] = vtype

    rows = []
    for path in sorted((cache_dir / "years").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        criteria = payload.get("SearchCriteria") or ""
        year = next((p.split(":")[1] for p in criteria.split(" | ")
                     if p.startswith("ModelYear")), None)
        for item in payload.get("Results") or []:
            rows.append({
                "make_id": item.get("Make_ID"),
                "make_name": item.get("Make_Name"),
                "model_id": item.get("Model_ID"),
                "model_name": item.get("Model_Name"),
                "model_year": int(year) if year else None,
                # Models vPIC does not classify as car/truck/MPV (motorcycles, buses,
                # incomplete vehicles) are out of the light-vehicle scope.
                "vehicle_type": types.get(item.get("Model_ID")),
            })
    frame = pd.DataFrame(rows, columns=["make_id", "make_name", "model_id", "model_name",
                                        "model_year", "vehicle_type"])
    # vPIC's make filter is a substring match ("VOLVO" also returns "VOLVO TRUCK"), so
    # keep exact make names only.
    exact = frame["make_name"].str.upper().isin(LIGHT_VEHICLE_MAKES)
    return frame[exact & frame["vehicle_type"].notna()].reset_index(drop=True)


def ingest_vpic(con: duckdb.DuckDBPyConnection, settings: Settings, sample: bool = False) -> int:
    if sample:
        cache_dir = settings.fixtures_dir / "vpic"
    else:
        cache_dir = settings.landing_dir / "vpic"
        collect(settings, cache_dir)
    frame = to_frame(cache_dir)
    frame["_loaded_at"] = datetime.now(UTC).replace(tzinfo=None)
    con.register("vpic_frame", frame)
    con.execute("create or replace table bronze.vpic_models as select * from vpic_frame")
    con.unregister("vpic_frame")
    log.info("vPIC: %s model-year rows", f"{len(frame):,}")
    return len(frame)
