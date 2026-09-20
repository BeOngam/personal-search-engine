from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger

from connectors.base import Settings


@dataclass
class FileRecord:
    path: str
    doc_id: str
    content_hash: str
    source_type: str
    indexed_at: datetime
    file_modified_at: Optional[datetime] = None


class MetadataStore:
    def __init__(self, settings: Settings):
        db_path = Path(settings.metadata_store.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_tables()
        logger.info(f"MetadataStore ready: {db_path}")

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS indexed_files (
                path             TEXT PRIMARY KEY,
                doc_id           TEXT NOT NULL,
                content_hash     TEXT NOT NULL,
                source_type      TEXT NOT NULL,
                indexed_at       TEXT NOT NULL,
                file_modified_at TEXT
            );
        """)
        self._conn.commit()

    def compute_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        return h.hexdigest()

    def needs_indexing(self, path: Path) -> bool:
        row = self._conn.execute(
            "SELECT content_hash FROM indexed_files WHERE path = ?",
            (str(path),),
        ).fetchone()

        if row is None:
            return True

        current_hash = self.compute_hash(path)
        return row["content_hash"] != current_hash

    def record(self, path: Path, doc_id: str, source_type: str) -> None:
        content_hash = self.compute_hash(path)
        file_modified_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        indexed_at = datetime.utcnow().isoformat()

        self._conn.execute(
            """
            INSERT INTO indexed_files
                (path, doc_id, content_hash, source_type, indexed_at, file_modified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                doc_id           = excluded.doc_id,
                content_hash     = excluded.content_hash,
                source_type      = excluded.source_type,
                indexed_at       = excluded.indexed_at,
                file_modified_at = excluded.file_modified_at
            """,
            (str(path), doc_id, content_hash, source_type, indexed_at, file_modified_at),
        )
        self._conn.commit()

    def get(self, path: Path) -> Optional[FileRecord]:
        row = self._conn.execute(
            "SELECT * FROM indexed_files WHERE path = ?", (str(path),)
        ).fetchone()

        if row is None:
            return None

        return FileRecord(
            path=row["path"],
            doc_id=row["doc_id"],
            content_hash=row["content_hash"],
            source_type=row["source_type"],
            indexed_at=datetime.fromisoformat(row["indexed_at"]),
            file_modified_at=(
                datetime.fromisoformat(row["file_modified_at"])
                if row["file_modified_at"] else None
            ),
        )

    def get_doc_id(self, path: Path) -> Optional[str]:
        row = self._conn.execute(
            "SELECT doc_id FROM indexed_files WHERE path = ?", (str(path),)
        ).fetchone()
        return row["doc_id"] if row else None

    def remove(self, path: Path) -> None:
        self._conn.execute(
            "DELETE FROM indexed_files WHERE path = ?", (str(path),)
        )
        self._conn.commit()
        logger.debug(f"Record removed: {path}")

    def all_paths(self, source_type: Optional[str] = None) -> list[str]:
        if source_type:
            rows = self._conn.execute(
                "SELECT path FROM indexed_files WHERE source_type = ?", (source_type,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT path FROM indexed_files").fetchall()
        return [r["path"] for r in rows]

    def count(self, source_type: Optional[str] = None) -> int:
        if source_type:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM indexed_files WHERE source_type = ?",
                (source_type,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM indexed_files"
            ).fetchone()
        return row["cnt"] if row else 0

    def close(self) -> None:
        self._conn.close()