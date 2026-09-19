from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from connectors.base import Settings
from pipeline.embedder import EmbeddedChunk


@dataclass
class VectorSearchResult:
    chunk_id: str
    doc_id: str
    content: str
    source_path: str
    title: str
    source_type: str
    score: float
    extra: dict


class VectorDB:
    def __init__(self, settings: Settings, embedding_dim: int):
        self.cfg = settings.vector_db
        self._dim = embedding_dim
        self._client = self._connect()
        self._ensure_collection()

    def _connect(self) -> QdrantClient:
        if self.cfg.mode == "in_memory":
            logger.info("Qdrant in-memory mode")
            return QdrantClient(":memory:")
        logger.info(f"اتصال به Qdrant: {self.cfg.host}:{self.cfg.port}")
        return QdrantClient(host=self.cfg.host, port=self.cfg.port)

    def _ensure_collection(self) -> None:
        existing = [c.name for c in self._client.get_collections().collections]
        if self.cfg.collection_name in existing:
            logger.debug(f"کالکشن '{self.cfg.collection_name}' از قبل وجود دارد.")
            return

        distance_map = {
            "Cosine": qmodels.Distance.COSINE,
            "Euclid": qmodels.Distance.EUCLID,
            "Dot":    qmodels.Distance.DOT,
        }
        distance = distance_map.get(self.cfg.distance, qmodels.Distance.COSINE)

        self._client.create_collection(
            collection_name=self.cfg.collection_name,
            vectors_config=qmodels.VectorParams(size=self._dim, distance=distance),
        )
        logger.info(f"کالکشن '{self.cfg.collection_name}' ساخته شد (dim={self._dim}).")

    def upsert(self, chunks: list[EmbeddedChunk]) -> None:
        if not chunks:
            return

        points = [
            qmodels.PointStruct(
                id=abs(hash(c.chunk_id)) % (2 ** 63),
                vector=c.embedding.tolist(),
                payload={
                    "chunk_id":   c.chunk_id,
                    "doc_id":     c.doc_id,
                    "content":    c.content,
                    "source_path": c.source_path,
                    "title":      c.title,
                    "source_type": c.source_type,
                    "extra":      c.extra,
                },
            )
            for c in chunks
        ]

        self._client.upsert(
            collection_name=self.cfg.collection_name,
            points=points,
        )
        logger.debug(f"{len(points)} بردار در Qdrant ذخیره شد.")

    def search(
        self,
        query_vector: np.ndarray,
        top_k: int | None = None,
    ) -> list[VectorSearchResult]:
        k = top_k or 20
        hits = self._client.search(
            collection_name=self.cfg.collection_name,
            query_vector=query_vector.tolist(),
            limit=k,
            with_payload=True,
        )

        results = []
        for hit in hits:
            p = hit.payload
            results.append(VectorSearchResult(
                chunk_id=p["chunk_id"],
                doc_id=p["doc_id"],
                content=p["content"],
                source_path=p["source_path"],
                title=p["title"],
                source_type=p["source_type"],
                score=hit.score,
                extra=p.get("extra", {}),
            ))

        return results

    def delete_by_doc_id(self, doc_id: str) -> None:
        self._client.delete(
            collection_name=self.cfg.collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(
                        key="doc_id",
                        match=qmodels.MatchValue(value=doc_id),
                    )]
                )
            ),
        )
        logger.debug(f"بردارهای doc_id='{doc_id}' از Qdrant حذف شدند.")

    def count(self) -> int:
        return self._client.count(collection_name=self.cfg.collection_name).count