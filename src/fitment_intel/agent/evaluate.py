"""Execution-accuracy evaluation of the NL-to-SQL analyst.

Each golden case pairs a question with a reference SQL query. A case passes when the
result of the analyst's final query contains the reference result: same row count, and
every reference column matches some result column value-for-value (order-insensitive,
floats rounded). Extra columns in the analyst's answer are allowed - they are often useful
context - but missing or wrong values are not.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import yaml

from fitment_intel.agent.analyst import Analyst
from fitment_intel.agent.sql_guard import ReadOnlyWarehouse
from fitment_intel.config import PROJECT_ROOT, Settings

log = logging.getLogger(__name__)

GOLDEN_PATH = PROJECT_ROOT / "evals" / "nl2sql_golden.yml"


def _column_signature(series: pd.Series) -> list:
    values = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            values.append("<null>")
        elif isinstance(v, bool):
            values.append(str(v))
        elif isinstance(v, int | float):
            values.append(round(float(v), 2))
        else:
            values.append(str(v).strip().upper())
    return sorted(values, key=lambda x: (str(type(x)), x))


def results_match(expected: pd.DataFrame, actual: pd.DataFrame | None) -> bool:
    if actual is None or len(expected) != len(actual):
        return False
    remaining = [_column_signature(actual[c]) for c in actual.columns]
    for column in expected.columns:
        signature = _column_signature(expected[column])
        if signature in remaining:
            remaining.remove(signature)
        else:
            return False
    return True


@dataclass
class CaseResult:
    case_id: str
    question: str
    passed: bool
    seconds: float
    turns: int
    queries: int
    input_tokens: int
    output_tokens: int
    sql: str | None
    error: str | None = None


@dataclass
class EvalReport:
    model: str
    results: list[CaseResult] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return sum(r.passed for r in self.results) / max(len(self.results), 1)

    def summary(self) -> str:
        lines = [f"model: {self.model}",
                 f"execution accuracy: {self.accuracy:.1%} "
                 f"({sum(r.passed for r in self.results)}/{len(self.results)})"]
        if self.results:
            secs = sorted(r.seconds for r in self.results)
            mean_turns = sum(r.turns for r in self.results) / len(self.results)
            lines.append(f"median latency: {secs[len(secs) // 2]:.1f}s, "
                         f"mean turns: {mean_turns:.1f}")
        for r in self.results:
            lines.append(f"  [{'PASS' if r.passed else 'FAIL'}] {r.case_id}: {r.question}"
                         + (f"  ({r.error})" if r.error else ""))
        return "\n".join(lines)


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]


def run_eval(settings: Settings, limit: int | None = None, analyst: Analyst | None = None,
             cases: list[dict] | None = None) -> EvalReport:
    analyst = analyst or Analyst.from_warehouse(settings)
    reference = ReadOnlyWarehouse(settings.warehouse_path, max_rows=10_000)
    cases = (cases or load_golden())[:limit]
    report = EvalReport(model=analyst.model)
    for case in cases:
        expected = reference.query(case["sql"]).frame
        try:
            answer = analyst.ask(case["question"])
            passed, error = results_match(expected, answer.frame), None
        except Exception as exc:  # one broken case must not abort the run
            log.exception("case %s failed", case["id"])
            answer, passed, error = None, False, repr(exc)[:200]
        report.results.append(CaseResult(
            case_id=case["id"], question=case["question"], passed=passed,
            seconds=answer.seconds if answer else 0.0, turns=answer.turns if answer else 0,
            queries=len(answer.queries) if answer else 0,
            input_tokens=answer.input_tokens if answer else 0,
            output_tokens=answer.output_tokens if answer else 0,
            sql=answer.sql if answer else None, error=error))
        log.info("%s %s", "PASS" if passed else "FAIL", case["id"])

    out = PROJECT_ROOT / "evals" / "results"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (out / f"eval_{stamp}.json").write_text(json.dumps({
        "model": report.model, "accuracy": report.accuracy,
        "results": [r.__dict__ for r in report.results]}, indent=2), encoding="utf-8")
    return report
