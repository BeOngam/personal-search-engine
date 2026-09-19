from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from connectors.base import Settings
from pipeline.embedder import EmbeddedChunk


@dataclass
class FTSResult:
    chunk_id: str
    doc_id: str
    content: str
    source_path: str
    title: str
    source_type: str
    rank: float
    extra: dict


class FTSDB:
    def __init__(self, settings: Settings):
        self.cfg = settings.fts_db
        db_path = Path(self.cfg.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"SQLite FTS5 آماده: {db_path}")

    def _create_tables(self) -> None:
        self._conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS doc_meta (
                chunk_id    TEXT PRIMARY KEY,
                doc_id      TEXT NOT NULL,
                source_path TEXT NOT NULL,
                title       TEXT NOT NULL,
                source_type TEXT NOT NULL,
                extra       TEXT NOT NULL DEFAULT '{{}}'
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS {self.cfg.table_name}
            USING fts5(
                chunk_id UNINDEXED,
                content,
                tokenize = '{self.cfg.language}'
            );
        """)
        self._conn.commit()

    def upsert(self, chunks: list[EmbeddedChunk]) -> None:
        if not chunks:
            return

        import json

        meta_rows = [
            (
                c.chunk_id,
                c.doc_id,
                c.source_path,
                c.title,
                c.source_type,
                json.dumps(c.extra, ensure_ascii=False),
            )
            for c in chunks
        ]

        fts_rows = [(c.chunk_id, c.content) for c in chunks]

        self._conn.executemany(
            f"DELETE FROM {self.cfg.table_name} WHERE chunk_id = ?",
            [(c.chunk_id,) for c in chunks],
        )
        self._conn.executemany(
            """
            INSERT INTO doc_meta (chunk_id, doc_id, source_path, title, source_type, extra)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(chunk_id) DO UPDATE SET
                doc_id      = excluded.doc_id,
                source_path = excluded.source_path,
                title       = excluded.title,
                source_type = excluded.source_type,
                extra       = excluded.extra
            """,
            meta_rows,
        )
        self._conn.executemany(
            f"INSERT INTO {self.cfg.table_name} (chunk_id, content) VALUES (?, ?)",
            fts_rows,
        )
        self._conn.commit()
        logger.debug(f"{len(chunks)} chunk در FTS ذخیره شد.")

    def search(self, query: str, top_k: int | None = None) -> list[FTSResult]:
        import json

        k = top_k or 20
        safe_query = query.replace('"', '""')

        rows = self._conn.execute(
            f"""
            SELECT
                f.chunk_id,
                f.content,
                f.rank,
                m.doc_id,
                m.source_path,
                m.title,
                m.source_type,
                m.extra
            FROM {self.cfg.table_name} f
            JOIN doc_meta m ON f.chunk_id = m.chunk_id
            WHERE {self.cfg.table_name} MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (f'"{safe_query}"', k),
        ).fetchall()

        results = []
        for row in rows:
            results.append(FTSResult(
                chunk_id=row["chunk_id"],
                doc_id=row["doc_id"],
                content=row["content"],
                source_path=row["source_path"],
                title=row["title"],
                source_type=row["source_type"],
                rank=row["rank"],
                extra=json.loads(row["extra"]),
            ))

        return results

    def delete_by_doc_id(self, doc_id: str) -> None:
        chunk_ids = self._conn.execute(
            "SELECT chunk_id FROM doc_meta WHERE doc_id = ?", (doc_id,)
        ).fetchall()

        ids = [r["chunk_id"] for r in chunk_ids]
        if not ids:
            return

        placeholders = ",".join("?" * len(ids))
        self._conn.execute(
            f"DELETE FROM {self.cfg.table_name} WHERE chunk_id IN ({placeholders})", ids
        )
        self._conn.execute(
            f"DELETE FROM doc_meta WHERE doc_id = ?", (doc_id,)
        )
        self._conn.commit()
        logger.debug(f"{len(ids)} chunk برای doc_id='{doc_id}' از FTS حذف شد.")

    def count(self) -> int:
        row = self._conn.execute(
            f"SELECT COUNT(*) as cnt FROM {self.cfg.table_name}"
        ).fetchone()
        return row["cnt"] if row else 0

    def close(self) -> None:
        self._conn.close()
