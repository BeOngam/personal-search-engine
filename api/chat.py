from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel

import os

from connectors.base import Settings

CONFIG_PATH = os.getenv("CONFIG_PATH", "config.yaml")
settings = Settings.from_yaml(CONFIG_PATH)
from pipeline.embedder import Embedder
from api.reranker import Reranker, SearchResult
from storage.fts_db import FTSDB
from storage.vector_db import VectorDB

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _state
    settings = Settings.from_yaml(os.getenv("CONFIG_PATH", "config.yaml"))
    embedder = Embedder(settings)

    _state["settings"] = settings
    _state["embedder"] = embedder
    _state["vector_db"] = VectorDB(settings, embedding_dim=embedder.dim)
    _state["fts_db"] = FTSDB(settings)
    _state["reranker"] = Reranker(settings)

    logger.info("API is ready.")
    yield

    _state["fts_db"].close()
    logger.info("API has shut down.")


app = FastAPI(title="Personal Search Engine", lifespan=lifespan)


class SearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None


class SearchResponseItem(BaseModel):
    chunk_id: str
    doc_id: str
    content: str
    source_path: str
    title: str
    source_type: str
    score: float


class ChatRequest(BaseModel):
    query: str
    top_k: Optional[int] = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[SearchResponseItem]


def _run_hybrid_search(query: str, top_k: Optional[int] = None) -> list[SearchResult]:
    settings: Settings = _state["settings"]
    embedder: Embedder = _state["embedder"]
    vector_db: VectorDB = _state["vector_db"]
    fts_db: FTSDB = _state["fts_db"]
    reranker: Reranker = _state["reranker"]

    query_vector = embedder.embed_query(query)

    vector_results = vector_db.search(
        query_vector, top_k=settings.search.top_k_vector
    )
    fts_results = fts_db.search(query, top_k=settings.search.top_k_fts)

    results = reranker.search(query, vector_results, fts_results)

    if top_k:
        results = results[:top_k]

    return results


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/stats")
def stats():
    return {
        "vector_count": _state["vector_db"].count(),
        "fts_count": _state["fts_db"].count(),
    }


@app.post("/search", response_model=list[SearchResponseItem])
def search(req: SearchRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty.")

    results = _run_hybrid_search(req.query, req.top_k)

    return [
        SearchResponseItem(
            chunk_id=r.chunk_id,
            doc_id=r.doc_id,
            content=r.content,
            source_path=r.source_path,
            title=r.title,
            source_type=r.source_type,
            score=r.score,
        )
        for r in results
    ]


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty.")

    settings: Settings = _state["settings"]
    results = _run_hybrid_search(req.query, req.top_k)

    if not results:
        return ChatResponse(
            answer="No relevant information was found in your documents.",
            sources=[],
        )

    context = "\n\n---\n\n".join(
        f"[Source: {r.title}]\n{r.content}" for r in results
    )

    answer = await _call_llm(settings, req.query, context)

    sources = [
        SearchResponseItem(
            chunk_id=r.chunk_id,
            doc_id=r.doc_id,
            content=r.content,
            source_path=r.source_path,
            title=r.title,
            source_type=r.source_type,
            score=r.score,
        )
        for r in results
    ]

    return ChatResponse(answer=answer, sources=sources)


async def _call_llm(settings: Settings, query: str, context: str) -> str:
    cfg = settings.llm

    prompt = (
        f"{cfg.system_prompt}\n\n"
        f"Relevant documents:\n{context}\n\n"
        f"User question: {query}\n\n"
        f"Answer:"
    )

    if cfg.provider == "ollama":
        return await _call_ollama(cfg, prompt)
    elif cfg.provider == "anthropic":
        return await _call_anthropic(cfg, prompt)
    else:
        raise HTTPException(
            status_code=500, detail=f"Unknown provider: {cfg.provider}"
        )


async def _call_ollama(cfg, prompt: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                f"{cfg.host}/api/generate",
                json={
                    "model": cfg.model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": cfg.temperature,
                        "num_predict": cfg.max_tokens,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "").strip()
        except httpx.HTTPError as e:
            logger.error(f"Error communicating with Ollama: {e}")
            raise HTTPException(
                status_code=502, detail="Failed to connect to the Ollama server."
            )


async def _call_anthropic(cfg, prompt: str) -> str:
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": cfg.model,
                    "max_tokens": cfg.max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
            return "\n".join(text_blocks).strip()
        except httpx.HTTPError as e:
            logger.error(f"Error communicating with the Anthropic API: {e}")
            raise HTTPException(
                status_code=502, detail="Failed to connect to the Anthropic API."
            )