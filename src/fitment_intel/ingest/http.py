"""HTTP primitives shared by the ingestion jobs: retries with backoff and conditional GETs."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger(__name__)

USER_AGENT = "fitment-intel/1.0 (+https://github.com/adityaaay)"
TIMEOUT = httpx.Timeout(30.0, read=300.0)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


retrying = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential_jitter(initial=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)


@dataclass
class DownloadResult:
    path: Path | None
    not_modified: bool
    etag: str | None
    last_modified: str | None
    sha256: str | None


@retrying
def download(
    url: str,
    dest: Path,
    etag: str | None = None,
    last_modified: str | None = None,
) -> DownloadResult:
    """Stream ``url`` to ``dest``; sends validators so an unchanged file costs one 304."""
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    digest = hashlib.sha256()
    with httpx.stream("GET", url, headers=headers, timeout=TIMEOUT, follow_redirects=True) as r:
        if r.status_code == 304:
            log.info("not modified: %s", url)
            return DownloadResult(None, True, etag, last_modified, None)
        r.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                fh.write(chunk)
                digest.update(chunk)
        new_etag = r.headers.get("etag")
        new_last_modified = r.headers.get("last-modified")
    tmp.replace(dest)
    return DownloadResult(dest, False, new_etag, new_last_modified, digest.hexdigest())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
