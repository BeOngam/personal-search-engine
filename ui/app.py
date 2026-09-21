from __future__ import annotations

import os

import httpx
import streamlit as st

from connectors.base import Settings

settings = Settings.from_yaml("config.yaml")
# In Docker the API runs in another container, so the host must be overridable.
API_HOST = os.getenv("API_HOST", "localhost")
API_BASE = f"http://{API_HOST}:{settings.api.port}"

st.set_page_config(
    page_title=settings.ui.page_title,
    page_icon="🔎",
    layout="wide",
)


def call_search(query: str, top_k: int | None = None) -> list[dict]:
    resp = httpx.post(
        f"{API_BASE}/search",
        json={"query": query, "top_k": top_k},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def call_chat(query: str, top_k: int | None = None) -> dict:
    resp = httpx.post(
        f"{API_BASE}/chat",
        json={"query": query, "top_k": top_k},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()


def get_stats() -> dict:
    resp = httpx.get(f"{API_BASE}/stats", timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def render_result_card(item: dict) -> None:
    with st.container(border=True):
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(f"**{item['title']}**")
        with col2:
            st.caption(f"score: {item['score']:.3f}")
        st.caption(f"{item['source_type']} · {item['source_path']}")
        st.write(item["content"][:500] + ("..." if len(item["content"]) > 500 else ""))


def render_search_tab() -> None:
    query = st.text_input("Search your documents", placeholder="e.g.: quarterly budget report")
    top_k = st.slider("Number of results", 1, 20, settings.ui.results_per_page)

    if query:
        with st.spinner("Searching..."):
            try:
                results = call_search(query, top_k)
            except httpx.HTTPError as e:
                st.error(f"Error communicating with the API: {e}")
                return

        if not results:
            st.info("No results found.")
            return

        st.write(f"{len(results)} results found.")
        for item in results:
            render_result_card(item)


def render_chat_tab() -> None:
    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
            if msg.get("sources"):
                with st.expander(f"{len(msg['sources'])} sources"):
                    for src in msg["sources"]:
                        render_result_card(src)

    query = st.chat_input("Ask a question about your documents...")

    if query:
        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.write(query)

        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                try:
                    result = call_chat(query)
                except httpx.HTTPError as e:
                    st.error(f"Error communicating with the API: {e}")
                    return

            st.write(result["answer"])
            if result["sources"]:
                with st.expander(f"{len(result['sources'])} sources"):
                    for src in result["sources"]:
                        render_result_card(src)

        st.session_state.messages.append({
            "role": "assistant",
            "content": result["answer"],
            "sources": result["sources"],
        })


def render_sidebar() -> None:
    st.sidebar.title(settings.ui.page_title)
    try:
        stats = get_stats()
        st.sidebar.metric("Number of chunks in vector index", stats["vector_count"])
        st.sidebar.metric("Number of chunks in text index", stats["fts_count"])
    except httpx.HTTPError:
        st.sidebar.warning("Could not connect to the API. Make sure the server is running:\n\n`uvicorn api.chat:app`")

    if st.sidebar.button("Clear chat history"):
        st.session_state.messages = []
        st.rerun()


def main() -> None:
    render_sidebar()
    tab_search, tab_chat = st.tabs(["Search", "Chat"])

    with tab_search:
        render_search_tab()

    with tab_chat:
        render_chat_tab()


if __name__ == "__main__":
    main()