from __future__ import annotations

import os
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
    .stApp { direction: rtl; }
    [data-testid="stSidebar"] { direction: rtl; }
    [data-testid="stMetricLabel"], [data-testid="stMetricValue"] { text-align: right; }
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


def render_result_card(item: dict, rank: int | None = None) -> None:
    with st.container(border=True):
        title_col, rank_col = st.columns([5, 1])
        with title_col:
            st.markdown(f"**{item['title']}**")
        if rank is not None:
            with rank_col:
                st.caption(f"رتبهٔ {rank}")
        st.caption(f"{item['source_type']} · `{item['source_path']}`")
        st.write(item["content"])


def render_search_tab() -> None:
    st.subheader("جست‌وجو در اسناد")
    st.caption("جست‌وجوی ترکیبی معنایی و متن کامل؛ عبارت را وارد کنید و جست‌وجو را بزنید.")

    with st.form("search_form"):
        query = st.text_input(
            "عبارت جست‌وجو",
            placeholder="مثلاً: گزارش هزینه‌های سه‌ماهه",
        )
        top_k = st.slider("تعداد نتایج", 1, 20, DEFAULT_TOP_K)
        submitted = st.form_submit_button("جست‌وجو", type="primary")

    if submitted:
        st.session_state.pop("search_results", None)
        st.session_state.pop("search_query", None)
        if not query.strip():
            st.warning("برای جست‌وجو، یک عبارت وارد کنید.")
        else:
            with st.spinner("در حال جست‌وجو..."):
                try:
                    st.session_state.search_results = call_search(query.strip(), top_k)
                    st.session_state.search_query = query.strip()
                except httpx.HTTPError as error:
                    st.error(_request_error("جست‌وجو ناموفق بود", error))

    results = st.session_state.get("search_results")
    previous_query = st.session_state.get("search_query")
    if results is None:
        return
    st.markdown(f"نتایج عبارت **«{previous_query}»**")
    if not results:
        st.info("نتیجه‌ای پیدا نشد. عبارت دیگری امتحان کنید یا اسناد را همگام‌سازی کنید.")
        return
    st.caption(f"{len(results)} نتیجه")
    for rank, item in enumerate(results, start=1):
        render_result_card(item, rank)


def render_chat_tab() -> None:
    st.subheader("گفت‌وگو با اسناد")
    st.caption("پاسخ‌ها بر اساس اسناد بازیابی‌شده ساخته می‌شوند و منبع آن‌ها نمایش داده می‌شود.")
    top_k = st.slider(
        "تعداد منابع بررسی‌شده",
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
                with st.expander(f"مشاهدهٔ {len(message['sources'])} منبع"):
                    for source in message["sources"]:
                        render_result_card(source)

    query = st.chat_input("سؤالتان را دربارهٔ اسناد بپرسید...")
    if not query:
        return

    with st.spinner("در حال جست‌وجو و آماده‌سازی پاسخ..."):
        try:
            result = call_chat(query.strip(), top_k)
        except httpx.HTTPError as error:
            st.error(_request_error("دریافت پاسخ ناموفق بود", error))
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
    st.sidebar.title("🔎 جست‌وجوی شخصی")
    try:
        stats = get_stats()
    except httpx.HTTPError as error:
        st.sidebar.error("ارتباط با سرویس جست‌وجو برقرار نیست.")
        st.sidebar.caption(_request_error("وضعیت سرویس", error))
        api_available = False
    else:
        st.sidebar.success("سرویس آماده است")
        vector_col, text_col = st.sidebar.columns(2)
        vector_col.metric("برداری", stats["vector_count"])
        text_col.metric("متنی", stats["fts_count"])
        api_available = True

    st.sidebar.divider()
    st.sidebar.subheader("مدیریت اسناد")
    force = st.sidebar.checkbox(
        "بازخوانی همهٔ فایل‌ها",
        help="به‌صورت پیش‌فرض فقط فایل‌های جدید یا تغییرکرده پردازش می‌شوند.",
    )
    if st.sidebar.button(
        "همگام‌سازی اسناد",
        type="primary",
        use_container_width=True,
        disabled=not api_available,
    ):
        with st.sidebar.status("در حال پردازش اسناد...", expanded=True) as status:
            try:
                results = call_index(force)
            except httpx.HTTPError as error:
                status.update(label="همگام‌سازی ناموفق بود", state="error")
                st.session_state.pop("index_summary", None)
                st.session_state.index_error = _request_error(
                    "ایندکس ناموفق بود", error
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
                    label="همگام‌سازی انجام شد",
                    state="complete"
                    if st.session_state.index_summary["errors"] == 0
                    else "error",
                )
                st.rerun()

    if error := st.session_state.get("index_error"):
        st.sidebar.error(error)
    if summary := st.session_state.get("index_summary"):
        st.sidebar.success(
            f"{summary['indexed']} فایل به‌روزرسانی شد؛ "
            f"{summary['skipped']} فایل بدون تغییر بود."
        )
        if summary["errors"]:
            st.sidebar.warning(
                f"پردازش {summary['errors']} فایل با خطا مواجه شد."
            )
            with st.sidebar.expander("مشاهدهٔ فایل‌های ناموفق"):
                for path in summary["failed_files"]:
                    st.sidebar.caption(path)

    if st.sidebar.button("پاک‌کردن تاریخچهٔ گفت‌وگو", use_container_width=True):
        st.session_state.messages = []
        st.rerun()


def main() -> None:
    render_sidebar()
    st.title("جست‌وجوی شخصی")
    st.caption("اسناد خود را جست‌وجو کنید یا دربارهٔ محتوای آن‌ها سؤال بپرسید.")
    search_tab, chat_tab = st.tabs(["جست‌وجو", "گفت‌وگو"])
    with search_tab:
        render_search_tab()
    with chat_tab:
        render_chat_tab()


if __name__ == "__main__":
    main()
