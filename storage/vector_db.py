
"""
storage/vector_db.py
--------------------
Qdrant wrapper for the personal search engine.

Responsibilities
----------------
- Create / verify the collection on first use.
- Upsert embedded chunks (vectors + payload).
- Search by query vector (top-k nearest neighbours).
- Delete points by doc_id (used when a source document is re-indexed or removed).
- Scroll / list all stored doc_ids (used by the indexer to detect deletions).

Collection schema
-----------------
Every point stores a payload with these fields so the API layer can render
results without hitting the metadata SQLite store for basic display:

    {
        "doc_id":     str,   # stable ID of the source document
        "chunk_index": int,  # position of this chunk inside the document
        "source":     str,   # connector name  e.g. "email", "docs", "notes"
        "text":       str,   # raw chunk text  (shown as the search snippet)
        "title":      str,   # document title or filename
        "url":        str,   # file path / email UID / ""
        "timestamp":  str,   # ISO-8601 creation / modification date or ""
    }

Point IDs
---------
Qdrant requires UUIDs or unsigned integers as point IDs.
We derive a deterministic UUID-v5 from (doc_id, chunk_index) so re-indexing
the same document always overwrites the same points.
"""

from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointIdsList,
    PointStruct,
    VectorParams,
)

logger = logging.getLogger(__name__)

# Namespace used for deterministic UUID generation (arbitrary but fixed).
_UUID_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single result returned by VectorDB.search()."""
    point_id: str
    score: float
    doc_id: str
    chunk_index: int
    source: str
    text: str
    title: str
    url: str
    timestamp: str
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _point_id(doc_id: str, chunk_index: int) -> str:
    """Derive a deterministic UUID string from (doc_id, chunk_index)."""
    return str(uuid.uuid5(_UUID_NAMESPACE, f"{doc_id}::{chunk_index}"))


def _payload_to_result(point_id: str, score: float, payload: dict) -> SearchResult:
    return SearchResult(
        point_id=point_id,
        score=score,
        doc_id=payload.get("doc_id", ""),
        chunk_index=payload.get("chunk_index", 0),
        source=payload.get("source", ""),
        text=payload.get("text", ""),
        title=payload.get("title", ""),
        url=payload.get("url", ""),
        timestamp=payload.get("timestamp", ""),
        extra={k: v for k, v in payload.items()
               if k not in {"doc_id", "chunk_index", "source", "text", "title", "url", "timestamp"}},
    )


# ---------------------------------------------------------------------------
# VectorDB
# ---------------------------------------------------------------------------

class VectorDB:
    """
    Thin wrapper around QdrantClient scoped to a single collection.

    Parameters
    ----------
    host:            Qdrant server hostname (default "localhost").
    port:            Qdrant gRPC/HTTP port (default 6333).
    collection_name: Name of the Qdrant collection to use.
    vector_size:     Dimensionality of the embedding vectors (must match the
                     model used in pipeline/embedder.py).
    distance:        Similarity metric – "Cosine" (default), "Dot", or "Euclid".
    in_memory:       If True, use an in-process Qdrant instance (useful for
                     tests / offline mode without a running Qdrant server).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        collection_name: str = "personal_search",
        vector_size: int = 768,
        distance: str = "Cosine",
        in_memory: bool = False,
    ) -> None:
        self.collection_name = collection_name
        self.vector_size = vector_size
        self._distance = Distance[distance.upper()]

        if in_memory:
            self._client = QdrantClient(":memory:")
            logger.info("VectorDB: using in-memory Qdrant instance.")
        else:
            self._client = QdrantClient(host=host, port=port)
            logger.info("VectorDB: connected to Qdrant at %s:%s.", host, port)

        self._ensure_collection()

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        """Create the collection if it does not already exist."""
        existing = {c.name for c in self._client.get_collections().collections}
        if self.collection_name not in existing:
            self._client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size,
                    distance=self._distance,
                ),
            )
            logger.info(
                "VectorDB: created collection '%s' (dim=%d, distance=%s).",
                self.collection_name,
                self.vector_size,
                self._distance,
            )
        else:
            logger.debug("VectorDB: collection '%s' already exists.", self.collection_name)

    def collection_exists(self) -> bool:
        existing = {c.name for c in self._client.get_collections().collections}
        return self.collection_name in existing

    def delete_collection(self) -> None:
        """Drop the entire collection (used in tests / full re-index scenarios)."""
        self._client.delete_collection(self.collection_name)
        logger.warning("VectorDB: deleted collection '%s'.", self.collection_name)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(
        self,
        doc_id: str,
        chunk_index: int,
        vector: list[float] | np.ndarray,
        text: str,
        source: str = "",
        title: str = "",
        url: str = "",
        timestamp: str = "",
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        """
        Insert or overwrite a single chunk vector.

        Returns the point UUID that was upserted.
        """
        if isinstance(vector, np.ndarray):
            vector = vector.tolist()

        point_id = _point_id(doc_id, chunk_index)
        payload: dict[str, Any] = {
            "doc_id": doc_id,
            "chunk_index": chunk_index,
            "source": source,
            "text": text,
            "title": title,
            "url": url,
            "timestamp": timestamp,
        }
        if extra_payload:
            payload.update(extra_payload)

        self._client.upsert(
            collection_name=self.collection_name,
            points=[PointStruct(id=point_id, vector=vector, payload=payload)],
        )
        logger.debug("VectorDB: upserted point %s (doc_id=%s, chunk=%d).", point_id, doc_id, chunk_index)
        return point_id

    def upsert_batch(
        self,
        records: list[dict[str, Any]],
    ) -> list[str]:
        """
        Upsert multiple chunks in a single round-trip.

        Each dict in *records* must have keys:
            doc_id, chunk_index, vector, text
        and optionally: source, title, url, timestamp, extra_payload.

        Returns list of point UUIDs in the same order as *records*.
        """
        points: list[PointStruct] = []
        ids: list[str] = []

        for r in records:
            vector = r["vector"]
            if isinstance(vector, np.ndarray):
                vector = vector.tolist()

            point_id = _point_id(r["doc_id"], r["chunk_index"])
            ids.append(point_id)

            payload: dict[str, Any] = {
                "doc_id": r["doc_id"],
                "chunk_index": r["chunk_index"],
                "source": r.get("source", ""),
                "text": r["text"],
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "timestamp": r.get("timestamp", ""),
            }
            if r.get("extra_payload"):
                payload.update(r["extra_payload"])

            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        self._client.upsert(collection_name=self.collection_name, points=points)
        logger.info("VectorDB: upserted %d points in batch.", len(points))
        return ids

    # ------------------------------------------------------------------
    # Delete operations
    # ------------------------------------------------------------------

    def delete_by_doc_id(self, doc_id: str) -> None:
        """
        Remove all points that belong to *doc_id*.
        Called before re-indexing a document to avoid stale chunks.
        """
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=FilterSelector(
                filter=Filter(
                    must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
                )
            ),
        )
        logger.info("VectorDB: deleted all points for doc_id='%s'.", doc_id)

    def delete_by_point_ids(self, point_ids: list[str]) -> None:
        """Remove specific points by their UUID."""
        self._client.delete(
            collection_name=self.collection_name,
            points_selector=PointIdsList(points=point_ids),
        )
        logger.debug("VectorDB: deleted %d points by ID.", len(point_ids))

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query_vector: list[float] | np.ndarray,
        top_k: int = 10,
        source_filter: str | None = None,
    ) -> list[SearchResult]:
        """
        Return the top-k most similar chunks to *query_vector*.

        Parameters
        ----------
        query_vector:   Embedding of the user query (same model as indexing).
        top_k:          Number of results to return.
        source_filter:  If provided, restrict results to a specific connector
                        (e.g. "email", "docs", "notes").
        """
        if isinstance(query_vector, np.ndarray):
            query_vector = query_vector.tolist()

        query_filter = None
        if source_filter:
            query_filter = Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=source_filter))]
            )

        hits = self._client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=top_k,
            query_filter=query_filter,
            with_payload=True,
        )

        results = [
            _payload_to_result(str(h.id), h.score, h.payload or {})
            for h in hits
        ]
        logger.debug("VectorDB: search returned %d results (top_k=%d).", len(results), top_k)
        return results

    # ------------------------------------------------------------------
    # Scroll / inspection
    # ------------------------------------------------------------------

    def list_doc_ids(self) -> set[str]:
        """
        Return the set of all doc_ids currently stored in the collection.
        Used by the indexer to detect documents that were deleted from the source.
        """
        doc_ids: set[str] = set()
        offset = None

        while True:
            results, next_offset = self._client.scroll(
                collection_name=self.collection_name,
                scroll_filter=None,
                limit=256,
                offset=offset,
                with_payload=["doc_id"],
                with_vectors=False,
            )
            for point in results:
                if point.payload and "doc_id" in point.payload:
                    doc_ids.add(point.payload["doc_id"])
            if next_offset is None:
                break
            offset = next_offset

        logger.debug("VectorDB: found %d unique doc_ids in collection.", len(doc_ids))
        return doc_ids

    def count(self) -> int:
        """Return the total number of points in the collection."""
        return self._client.count(collection_name=self.collection_name).count

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"VectorDB(collection='{self.collection_name}', "
            f"vector_size={self.vector_size}, distance={self._distance})"
        )