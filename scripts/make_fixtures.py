"""Cut the small, offline fixture extracts used by `fitment run --sample` and CI.

Reads the raw NHTSA zips and vPIC cache that a full `fitment ingest` leaves in the landing
zone and writes byte-compatible extracts to tests/fixtures/:

* a handful of makes and model years, all of their recall rows,
* a deterministic sample of complaints per file, keeping every component row of a
  sampled complaint (so the complaint x component grain is preserved),
* personal / quasi-identifying columns blanked and narratives shortened.

    python scripts/make_fixtures.py
"""

from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path

from fitment_intel.config import FLAT_FILE_SOURCES, get_settings

MAKES = {"HONDA", "FORD", "TOYOTA", "HYUNDAI", "KIA", "CHEVROLET", "TESLA"}
YEARS = range(2014, 2023)
COMPLAINTS_PER_FILE = 1500
NARRATIVE_CHARS = 280


def _keep_complaint(odino: str, rate: float) -> bool:
    bucket = int(hashlib.md5(odino.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < rate


def _in_scope(fields: list[str], idx: dict[str, int]) -> bool:
    if len(fields) <= idx["yeartxt"]:
        return False
    year = fields[idx["yeartxt"]]
    return (fields[idx["maketxt"]].strip().upper() in MAKES
            and year.isdigit() and int(year) in YEARS)


def cut_flat_files(landing: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for src in FLAT_FILE_SOURCES:
        zip_path = landing / "nhtsa" / Path(src.url).name
        if not zip_path.exists():
            print(f"skip {src.name}: {zip_path} not found")
            continue
        idx = {name: i for i, name in enumerate(src.columns)}
        with zipfile.ZipFile(zip_path) as zf, zf.open(src.member) as fh:
            lines = [raw.decode("utf-8").rstrip("\r\n").split("\t") for raw in fh]

        rows = [f for f in lines if _in_scope(f, idx)]
        if src.table == "nhtsa_complaints":
            complaints = {f[idx["odino"]] for f in rows}
            rate = min(1.0, COMPLAINTS_PER_FILE / max(len(complaints), 1))
            rows = [f for f in rows if _keep_complaint(f[idx["odino"]], rate)]
            for f in rows:
                for col in src.drop_columns:
                    if idx[col] < len(f):
                        f[idx[col]] = ""
                f[idx["cdescr"]] = f[idx["cdescr"]][:NARRATIVE_CHARS]
        else:
            for col in ("desc_defect", "consequence_defect", "corrective_action", "notes"):
                if idx[col] < len(rows[0]):
                    for f in rows:
                        f[idx[col]] = f[idx[col]][:NARRATIVE_CHARS]

        target = out / Path(src.url).name
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(src.member, "".join("\t".join(f) + "\n" for f in rows))
        print(f"{src.name}: {len(rows):,} rows -> {target.name} "
              f"({target.stat().st_size / 1024:.0f} KB)")


def cut_vpic(landing: Path, out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    slugs = {m.lower().replace("-", "_").replace(" ", "_") for m in MAKES}
    copied = 0
    for sub in ("years", "types"):
        (out / sub).mkdir(parents=True)
        for path in sorted((landing / "vpic" / sub).glob("*.json")):
            make_part = path.stem.rsplit("_", 1)[0] if sub == "years" else None
            if sub == "years":
                year = int(path.stem.rsplit("_", 1)[1])
                if make_part not in slugs or year not in YEARS:
                    continue
            elif not any(path.stem.startswith(s + "_") for s in slugs):
                continue
            shutil.copy2(path, out / sub / path.name)
            copied += 1
    print(f"vpic: {copied} cached responses -> {out}")


def main() -> None:
    settings = get_settings()
    cut_flat_files(settings.landing_dir, settings.fixtures_dir / "nhtsa")
    cut_vpic(settings.landing_dir, settings.fixtures_dir / "vpic")


if __name__ == "__main__":
    main()
