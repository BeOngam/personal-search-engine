"""
connectors/base.py
──────────────────
Abstract base class for all connectors.

Each connector (docs, email, chats, notes) must:
  1. Inherit from BaseConnector
  2. Implement fetch() which returns a generator of Documents
  3. Implement can_handle() which indicates whether a file is supported

This file also contains:
  - Document class (standard output dataclass for connectors)
  - Settings class (loads config.yaml with pydantic-settings)
"""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Generator, Optional

import yaml
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


# =============================================================================
#  Settings — load config.yaml
# =============================================================================

def _load_yaml(path: str | Path) -> dict:
    """Convert config.yaml to dict."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class ChunkingSettings(BaseModel):
    strategy: str = "recursive"
    chunk_size: int = 512
    chunk_overlap: int = 64
    min_chunk_size: int = 50
    tokenizer: str = "cl100k_base"


class EmbeddingSettings(BaseModel):
    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    device: str = "cpu"
    batch_size: int = 32
    normalize: bool = True


class VectorDBSettings(BaseModel):
    host: str = "localhost"
    port: int = 6333
    collection_name: str = "personal_search"
    distance: str = "Cosine"
    mode: str = "server"       # server | in_memory


class FTSSettings(BaseModel):
    path: str = "./data/fts.db"
    table_name: str = "documents"
    language: str = "unicode61"


class MetadataStoreSettings(BaseModel):
    path: str = "./data/metadata.db"


class SearchSettings(BaseModel):
    top_k_vector: int = 20
    top_k_fts: int = 20
    top_k_final: int = 5
    rrf_k: int = 60
    vector_weight: float = 0.7
    fts_weight: float = 0.3


class RerankerSettings(BaseModel):
    enabled: bool = True
    model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    device: str = "cpu"
    batch_size: int = 16


class LLMSettings(BaseModel):
    provider: str = "ollama"
    model: str = "llama3.2"
    host: str = "http://localhost:11434"
    temperature: float = 0.1
    max_tokens: int = 1024
    system_prompt: str = "Answer based on the provided documents."


class SourceConfig(BaseModel):
    enabled: bool = False
    paths: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)
    recursive: bool = True


class SchedulerSettings(BaseModel):
    enabled: bool = True
    cron: str = "0 2 * * *"
    interval_seconds: Optional[int] = None


class LoggingSettings(BaseModel):
    level: str = "INFO"
    file: str = "./logs/app.log"
    rotation: str = "10 MB"
    retention: str = "7 days"


class Settings(BaseModel):
    """
    Full project settings.
    Loaded with Settings.from_yaml("config.yaml").
    """

    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    vector_db: VectorDBSettings = Field(default_factory=VectorDBSettings)
    fts_db: FTSSettings = Field(default_factory=FTSSettings)
    metadata_store: MetadataStoreSettings = Field(default_factory=MetadataStoreSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    @classmethod
    def from_yaml(cls, path: str | Path = "config.yaml") -> "Settings":
        """Read config.yaml and return Settings."""
        raw = _load_yaml(path)

        # sources are parsed separately because their keys are dynamic
        raw_sources = raw.pop("sources", {})
        parsed_sources = {
            name: SourceConfig(**cfg)
            for name, cfg in raw_sources.items()
        }

        instance = cls(sources=parsed_sources, **raw)
        logger.info(f"Settings loaded from {path}.")
        return instance

    def get_source(self, name: str) -> Optional[SourceConfig]:
        """Return settings for a specific source."""
        return self.sources.get(name)

    def enabled_sources(self) -> list[str]:
        """Return list of enabled sources."""
        return [name for name, cfg in self.sources.items() if cfg.enabled]


# =============================================================================
#  Document — standard output of all connectors
# =============================================================================

class SourceType(str, Enum):
    """Type of data source."""
    DOCS   = "docs"
    EMAIL  = "email"
    NOTES  = "notes"
    CHATS  = "chats"
    WEB    = "web"
    OTHER  = "other"


@dataclass
class Document:
    """
    Standard information unit produced by connectors.
    pipeline/chunker.py converts this object into smaller chunks.

    Attributes:
        doc_id      : Unique identifier (content hash or file path)
        content     : Raw extracted text
        source_type : Source type (docs, email, ...)
        source_path : Primary source path or URL (for user display)
        title       : Document title (file name or email subject)
        author      : Author (if available)
        created_at  : Document creation date
        modified_at : Last modification date
        language    : Detected language (optional)
        extra       : Additional metadata (specific to each connector)
    """

    content: str
    source_type: SourceType
    source_path: str

    title: str = ""
    author: str = ""
    created_at: Optional[datetime] = None
    modified_at: Optional[datetime] = None
    language: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    # doc_id is computed automatically from content
    doc_id: str = field(init=False)

    def __post_init__(self):
        self.doc_id = self._compute_id()

    def _compute_id(self) -> str:
        """
        Generate a unique identifier from path + content.
        If the file changes, doc_id also changes.
        """
        raw = f"{self.source_path}::{self.content}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def word_count(self) -> int:
        return len(self.content.split())

    @property
    def char_count(self) -> int:
        return len(self.content)

    def is_empty(self) -> bool:
        return not self.content.strip()

    def __repr__(self) -> str:
        return (
            f"Document(id={self.doc_id}, "
            f"source={self.source_type.value}, "
            f"title='{self.title[:40]}', "
            f"words={self.word_count})"
        )


# =============================================================================
#  BaseConnector — abstract base class
# =============================================================================

class BaseConnector(ABC):
    """
    Base class that all connectors inherit from.

    Usage:
        class DocsConnector(BaseConnector):
            SOURCE_TYPE = SourceType.DOCS

            def can_handle(self, path: Path) -> bool:
                return path.suffix.lower() in {".pdf", ".docx", ".txt"}

            def fetch(self, source_cfg: SourceConfig) -> Generator[Document, None, None]:
                for file_path in self._iter_files(source_cfg):
                    text = self._extract(file_path)
                    yield self._make_document(text, file_path)
    """

    # Subclasses must set this
    SOURCE_TYPE: SourceType = SourceType.OTHER

    def __init__(self, settings: Settings):
        self.settings = settings
        self.source_cfg = settings.get_source(self.SOURCE_TYPE.value)
        logger.debug(f"{self.__class__.__name__} initialized.")

    # ── Abstract methods (each connector must implement) ──────────────────

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """
        Can this connector process this file/path?
        Example: DocsConnector only accepts .pdf, .docx, and .txt.
        """
        ...

    @abstractmethod
    def fetch(self, source_cfg: Optional[SourceConfig] = None) -> Generator[Document, None, None]:
        """
        Main method of each connector.
        Must yield ready Documents.

        Args:
            source_cfg: Source settings (if None, use self.source_cfg)

        Yields:
            Document: one complete document at a time
        """
        ...

    # ── Helper methods (connectors can use or override) ────────────────

    def _iter_files(self, source_cfg: SourceConfig) -> Generator[Path, None, None]:
        """
        Find all valid files from configured paths.
        If recursive=True, subdirectories are also scanned.
        """
        extensions = {ext.lower() for ext in source_cfg.extensions}

        for raw_path in source_cfg.paths:
            base = Path(os.path.expanduser(raw_path))

            if not base.exists():
                logger.warning(f"Path does not exist: {base} — skipped.")
                continue

            pattern = "**/*" if source_cfg.recursive else "*"

            for file_path in base.glob(pattern):
                if not file_path.is_file():
                    continue
                if extensions and file_path.suffix.lower() not in extensions:
                    continue
                if self.can_handle(file_path):
                    yield file_path

    def _make_document(
        self,
        content: str,
        source_path: Path | str,
        title: str = "",
        author: str = "",
        created_at: Optional[datetime] = None,
        modified_at: Optional[datetime] = None,
        language: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> Document:
        """
        Create a standard Document.
        Connectors can use this instead of creating Document directly.
        """
        path = Path(source_path)

        # If no date provided, read from file metadata
        if modified_at is None and path.exists():
            modified_at = datetime.fromtimestamp(path.stat().st_mtime)

        return Document(
            content=content,
            source_type=self.SOURCE_TYPE,
            source_path=str(source_path),
            title=title or path.stem,
            author=author,
            created_at=created_at,
            modified_at=modified_at,
            language=language,
            extra=extra or {},
        )

    def _read_text_file(self, path: Path, encoding: str = "utf-8") -> str:
        """
        Read a simple text file.
        If encoding is wrong, retry with chardet.
        """
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            import chardet
            raw = path.read_bytes()
            detected = chardet.detect(raw)
            enc = detected.get("encoding") or "utf-8"
            logger.debug(f"Detected encoding for {path.name}: {enc}")
            return raw.decode(enc, errors="replace")

    def run(self) -> Generator[Document, None, None]:
        """
        Main entry point for the pipeline.
        If the connector is disabled, nothing is yielded.
        """
        cfg = self.source_cfg

        if cfg is None:
            logger.warning(
                f"{self.__class__.__name__}: source '{self.SOURCE_TYPE.value}' "
                f"not defined in config.yaml — skipped."
            )
            return

        if not cfg.enabled:
            logger.info(
                f"{self.__class__.__name__}: source '{self.SOURCE_TYPE.value}' "
                f"is disabled — skipped."
            )
            return

        logger.info(f"Starting fetch from {self.__class__.__name__}...")
        count = 0
        errors = 0

        for doc in self.fetch(cfg):
            if doc.is_empty():
                logger.debug(f"Empty document skipped: {doc.source_path}")
                continue
            count += 1
            yield doc

        logger.info(
            f"{self.__class__.__name__} finished: "
            f"{count} successful, {errors} errors."
        )

    def __repr__(self) -> str:
        enabled = self.source_cfg.enabled if self.source_cfg else False
        return (
            f"{self.__class__.__name__}("
            f"source={self.SOURCE_TYPE.value}, "
            f"enabled={enabled})"
        )
