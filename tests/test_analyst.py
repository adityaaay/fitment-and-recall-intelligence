"""The agent loop is tested against a scripted fake client - no network, no API key."""

from types import SimpleNamespace

import duckdb
import pandas as pd
import pytest

from fitment_intel.agent.analyst import FALLBACK_BETA, Analyst
from fitment_intel.agent.evaluate import results_match
from fitment_intel.agent.sql_guard import ReadOnlyWarehouse


def _usage():
    return SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=80)


def tool_call(call_id, sql):
    block = SimpleNamespace(type="tool_use", id=call_id, name="run_sql", input={"sql": sql})
    return SimpleNamespace(stop_reason="tool_use", content=[block], usage=_usage())


def final(text):
    return SimpleNamespace(stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text=text)], usage=_usage())


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


@pytest.fixture
def warehouse(tmp_path):
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute("""create table marts.fct_complaint as
                   select * from (values (1, 'HONDA'), (2, 'HONDA'), (3, 'FORD')) t(id, make)""")
    con.close()
    return ReadOnlyWarehouse(path)


def test_loop_recovers_from_errors_and_returns_last_good_query(warehouse):
    client = FakeClient([
        tool_call("t1", "select nope from marts.fct_complaint"),        # binder error
        tool_call("t2", "drop table marts.fct_complaint"),              # rejected by guard
        tool_call("t3", "select make, count(*) as n from marts.fct_complaint "
                        "group by make order by n desc"),
        final("HONDA leads with 2 complaints."),
    ])
    answer = Analyst(client, warehouse, "schema").ask("Which make has most complaints?")

    assert answer.text == "HONDA leads with 2 complaints."
    assert answer.turns == 4 and len(answer.queries) == 3
    assert answer.frame.to_dict("records") == [{"make": "HONDA", "n": 2}, {"make": "FORD", "n": 1}]
    assert "group by make" in answer.sql
    assert answer.input_tokens == 400 and answer.cache_read_tokens == 320

    # Errors went back to the model as is_error tool results, in order.
    results = [m["content"][0] for m in client.requests[-1]["messages"] if m["role"] == "user"
               and isinstance(m["content"], list)]
    assert [r["is_error"] for r in results] == [True, True, False]
    assert "Query rejected" in results[1]["content"]
    assert results[2]["content"].startswith("rows: 2")


def test_request_shape(warehouse):
    client = FakeClient([final("ok")])
    Analyst(client, warehouse, "SCHEMA-TEXT", model="claude-opus-5").ask("hi")
    req = client.requests[0]
    assert req["model"] == "claude-opus-5"
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert req["thinking"] == {"type": "adaptive"}
    assert "SCHEMA-TEXT" in req["system"][0]["text"]
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["tools"][0]["name"] == "run_sql"


def test_refusal_is_reported(warehouse):
    refusal = SimpleNamespace(stop_reason="refusal", content=[], usage=_usage())
    answer = Analyst(FakeClient([refusal]), warehouse, "s").ask("q")
    assert "declined" in answer.text and answer.sql is None


def test_results_match_semantics():
    expected = pd.DataFrame({"make": ["HONDA", "FORD"], "n": [2, 1]})
    reordered_with_extra = pd.DataFrame({"n": [1, 2], "make": ["ford", "honda"],
                                         "share": [0.33, 0.67]})
    assert results_match(expected, reordered_with_extra)
    assert not results_match(expected, pd.DataFrame({"make": ["HONDA", "FORD"], "n": [2, 2]}))
    assert not results_match(expected, expected.head(1))
    assert results_match(pd.DataFrame({"x": [1.0049]}), pd.DataFrame({"y": [1.0]}))
    assert not results_match(expected, None)
