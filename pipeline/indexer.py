from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from connectors.base import Settings
from connectors.docs import DocsConnector
from pipeline.chunker import Chunker
from pipeline.embedder import Embedder
from storage.fts_db import FTSDB
from storage.metadata_store import MetadataStore
from storage.vector_db import VectorDB


@dataclass
class IndexStats:
    source: str
    total_files: int = 0
    skipped_files: int = 0
    indexed_files: int = 0
    total_chunks: int = 0
    errors: int = 0
    failed_files: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"IndexStats(source={self.source}, "
            f"indexed={self.indexed_files}/{self.total_files}, "
            f"chunks={self.total_chunks}, errors={self.errors})"
        )


class Indexer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.chunker = Chunker(settings)
        self.embedder = Embedder(settings)
        self.vector_db = VectorDB(settings, embedding_dim=self.embedder.dim)
        self.fts_db = FTSDB(settings)
        self.metadata_store = MetadataStore(settings)

    def index_docs(self, force: bool = False) -> IndexStats:
        return self._index_source("docs", DocsConnector(self.settings), force=force)

    def index_all(self, force: bool = False) -> list[IndexStats]:
        stats_list: list[IndexStats] = []
        enabled = self.settings.enabled_sources()

        if not enabled:
            logger.warning("No enabled sources found in config.yaml.")
            return stats_list

        connector_map = {
            "docs": lambda: DocsConnector(self.settings),
        }

        for source_name in enabled:
            if source_name not in connector_map:
                logger.warning(f"Connector for '{source_name}' is not implemented yet — skipped.")
                continue
            connector = connector_map[source_name]()
            stats = self._index_source(source_name, connector, force=force)
            stats_list.append(stats)

        return stats_list

    def _index_source(self, source_name: str, connector, force: bool = False) -> IndexStats:
        from pathlib import Path

        stats = IndexStats(source=source_name)
        logger.info(f"Starting indexing for source: '{source_name}' (force={force})")

        source_cfg = self.settings.get_source(source_name)
        if source_cfg is None or not source_cfg.enabled:
            logger.info(f"Source '{source_name}' is disabled.")
            return stats

        for doc in connector.run():
            stats.total_files += 1
            path = Path(doc.source_path)

            if not force and not self.metadata_store.needs_indexing(path):
                logger.debug(f"No changes detected, skipped: {path.name}")
                stats.skipped_files += 1
                continue

            try:
                old_doc_id = self.metadata_store.get_doc_id(path)
                if old_doc_id and old_doc_id != doc.doc_id:
                    logger.debug(f"Removing old version: {old_doc_id}")
                    self.vector_db.delete_by_doc_id(old_doc_id)
                    self.fts_db.delete_by_doc_id(old_doc_id)

                chunks = self.chunker.chunk_document(doc)
                if not chunks:
                    logger.warning(f"No chunks were produced: {path.name}")
                    stats.errors += 1
                    stats.failed_files.append(str(path))
                    continue

                embedded = list(self.embedder.embed_chunks(iter(chunks)))

                self.vector_db.upsert(embedded)
                self.fts_db.upsert(embedded)
                self.metadata_store.record(path, doc.doc_id, source_name)

                stats.indexed_files += 1
                stats.total_chunks += len(embedded)
                logger.info(f"✓ '{path.name}' — {len(embedded)} chunks indexed.")

            except Exception as e:
                logger.error(f"Error indexing '{path.name}': {e}")
                stats.errors += 1
                stats.failed_files.append(str(path))

        logger.info(f"Finished indexing '{source_name}': {stats}")
        return stats

    def reindex_file(self, file_path: str) -> Optional[IndexStats]:
        from pathlib import Path
        from connectors.base import SourceConfig, SourceType
        from connectors.docs import DocsConnector, SUPPORTED_EXTENSIONS

        path = Path(file_path).expanduser().resolve()

        if not path.exists():
            logger.error(f"File does not exist: {path}")
            return None

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            logger.error(f"Unsupported extension: {path.suffix}")
            return None

        tmp_cfg = SourceConfig(
            enabled=True,
            paths=[str(path.parent)],
            extensions=[path.suffix.lower()],
            recursive=False,
        )
        original_cfg = self.settings.sources.get("docs")
        self.settings.sources["docs"] = tmp_cfg
        connector = DocsConnector(self.settings)
        try:
            return self._index_source("docs", connector, force=True)
        finally:
            if original_cfg is not None:
                self.settings.sources["docs"] = original_cfg
            else:
                self.settings.sources.pop("docs", None)

    def close(self) -> None:
        self.fts_db.close()
        self.metadata_store.close()