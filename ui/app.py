from __future__ import annotations

import os
from html import escape
from pathlib import Path

import httpx
import streamlit as st

from connectors.base import Settings

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"
settings = Settings.from_yaml(CONFIG_PATH)
API_HOST = os.getenv("API_HOST", "localhost")
API_BASE = f"http://{API_HOST}:{settings.api.port}"
DEFAULT_TOP_K = min(max(settings.ui.results_per_page, 1), 20)

st.set_page_config(
    page_title=settings.ui.page_title,
    page_icon="🔎",
    layout="wide",
)

st.markdown(
    """
    <style>
    :root {
        --pse-ink: #172033;
        --pse-muted: #697586;
        --pse-accent: #4f46e5;
        --pse-border: #e6e9f0;
        --pse-surface: #ffffff;
        --pse-bg: #f7f8fb;
    }
    html, body, [data-testid="stAppViewContainer"], .stApp {
        background: var(--pse-bg) !important;
        color: var(--pse-ink);
    }
    .stApp { direction: ltr; }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stSidebar"] {
        direction: ltr;
        background: var(--pse-surface);
        border-right: 1px solid var(--pse-border);
    }
    [data-testid="stSidebar"] > div:first-child { padding-top: 1.25rem; }
    .block-container { max-width: 1080px; padding: 2rem 2rem 4rem; }
    h1, h2, h3 {
        color: var(--pse-ink) !important;
        letter-spacing: -0.035em;
    }
    h1 { font-weight: 700; }
    [data-testid="stMarkdownContainer"] p { color: var(--pse-muted) !important; }
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p { color: var(--pse-muted) !important; }
    [data-testid="stMetricLabel"], [data-testid="stMetricValue"] { text-align: left; }
    [data-testid="stMetric"] {
        padding: 0.75rem;
        background: #f8f9fc;
        border: 1px solid var(--pse-border);
        border-radius: 0.8rem;
        box-shadow: none;
    }
    [data-testid="stMetricLabel"] { color: var(--pse-muted); }
    [data-testid="stTabs"] [role="tablist"] {
        gap: 1.25rem;
        border-bottom: 1px solid var(--pse-border);
    }
    [data-testid="stTabs"] button[role="tab"] {
        padding: 0.8rem 0.15rem;
        color: var(--pse-muted) !important;
        border-radius: 0;
    }
    [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
        color: var(--pse-accent) !important;
        font-weight: 600;
    }
    [data-testid="stTabs"] button[role="tab"][aria-selected="true"] p {
        color: var(--pse-accent) !important;
    }
    [data-testid="stForm"] {
        padding: 1.1rem 1.2rem 0.45rem;
        background: var(--pse-surface);
        border: 1px solid var(--pse-border);
        border-radius: 1rem;
        box-shadow: 0 8px 24px rgba(23, 32, 51, 0.04);
    }
    [data-testid="stVerticalBlockBorderWrapper"] {
        border-color: var(--pse-border);
        border-radius: 0.95rem;
        background: var(--pse-surface);
    }
    [data-testid="stTextInput"] input {
        min-height: 3rem;
        color: var(--pse-ink) !important;
        border: 1px solid #dfe3ec !important;
        border-radius: 0.7rem !important;
        background: #fff !important;
        -webkit-text-fill-color: var(--pse-ink);
    }
    [data-testid="stTextInput"] input::placeholder {
        color: #8993a3 !important;
        -webkit-text-fill-color: #8993a3;
    }
    [data-testid="stTextInput"] input:focus {
        border-color: var(--pse-accent) !important;
        box-shadow: 0 0 0 1px var(--pse-accent) !important;
    }
    [data-testid="stBaseButton-primary"] {
        color: #fff !important;
        border: 1px solid var(--pse-accent) !important;
        border-radius: 0.7rem !important;
        background: var(--pse-accent) !important;
        box-shadow: none !important;
        font-weight: 600;
    }
    [data-testid="stBaseButton-primary"]:hover {
        background: #4338ca !important;
        border-color: #4338ca !important;
    }
    [data-testid="stBaseButton-secondary"] {
        border: 1px solid var(--pse-border);
        border-radius: 0.7rem;
    }
    [data-testid="stChatMessage"] {
        border: 1px solid var(--pse-border);
        border-radius: 0.9rem;
        background: var(--pse-surface);
        box-shadow: none;
    }
    [data-testid="stExpander"] {
        border: 1px solid var(--pse-border);
        border-radius: 0.75rem;
        background: #fff;
    }
    .pse-eyebrow {
        margin: 0 0 0.45rem;
        color: var(--pse-accent) !important;
        font-size: 0.76rem;
        font-weight: 700;
        letter-spacing: 0.12em;
        text-transform: uppercase;
    }
    .pse-intro { max-width: 650px; color: var(--pse-muted) !important; font-size: 1.03rem; }
    .pse-result-content { color: #354052 !important; line-height: 1.75; overflow-wrap: anywhere; }
    .pse-sidebar-title { margin-bottom: 0; font-size: 1.25rem; font-weight: 700; color: var(--pse-ink) !important; }
    .pse-sidebar-copy { margin-top: 0.25rem; color: var(--pse-muted) !important; font-size: 0.9rem; }
    @media (max-width: 700px) {
        .block-container { padding: 1.2rem 1rem 3rem; }
        [data-testid="stForm"] { padding: 0.9rem 0.8rem 0.35rem; }
        [data-testid="stTabs"] [role="tablist"] { gap: 0.8rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def _request_error(action: str, error: httpx.HTTPError) -> str:
    detail = ""
    if isinstance(error, httpx.HTTPStatusError):
        try:
            body = error.response.json()
            if isinstance(body, dict):
                detail = str(body.get("detail", ""))
        except ValueError:
            detail = error.response.text
    if not detail:
        detail = str(error)
    return f"{action}: {detail}"


def call_search(query: str, top_k: int) -> list[dict]:
    response = httpx.post(
        f"{API_BASE}/search",
        json={"query": query, "top_k": top_k},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


def call_chat(query: str, top_k: int) -> dict:
    response = httpx.post(
        f"{API_BASE}/chat",
        json={"query": query, "top_k": top_k},
        timeout=300.0,
    )
    response.raise_for_status()
    return response.json()


def get_stats() -> dict:
    response = httpx.get(f"{API_BASE}/stats", timeout=10.0)
    response.raise_for_status()
    return response.json()


def call_index(force: bool) -> list[dict]:
    response = httpx.post(
        f"{API_BASE}/index",
        json={"force": force},
        timeout=1800.0,
    )
    response.raise_for_status()
    return response.json()


def _format_source_path(value: str) -> str:
    return escape(str(value)).replace("\n", " ")


def _clean_content(value: str) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) > 500:
        text = text[:497].rstrip() + "..."
    return text


def render_result_card(item: dict, rank: int | None = None) -> None:
    with st.container(border=True):
        title_col, rank_col = st.columns([5, 1])
        with title_col:
            st.markdown(f"**{escape(str(item.get('title', 'Untitled')))}**")
        if rank is not None:
            with rank_col:
                st.caption(f"#{rank}")
        source_type = escape(str(item.get("source_type", "unknown")))
        source_path = _format_source_path(item.get("source_path", ""))
        st.caption(f"{source_type} · `{source_path}`")
        content = escape(_clean_content(item.get("content", ""))).replace("\n", "<br>")
        st.markdown(f'<div class="pse-result-content">{content}</div>', unsafe_allow_html=True)


def render_search_tab() -> None:
    st.subheader("Search your library")
    st.caption("Search by meaning or find an exact phrase across your indexed files.")

    with st.form("search_form"):
        query = st.text_input(
            "Search query",
            placeholder="Try: What were the main decisions in the project notes?",
            label_visibility="collapsed",
        )
        controls_col, submit_col = st.columns([4, 1], vertical_alignment="bottom")
        with controls_col:
            top_k = st.slider("Results to show", 1, 20, DEFAULT_TOP_K)
        with submit_col:
            submitted = st.form_submit_button(
                "Search",
                type="primary",
                use_container_width=True,
            )

    if submitted:
        st.session_state.pop("search_results", None)
        st.session_state.pop("search_query", None)
        if not query.strip():
            st.warning("Enter a query to search your documents.")
        else:
            with st.spinner("Searching your knowledge base..."):
                try:
                    st.session_state.search_results = call_search(query.strip(), top_k)
                    st.session_state.search_query = query.strip()
                except httpx.HTTPError as error:
                    st.error(_request_error("Search failed", error))

    results = st.session_state.get("search_results")
    previous_query = st.session_state.get("search_query")
    if results is None:
        return
    st.markdown(f"#### Results for “{escape(str(previous_query))}”")
    if not results:
        st.info("No matches found. Try another query or sync your documents.")
        return
    st.caption(f"{len(results)} result{'s' if len(results) != 1 else ''}")
    for rank, item in enumerate(results, start=1):
        render_result_card(item, rank)


def render_chat_tab() -> None:
    st.subheader("Ask your documents")
    st.caption("Get answers grounded in your files, with source passages for context.")
    top_k = st.slider(
        "Sources to use",
        1,
        20,
        DEFAULT_TOP_K,
        key="chat_top_k",
    )
    messages = st.session_state.setdefault("messages", [])

    for message in messages:
        with st.chat_message(message["role"]):
            st.write(message["content"])
            if message.get("sources"):
                with st.expander(
                    f"View {len(message['sources'])} source"
                    f"{'s' if len(message['sources']) != 1 else ''}"
                ):
                    for source in message["sources"]:
                        render_result_card(source)

    query = st.chat_input("Ask a question about your documents...")
    if not query:
        return

    with st.spinner("Searching your documents and preparing an answer..."):
        try:
            result = call_chat(query.strip(), top_k)
        except httpx.HTTPError as error:
            st.error(_request_error("Could not get an answer", error))
            return

    messages.extend(
        [
            {"role": "user", "content": query},
            {
                "role": "assistant",
                "content": result["answer"],
                "sources": result["sources"],
            },
        ]
    )
    st.rerun()


def render_sidebar() -> None:
    st.sidebar.markdown('<p class="pse-sidebar-title">⌕ &nbsp; Personal Search</p>', unsafe_allow_html=True)
    st.sidebar.markdown('<p class="pse-sidebar-copy">Your private knowledge library</p>', unsafe_allow_html=True)
    try:
        stats = get_stats()
    except httpx.HTTPError as error:
        st.sidebar.error("Search service is unavailable.")
        st.sidebar.caption(_request_error("Service status", error))
        api_available = False
    else:
        st.sidebar.success("All systems ready")
        vector_col, text_col = st.sidebar.columns(2)
        vector_col.metric("Semantic", stats["vector_count"])
        text_col.metric("Full-text", stats["fts_count"])
        api_available = True

    st.sidebar.divider()
    st.sidebar.subheader("Library")
    force = st.sidebar.checkbox(
        "Re-index all files",
        help="By default, only new or changed files are processed.",
    )
    if st.sidebar.button(
        "Sync documents",
        type="primary",
        use_container_width=True,
        disabled=not api_available,
    ):
        with st.sidebar.status("Indexing documents...", expanded=True) as status:
            try:
                results = call_index(force)
            except httpx.HTTPError as error:
                status.update(label="Document sync failed", state="error")
                st.session_state.pop("index_summary", None)
                st.session_state.index_error = _request_error(
                    "Indexing failed", error
                )
            else:
                st.session_state.pop("index_error", None)
                st.session_state.index_summary = {
                    "indexed": sum(item["indexed_files"] for item in results),
                    "skipped": sum(item["skipped_files"] for item in results),
                    "errors": sum(item["errors"] for item in results),
                    "failed_files": [
                        path for item in results for path in item["failed_files"]
                    ],
                }
                status.update(
                    label="Document sync complete",
                    state="complete"
                    if st.session_state.index_summary["errors"] == 0
                    else "error",
                )
                st.rerun()

    if error := st.session_state.get("index_error"):
        st.sidebar.error(error)
    if summary := st.session_state.get("index_summary"):
        st.sidebar.success(
            f"Updated {summary['indexed']} file(s); "
            f"{summary['skipped']} unchanged."
        )
        if summary["errors"]:
            st.sidebar.warning(
                f"{summary['errors']} file(s) could not be processed."
            )
            with st.sidebar.expander("View files with errors"):
                for path in summary["failed_files"]:
                    st.sidebar.caption(path)

    if st.sidebar.button("Clear chat history", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


def main() -> None:
    render_sidebar()
    st.markdown('<p class="pse-eyebrow">YOUR PERSONAL KNOWLEDGE SPACE</p>', unsafe_allow_html=True)
    st.title("Find what you need.")
    st.markdown(
        '<p class="pse-intro">Search across your documents or ask a question '
        'and get an answer grounded in your files.</p>',
        unsafe_allow_html=True,
    )
    search_tab, chat_tab = st.tabs(["⌕  Search", "✦  Ask a question"])
    with search_tab:
        render_search_tab()
    with chat_tab:
        render_chat_tab()


if __name__ == "__main__":
    main()
