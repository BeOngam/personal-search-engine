"""
connectors/docs.py
──────────────────
Connector for reading local documents:
  - PDF      →  pdfplumber (layout-aware) + pypdf (fallback)
  - DOCX     →  python-docx
  - TXT / MD →  raw text with automatic encoding detection

Output: Generator[Document, None, None]

Usage:
    from connectors.docs import DocsConnector
    from connectors.base import Settings

    settings = Settings.from_yaml("config.yaml")
    connector = DocsConnector(settings)
    for doc in connector.run():
        print(doc)
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from loguru import logger

from connectors.base import BaseConnector, Document, Settings, SourceConfig, SourceType

# ── Supported extensions ─────────────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".markdown"}


# =============================================================================
#  DocsConnector
# =============================================================================

class DocsConnector(BaseConnector):
    """
    Reads local documents (PDF, DOCX, TXT, MD) from configured folders
    and converts them into Document objects.
    """

    SOURCE_TYPE = SourceType.DOCS

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in SUPPORTED_EXTENSIONS

    def fetch(
        self, source_cfg: Optional[SourceConfig] = None
    ) -> Generator[Document, None, None]:
        """
        Finds all valid files and extracts them one by one.
        Files that raise errors are skipped (the whole pipeline doesn't stop).
        """
        cfg = source_cfg or self.source_cfg
        if cfg is None:
            return

        for file_path in self._iter_files(cfg):
            try:
                doc = self._extract(file_path)
                if doc is not None:
                    yield doc
            except Exception as e:
                logger.error(f"Error processing '{file_path.name}': {e}")

    # ── router: based on extension, calls the appropriate extractor ──────────

    def _extract(self, path: Path) -> Optional[Document]:
        ext = path.suffix.lower()

        if ext == ".pdf":
            return self._extract_pdf(path)
        elif ext == ".docx":
            return self._extract_docx(path)
        elif ext in {".txt", ".md", ".markdown"}:
            return self._extract_text(path)
        else:
            logger.warning(f"Unknown extension skipped: {path.name}")
            return None

    # ── PDF ──────────────────────────────────────────────────────────────────

    def _extract_pdf(self, path: Path) -> Optional[Document]:
        """
        First tries pdfplumber (layout-aware).
        If the output is empty, retries with pypdf.
        """
        text = self._pdf_with_pdfplumber(path)

        if not text or len(text.strip()) < 20:
            logger.debug(f"pdfplumber returned insufficient output, falling back to pypdf: {path.name}")
            text = self._pdf_with_pypdf(path)

        if not text or not text.strip():
            logger.warning(f"Empty or scanned PDF (no text): {path.name}")
            return None

        text = _clean_text(text)
        meta = _pdf_metadata(path)

        return self._make_document(
            content=text,
            source_path=path,
            title=meta.get("title") or path.stem,
            author=meta.get("author", ""),
            created_at=meta.get("created_at"),
            modified_at=meta.get("modified_at"),
            extra={"pages": meta.get("pages", 0), "format": "pdf"},
        )

    def _pdf_with_pdfplumber(self, path: Path) -> str:
        """pdfplumber: preserves tables and character spacing better."""
        try:
            import pdfplumber
            pages_text: list[str] = []
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text(x_tolerance=2, y_tolerance=2)
                    if page_text:
                        pages_text.append(page_text)
            return "\n\n".join(pages_text)
        except Exception as e:
            logger.debug(f"pdfplumber raised an error ({path.name}): {e}")
            return ""

    def _pdf_with_pypdf(self, path: Path) -> str:
        """pypdf: faster, but preserves layout less."""
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            pages_text = []
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    pages_text.append(t)
            return "\n\n".join(pages_text)
        except Exception as e:
            logger.debug(f"pypdf raised an error ({path.name}): {e}")
            return ""

    # ── DOCX ─────────────────────────────────────────────────────────────────

    def _extract_docx(self, path: Path) -> Optional[Document]:
        """
        With python-docx:
          - paragraph text
          - text inside tables
          - headers and footers
        """
        try:
            from docx import Document as DocxDocument
            from docx.oxml.ns import qn
        except ImportError:
            logger.error("python-docx is not installed: pip install python-docx")
            return None

        try:
            doc = DocxDocument(str(path))
        except Exception as e:
            logger.error(f"Error opening DOCX '{path.name}': {e}")
            return None

        parts: list[str] = []

        # Main paragraphs
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                parts.append(text)

        # Tables
        for table in doc.tables:
            rows_text: list[str] = []
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    rows_text.append(" | ".join(cells))
            if rows_text:
                parts.append("\n".join(rows_text))

        # Section headers and footers
        for section in doc.sections:
            for hf in [section.header, section.footer]:
                if hf is not None:
                    for para in hf.paragraphs:
                        text = para.text.strip()
                        if text and text not in parts:
                            parts.append(text)

        full_text = "\n\n".join(parts)
        if not full_text.strip():
            logger.warning(f"DOCX appears to be empty: {path.name}")
            return None

        full_text = _clean_text(full_text)

        # Core properties metadata
        props = doc.core_properties
        created_at  = _safe_datetime(getattr(props, "created", None))
        modified_at = _safe_datetime(getattr(props, "modified", None))
        author      = getattr(props, "author", "") or ""
        title       = getattr(props, "title", "") or path.stem

        return self._make_document(
            content=full_text,
            source_path=path,
            title=title,
            author=author,
            created_at=created_at,
            modified_at=modified_at,
            extra={"format": "docx"},
        )

    # ── TXT / MD ─────────────────────────────────────────────────────────────

    def _extract_text(self, path: Path) -> Optional[Document]:
        """
        Plain text and Markdown files.
        For MD: preserves headers but doesn't strip extra syntax
        (the chunker can later use headers for splitting).
        """
        try:
            content = self._read_text_file(path)
        except Exception as e:
            logger.error(f"Error reading file '{path.name}': {e}")
            return None

        if not content.strip():
            logger.warning(f"Empty text file: {path.name}")
            return None

        # For Markdown, extract a title from the first # header
        title = path.stem
        fmt = path.suffix.lower().lstrip(".")

        if fmt in ("md", "markdown"):
            title = _extract_md_title(content) or path.stem

        content = _clean_text(content)

        return self._make_document(
            content=content,
            source_path=path,
            title=title,
            extra={"format": fmt},
        )


# =============================================================================
#  Helper functions (private)
# =============================================================================

def _clean_text(text: str) -> str:
    """
    Cleans raw text:
      - extra spaces
      - control characters (except newline and tab)
      - more than two consecutive blank lines
    """
    # Control characters other than \n and \t
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Extra horizontal spaces on each line
    text = re.sub(r"[^\S\n]+", " ", text)
    # More than two blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_md_title(content: str) -> Optional[str]:
    """Returns the first # header in Markdown as the title."""
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line.lstrip("# ").strip()
    return None


def _safe_datetime(value: object) -> Optional[datetime]:
    """Converts various values to datetime (or returns None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _pdf_metadata(path: Path) -> dict:
    """Extracts PDF metadata using pypdf."""
    meta: dict = {}
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        info = reader.metadata or {}

        meta["pages"] = len(reader.pages)
        meta["title"] = _strip_pdf_meta(info.get("/Title", ""))
        meta["author"] = _strip_pdf_meta(info.get("/Author", ""))

        raw_date = info.get("/ModDate") or info.get("/CreationDate")
        meta["modified_at"] = _parse_pdf_date(raw_date)
        meta["created_at"] = _parse_pdf_date(info.get("/CreationDate"))

    except Exception as e:
        logger.debug(f"Failed to extract PDF metadata ({path.name}): {e}")

    return meta


def _strip_pdf_meta(value: object) -> str:
    """PDF metadata values are sometimes bytes or None."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _parse_pdf_date(raw: object) -> Optional[datetime]:
    """
    PDF date format: D:20230415120000+03'30'
    Converts to datetime.
    """
    if not raw:
        return None
    s = _strip_pdf_meta(raw)
    # Remove the D: prefix
    s = re.sub(r"^D:", "", s)
    # Only the first 14 characters (YYYYMMDDHHmmss)
    s = s[:14]
    try:
        return datetime.strptime(s, "%Y%m%d%H%M%S")
    except Exception:
        return None