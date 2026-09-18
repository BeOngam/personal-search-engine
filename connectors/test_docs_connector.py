"""
test_docs_connector.py
──────────────────────
Quick test for DocsConnector without needing pytest.

Run:
    python test_docs_connector.py
    python test_docs_connector.py --path ~/Documents/sample.pdf
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from loguru import logger

# ── Configure loguru for colored terminal output ────────────────────────────
logger.remove()
logger.add(sys.stderr, level="DEBUG", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")


def make_sample_files(tmp_dir: Path) -> None:
    """Creates a few sample files in a temp folder for testing without real files."""

    # TXT
    (tmp_dir / "sample.txt").write_text(
        "This is a test text file.\n\n"
        "Section two: more content to test the chunker in later stages.\n"
        "Third line with additional information.",
        encoding="utf-8",
    )

    # Markdown
    (tmp_dir / "notes.md").write_text(
        "# Project Notes\n\n"
        "## Section One\n"
        "This is a Markdown note.\n\n"
        "## Section Two\n"
        "- First item\n"
        "- Second item\n",
        encoding="utf-8",
    )

    # Empty file (should be skipped)
    (tmp_dir / "empty.txt").write_text("", encoding="utf-8")

    # File with unknown extension (should be skipped)
    (tmp_dir / "ignore.xyz").write_text("should not be read", encoding="utf-8")

    logger.info(f"Sample files created in {tmp_dir}.")


def run_test(test_path: str | None = None) -> None:
    from connectors.base import Settings, SourceConfig
    from connectors.docs import DocsConnector

    # ── Build Settings manually (no config.yaml needed) ─────────────────────
    if test_path:
        path = Path(test_path).expanduser()
        if not path.exists():
            logger.error(f"Path does not exist: {path}")
            sys.exit(1)
        search_paths = [str(path.parent if path.is_file() else path)]
        recursive = False
    else:
        # Create temporary files
        tmp = tempfile.mkdtemp(prefix="pse_test_")
        tmp_path = Path(tmp)
        make_sample_files(tmp_path)
        search_paths = [tmp]
        recursive = False

    # ── Temporary Settings for the test ─────────────────────────────────────
    settings = Settings()
    settings.sources["docs"] = SourceConfig(
        enabled=True,
        paths=search_paths,
        extensions=[".pdf", ".docx", ".txt", ".md", ".markdown"],
        recursive=recursive,
    )

    # ── Run the connector ───────────────────────────────────────────────────
    connector = DocsConnector(settings)
    logger.info(f"Connector: {connector}")

    docs = list(connector.run())

    # ── Display results ─────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print(f"  Number of documents extracted: {len(docs)}")
    print("═" * 60)

    for i, doc in enumerate(docs, 1):
        print(f"\n[{i}] {doc}")
        print(f"    Title    : {doc.title}")
        print(f"    Path     : {doc.source_path}")
        print(f"    Words    : {doc.word_count}")
        print(f"    doc_id   : {doc.doc_id}")
        print(f"    extra    : {doc.extra}")
        print(f"    Preview  : {doc.content[:120].strip()!r}")

    print("\n" + "═" * 60)

    # ── Basic checks ────────────────────────────────────────────────────────
    if test_path is None:
        # We expect only sample.txt and notes.md to be read (not empty.txt and ignore.xyz)
        assert len(docs) == 2, f"Expected 2 documents, got {len(docs)}"
        titles = {doc.title for doc in docs}
        assert "sample" in titles, "sample.txt should be read"
        assert "notes" in titles or "Project Notes" in titles, "notes.md should be read"
        logger.success("✓ All assertions passed!")
    else:
        logger.success(f"✓ {len(docs)} documents extracted successfully.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test DocsConnector")
    parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="File or folder path to test (optional — default: temporary files)",
    )
    args = parser.parse_args()
    run_test(args.path)