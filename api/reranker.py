from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sentence_transformers import CrossEncoder

from connectors.base import Settings
from storage.fts_db import FTSResult
from storage.vector_db import VectorSearchResult


@dataclass
class SearchResult:
    chunk_id: str
    doc_id: str
    content: str
    source_path: str
    title: str
    source_type: str
    score: float
    extra: dict


class Reranker:
    def __init__(self, settings: Settings):
        self.cfg = settings.reranker
        self.search_cfg = settings.search
        self._model: CrossEncoder | None = None

        if self.cfg.enabled:
            logger.info(f"Loading reranker: {self.cfg.model}")
            self._model = CrossEncoder(self.cfg.model, device=self.cfg.device)

    def fuse(
        self,
        vector_results: list[VectorSearchResult],
        fts_results: list[FTSResult],
    ) -> list[SearchResult]:
        rrf_k = self.search_cfg.rrf_k
        scores: dict[str, float] = {}
        payloads: dict[str, dict] = {}

        for rank, r in enumerate(vector_results):
            score = self.search_cfg.vector_weight * (1.0 / (rrf_k + rank + 1))
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + score
            payloads[r.chunk_id] = {
                "chunk_id": r.chunk_id,
                "doc_id": r.doc_id,
                "content": r.content,
                "source_path": r.source_path,
                "title": r.title,
                "source_type": r.source_type,
                "extra": r.extra,
            }

        for rank, r in enumerate(fts_results):
            score = self.search_cfg.fts_weight * (1.0 / (rrf_k + rank + 1))
            scores[r.chunk_id] = scores.get(r.chunk_id, 0.0) + score
            if r.chunk_id not in payloads:
                payloads[r.chunk_id] = {
                    "chunk_id": r.chunk_id,
                    "doc_id": r.doc_id,
                    "content": r.content,
                    "source_path": r.source_path,
                    "title": r.title,
                    "source_type": r.source_type,
                    "extra": r.extra,
                }

        fused = [
            SearchResult(score=score, **payloads[chunk_id])
            for chunk_id, score in scores.items()
        ]
        fused.sort(key=lambda r: r.score, reverse=True)
        return fused

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        if not results:
            return []

        if not self.cfg.enabled or self._model is None:
            return results[: self.search_cfg.top_k_final]

        pairs = [(query, r.content) for r in results]
        raw_scores = self._model.predict(
            pairs, batch_size=self.cfg.batch_size, show_progress_bar=False
        )

        for r, score in zip(results, raw_scores):
            r.score = float(score)

        results.sort(key=lambda r: r.score, reverse=True)
        return results[: self.search_cfg.top_k_final]

    def search(
        self,
        query: str,
        vector_results: list[VectorSearchResult],
        fts_results: list[FTSResult],
    ) -> list[SearchResult]:
        fused = self.fuse(vector_results, fts_results)
        logger.debug(f"fusion: {len(fused)} unique results")
        final = self.rerank(query, fused)
        logger.debug(f"rerank: {len(final)} final results")
        return final