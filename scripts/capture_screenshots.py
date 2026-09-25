"""Capture README screenshots of the running dashboard (docs/img/*.png).

Requires `pip install playwright` and a Chromium-based browser (uses the installed
Microsoft Edge on Windows, or `playwright install chromium` elsewhere):

    fitment dashboard            # in one terminal
    python scripts/capture_screenshots.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8501"
OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
PAGES = {
    "home": "/",
    "coverage_gaps": "/Coverage_Gaps",
    "vehicle_explorer": "/Vehicle_Explorer",
    "data_quality": "/Data_Quality",
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    channel = "msedge" if sys.platform == "win32" else None
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=channel)
        page = browser.new_page(viewport={"width": 1440, "height": 900},
                                device_scale_factor=1.5, color_scheme="light")
        for name, path in PAGES.items():
            page.goto(BASE + path, wait_until="networkidle")
            page.wait_for_selector("[data-testid='stAppViewContainer']")
            # Streamlit renders in several passes; wait until the run indicator is gone.
            try:
                page.wait_for_function(
                    "!document.querySelector('[data-testid=\"stStatusWidget\"]')",
                    timeout=20_000)
            except PlaywrightTimeout:
                pass  # the widget can linger after the run; fall through to a fixed wait
            page.wait_for_timeout(4000)
            page.screenshot(path=OUT / f"{name}.png", full_page=True)
            print("saved", OUT / f"{name}.png")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
