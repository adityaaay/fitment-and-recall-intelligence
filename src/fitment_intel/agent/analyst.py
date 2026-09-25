"""Natural-language analyst: Claude answers catalog and safety questions by writing SQL
against the marts, executing it through the read-only sandbox, and reading the results.

The loop is deliberately small and explicit (no framework): Claude may call ``run_sql``
several times - to explore values, fix an error, or refine - and then answers in prose.
The last successful query is returned with the answer so every number is auditable.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import pandas as pd

from fitment_intel.agent.schema_context import build_schema_context
from fitment_intel.agent.sql_guard import ReadOnlyWarehouse, UnsafeQuery
from fitment_intel.config import Settings

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TURNS = 8
PREVIEW_ROWS = 40

SYSTEM_PROMPT = """\
You are the analytics assistant for an automotive aftermarket parts manufacturer. Product \
managers ask you about vehicle safety recalls, owner complaints filed with NHTSA, and the \
company's parts catalog coverage.

Answer by querying the DuckDB warehouse with the run_sql tool. Base every number you \
report on a query result from this conversation; if the data cannot answer the question, \
say so plainly instead of estimating.

Warehouse conventions that matter for correct numbers:
- Count complaints with count(distinct complaint_id); a complaint can cite several components.
- Count recalls with count(distinct campaign_number). dim_recall_campaign.units_affected is \
per campaign - never sum it across vehicles or components.
- Makes are upper case ('HONDA', 'MERCEDES-BENZ'). Match models with model_norm (upper \
case, punctuation replaced by spaces, e.g. 'F 150', 'CR V') or with ilike on model_name.
- Part categories are shared by dim_component.part_category and dim_part.part_category.
- If you are unsure of exact values (a make, model or category spelling), query distinct \
values first rather than guessing.

Keep the final answer short: lead with the direct answer, then a compact table or 2-4 \
bullets of supporting figures. Do not restate the SQL; it is shown to the user separately.

The warehouse schema (all tables live in the `marts` schema):

{schema}
"""

RUN_SQL_TOOL = {
    "name": "run_sql",
    "description": (
        "Execute one read-only DuckDB SELECT query against the warehouse and return up to "
        f"{PREVIEW_ROWS} rows as CSV, plus the total row count. Use fully qualified table "
        "names such as marts.dim_vehicle. Errors are returned as text so you can fix the query."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single SELECT or WITH query."},
        },
        "required": ["sql"],
        "additionalProperties": False,
    },
    "strict": True,
}


@dataclass
class Answer:
    question: str
    text: str
    sql: str | None = None
    frame: pd.DataFrame | None = None
    queries: list[str] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    seconds: float = 0.0
    stop_reason: str | None = None


def _format_result(result) -> str:
    frame = result.frame
    preview = frame.head(PREVIEW_ROWS).to_csv(index=False)
    total = f"{len(frame)}+" if result.truncated else str(len(frame))
    note = f"rows: {total}"
    if len(frame) > PREVIEW_ROWS:
        note += f" (showing first {PREVIEW_ROWS})"
    return f"{note}\n{preview}"


class Analyst:
    def __init__(self, client, warehouse: ReadOnlyWarehouse, schema_context: str,
                 model: str | None = None):
        self.client = client
        self.warehouse = warehouse
        self.model = model or os.environ.get("FITMENT_CLAUDE_MODEL", DEFAULT_MODEL)
        self.system = [{
            "type": "text",
            "text": SYSTEM_PROMPT.format(schema=schema_context),
            "cache_control": {"type": "ephemeral"},  # schema prefix is identical every call
        }]

    @classmethod
    def from_warehouse(cls, settings: Settings, client=None) -> Analyst:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        context = build_schema_context(
            settings.warehouse_path, settings.dbt_project_dir / "target" / "manifest.json")
        return cls(client, ReadOnlyWarehouse(settings.warehouse_path), context)

    def _create(self, messages: list[dict]):
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=self.system,
            tools=[RUN_SQL_TOOL],
            messages=messages,
            thinking={"type": "adaptive"},
            # If a safety classifier declines (e.g. a false positive on "crash" or "fire"
            # wording), the API retries on Anthropic's recommended fallback model.
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def ask(self, question: str) -> Answer:
        started = time.perf_counter()
        answer = Answer(question=question, text="")
        messages: list[dict] = [{"role": "user", "content": question}]

        for turn in range(1, MAX_TURNS + 1):
            response = self._create(messages)
            answer.turns = turn
            usage = response.usage
            answer.input_tokens += usage.input_tokens or 0
            answer.output_tokens += usage.output_tokens or 0
            answer.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
            answer.stop_reason = response.stop_reason

            if response.stop_reason == "refusal":
                answer.text = "The model declined to answer this request."
                break
            if response.stop_reason == "max_tokens":
                answer.text = "The response hit the output limit before finishing."
                break

            tool_calls = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_calls:
                answer.text = "\n".join(
                    b.text for b in response.content if b.type == "text").strip()
                break

            messages.append({"role": "assistant", "content": response.content})
            results = []
            for call in tool_calls:
                sql = call.input.get("sql", "") if isinstance(call.input, dict) else ""
                answer.queries.append(sql)
                try:
                    result = self.warehouse.query(sql)
                    content, is_error = _format_result(result), False
                    answer.sql, answer.frame = result.sql, result.frame
                except UnsafeQuery as exc:
                    content, is_error = f"Query rejected: {exc}", True
                except Exception as exc:  # DuckDB binder/parser errors go back to Claude
                    content, is_error = f"Query failed: {exc}", True
                log.debug("run_sql error=%s\n%s", is_error, sql)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                "content": content, "is_error": is_error})
            messages.append({"role": "user", "content": results})
        else:
            answer.text = answer.text or "Stopped after the maximum number of query attempts."

        answer.seconds = round(time.perf_counter() - started, 2)
        return answer
