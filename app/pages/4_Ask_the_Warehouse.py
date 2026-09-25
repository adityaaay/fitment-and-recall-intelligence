import os

import streamlit as st
from common import SETTINGS, page_header, require_warehouse

st.set_page_config(page_title="Ask the Warehouse", page_icon="🔧", layout="wide")
page_header("Ask the warehouse",
            "Plain-English questions answered by Claude writing read-only SQL against the "
            "marts - grounded in the dbt documentation, with every query shown.")
require_warehouse()

EXAMPLES = [
    "Which 10 vehicles had the most owner complaints about the engine in the last 3 years?",
    "How many recall campaigns carried a do-not-drive advisory each year since 2018?",
    "For 2016-2019 Hyundai and Kia models, which part categories have complaints but no "
    "catalog coverage?",
    "What share of Ford F-150 complaints involve a crash or fire, by model year?",
]


@st.cache_resource(show_spinner=False)
def get_analyst():
    from fitment_intel.agent.analyst import Analyst

    return Analyst.from_warehouse(SETTINGS)


has_credentials = any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                                                   "ANTHROPIC_PROFILE"))
if not has_credentials:
    st.info("Set `ANTHROPIC_API_KEY` (or sign in with `ant auth login` and set "
            "`ANTHROPIC_PROFILE`) before launching the app to enable this page.")

st.write("Try one:")
cols = st.columns(len(EXAMPLES))
for col, example in zip(cols, EXAMPLES, strict=True):
    if col.button(example, use_container_width=True):
        st.session_state["question"] = example

question = st.text_area("Question", key="question", height=90,
                        placeholder="e.g. Which Toyota models have the most brake complaints?")
if st.button("Ask", type="primary", disabled=not question):
    with st.spinner("Querying the warehouse..."):
        try:
            answer = get_analyst().ask(question)
        except Exception as exc:
            st.error(f"The analyst could not run: {exc}")
            st.stop()
    st.markdown(answer.text)
    if answer.frame is not None:
        st.dataframe(answer.frame, hide_index=True, use_container_width=True)
    if answer.sql:
        with st.expander("SQL"):
            st.code(answer.sql, language="sql")
    st.caption(f"{answer.turns} model turns · {len(answer.queries)} queries · "
               f"{answer.seconds:.1f}s · {answer.input_tokens:,} input tokens "
               f"({answer.cache_read_tokens:,} from cache) · {answer.output_tokens:,} output")
