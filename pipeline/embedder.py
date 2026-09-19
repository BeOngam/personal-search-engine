from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

import numpy as np
from loguru import logger
from sentence_transformers import SentenceTransformer

from connectors.base import Settings
from pipeline.chunker import Chunk


@dataclass
class EmbeddedChunk:
    chunk_id: str
    doc_id: str
    content: str
    index: int
    source_type: str
    source_path: str
    title: str
    token_count: int
    embedding: np.ndarray
    extra: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"EmbeddedChunk(id={self.chunk_id}, doc={self.doc_id}, "
            f"dim={len(self.embedding)})"
        )


class Embedder:
    def __init__(self, settings: Settings):
        self.cfg = settings.embedding
        logger.info(f"Loading embedding model: {self.cfg.model}")
        self._model = SentenceTransformer(
            self.cfg.model,
            device=self.cfg.device,
        )
        self._dim = self._model.get_sentence_embedding_dimension()
        logger.info(f"Model ready - dim: {self._dim}, device: {self.cfg.device}")

    @property
    def dim(self) -> int:
        return self._dim

    def embed_chunks(
        self,
        chunks: list[Chunk] | Generator[Chunk, None, None],
    ) -> Generator[EmbeddedChunk, None, None]:
        batch: list[Chunk] = []

        for chunk in chunks:
            batch.append(chunk)
            if len(batch) >= self.cfg.batch_size:
                yield from self._process_batch(batch)
                batch.clear()

        if batch:
            yield from self._process_batch(batch)

    def _process_batch(self, batch: list[Chunk]) -> list[EmbeddedChunk]:
        texts = [c.content for c in batch]

        vectors = self._model.encode(
            texts,
            batch_size=self.cfg.batch_size,
            normalize_embeddings=self.cfg.normalize,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

        result: list[EmbeddedChunk] = []
        for chunk, vector in zip(batch, vectors):
            result.append(
                EmbeddedChunk(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    content=chunk.content,
                    index=chunk.index,
                    source_type=chunk.source_type.value,
                    source_path=chunk.source_path,
                    title=chunk.title,
                    token_count=chunk.token_count,
                    embedding=vector,
                    extra=chunk.extra.copy(),
                )
            )

        logger.debug(f"Batch of {len(batch)} chunks embedded.")
        return result

    def embed_query(self, query: str) -> np.ndarray:
        vector = self._model.encode(
            query,
            normalize_embeddings=self.cfg.normalize,
            convert_to_numpy=True,
        )
        return vector