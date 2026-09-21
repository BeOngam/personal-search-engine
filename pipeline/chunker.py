from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

import tiktoken
from langchain_text_splitters import RecursiveCharacterTextSplitter
from loguru import logger

from connectors.base import Document, Settings, SourceType


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    content: str
    index: int
    source_type: SourceType
    source_path: str
    title: str
    token_count: int
    extra: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"Chunk(id={self.chunk_id}, doc={self.doc_id}, "
            f"idx={self.index}, tokens={self.token_count})"
        )


class Chunker:
    def __init__(self, settings: Settings):
        self.cfg = settings.chunking
        self._tokenizer = tiktoken.get_encoding(self.cfg.tokenizer)
        self._splitter = self._build_splitter()

    def _build_splitter(self) -> RecursiveCharacterTextSplitter:
        return RecursiveCharacterTextSplitter(
            chunk_size=self.cfg.chunk_size,
            chunk_overlap=self.cfg.chunk_overlap,
            length_function=self._count_tokens,
            separators=["\n\n", "\n", ".", "؟", "!", "،", " ", ""],
        )

    def _count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text))

    def chunk_document(self, doc: Document) -> list[Chunk]:
        if doc.is_empty():
            return []

        raw_chunks = self._splitter.split_text(doc.content)

        chunks: list[Chunk] = []
        for i, text in enumerate(raw_chunks):
            token_count = self._count_tokens(text)
            if token_count < self.cfg.min_chunk_size:
                logger.debug(f"Small chunk skipped (doc={doc.doc_id}, idx={i}, tokens={token_count})")
                continue

            chunk_id = f"{doc.doc_id}_{i:04d}"
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    doc_id=doc.doc_id,
                    content=text,
                    index=i,
                    source_type=doc.source_type,
                    source_path=doc.source_path,
                    title=doc.title,
                    token_count=token_count,
                    extra=doc.extra.copy(),
                )
            )

        logger.debug(f"'{doc.title}' -> {len(chunks)} chunks")
        return chunks

    def chunk_documents(
        self, docs: list[Document] | Generator[Document, None, None]
    ) -> Generator[Chunk, None, None]:
        for doc in docs:
            for chunk in self.chunk_document(doc):
                yield chunk