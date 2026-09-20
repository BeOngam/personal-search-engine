# Personal Search Engine

A privacy-first, self-hosted personal search engine that indexes all your data — documents, emails, notes, chats — in one place and lets you search it with **hybrid search** (semantic + full-text) and ask questions about it with **local RAG**.

Everything runs on your own machine. No data leaves your computer unless you explicitly configure a cloud LLM provider.

---

## Table of Contents

- [Why this project exists](#why-this-project-exists)
- [Architecture](#architecture)
- [Project structure](#project-structure)
- [How a search query actually works](#how-a-search-query-actually-works)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Running the pipeline](#running-the-pipeline)
- [API reference](#api-reference)
- [Adding a new connector](#adding-a-new-connector)
- [Project status](#project-status)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

---

## Why this project exists

Your personal data — PDFs, Word docs, notes, emails, chat exports — is scattered across folders and apps, and none of it is searchable the way a modern search engine searches the web. This project builds a **local RAG (Retrieval-Augmented Generation) system** over your own files:

- **Hybrid search**: combines vector (semantic) search with classic full-text search, so you can find things by meaning *and* by exact keyword.
- **Local-first**: embeddings, reranking, and (optionally) the LLM all run locally. Nothing is sent to a third party unless you configure one.
- **Incremental indexing**: files are hashed, so re-running the indexer only processes what actually changed.
- **Pluggable connectors**: documents today, email/notes/chats tomorrow — all through the same interface.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          DATA SOURCES                            │
│         (local files: PDFs, DOCX, TXT, MD, email, chats)         │
└───────────────────────────────┬───────────────────────────────────┘
                                 │
                         connectors/*.py
                    (extract raw text → Document)
                                 │
                                 ▼
                       pipeline/chunker.py
              (Document → list[Chunk], token-aware splitting)
                                 │
                                 ▼
                      pipeline/embedder.py
             (Chunk → EmbeddedChunk, sentence-transformers)
                                 │
                  ┌──────────────┴──────────────┐
                  ▼                              ▼
        storage/vector_db.py            storage/fts_db.py
        (Qdrant — semantic search)      (SQLite FTS5 — keyword search)
                  │                              │
                  └──────────────┬───────────────┘
                                 ▼
                      pipeline/indexer.py
              (orchestrates the whole pipeline end-to-end,
               tracks file state via storage/metadata_store.py)

──────────────────────────── QUERY TIME ────────────────────────────

              user query ──► api/chat.py (FastAPI)
                                 │
                    ┌────────────┴────────────┐
                    ▼                          ▼
          storage/vector_db.py       storage/fts_db.py
          (top-K semantic hits)      (top-K keyword hits)
                    │                          │
                    └────────────┬─────────────┘
                                 ▼
                       api/reranker.py
        (Reciprocal Rank Fusion + cross-encoder reranking)
                                 │
                                 ▼
                     final ranked results
                                 │
                    ┌────────────┴────────────┐
                    ▼                          ▼
              /search endpoint          /chat endpoint
             (raw ranked chunks)   (chunks → context → local LLM
                                     via Ollama → grounded answer)
                                 │
                                 ▼
                        ui/app.py (Streamlit)
```

Two independent indexes are kept in sync for every chunk:

| Index | Purpose | Backend |
|---|---|---|
| Vector index | "find things that *mean* this" | Qdrant |
| Full-text index | "find things that *say* this exactly" | SQLite FTS5 |

Results from both are merged with **Reciprocal Rank Fusion (RRF)** and then reordered by a **cross-encoder reranker** for the final top-K.

---

## Project structure

```
.
├── config.yaml                # Central configuration (sources, chunking, models, DB, LLM, ...)
├── docker-compose.yml         # Qdrant (+ app services) container definitions
├── requirements.txt           # Python dependencies
├── main.py                    # Entry point: wires everything together, starts the API
├── scheduler.py                # APScheduler job that triggers periodic re-indexing
│
├── connectors/                 # Turn a data source into Document objects
│   ├── base.py                 #   BaseConnector (ABC), Document, Settings loader
│   ├── docs.py                 #   PDF / DOCX / TXT / MD  ✅ implemented
│   ├── email.py                #   Email (.mbox / .eml)   🚧 not yet implemented
│   ├── notes.py                #   Notes apps (e.g. Obsidian) 🚧 not yet implemented
│   └── chats.py                #   Chat exports            🚧 not yet implemented
│
├── pipeline/                    # Document → searchable, embedded chunks
│   ├── chunker.py               #   Token-aware text splitting (Chunk)
│   ├── embedder.py              #   sentence-transformers embeddings (EmbeddedChunk)
│   └── indexer.py               #   Orchestrates connector → chunker → embedder → storage
│
├── storage/                     # Persistence layer
│   ├── vector_db.py             #   Qdrant client wrapper
│   ├── fts_db.py                #   SQLite FTS5 wrapper
│   └── metadata_store.py        #   Tracks indexed files (hash-based change detection)
│
├── api/                         # Query-time layer
│   ├── reranker.py              #   RRF fusion + cross-encoder reranking
│   └── chat.py                  #   FastAPI app: /search, /chat, /health, /stats
│
├── ui/
│   └── app.py                   #   Streamlit front-end 🚧 not yet implemented
│
└── test_docs_connector.py       # Manual smoke test for the docs connector
```

**Legend:** ✅ implemented and testable · 🚧 stubbed out, not yet implemented.

---

## How a search query actually works

1. The query is embedded with the same model used at index time (`pipeline/embedder.py`).
2. `storage/vector_db.py` returns the top `search.top_k_vector` semantically similar chunks from Qdrant.
3. `storage/fts_db.py` returns the top `search.top_k_fts` keyword matches from SQLite FTS5.
4. `api/reranker.py` fuses both lists with RRF (weighted by `search.vector_weight` / `search.fts_weight`), then — if `reranker.enabled` — re-scores the fused candidates with a cross-encoder and keeps the top `search.top_k_final`.
5. `/search` returns these chunks directly; `/chat` additionally stuffs them into a prompt and asks the configured LLM (Ollama locally by default) to answer *grounded in that context*, returning both the answer and its sources.

---

## Getting started

### 1. Prerequisites

- Python 3.10+
- Docker (for Qdrant) — or set `vector_db.mode: in_memory` in `config.yaml` to skip Docker entirely for local testing
- [Ollama](https://ollama.ai) installed locally, if you want the `/chat` endpoint to work with a local LLM

### 2. Install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> `torch` + `transformers` + `langchain` together are heavy. Expect this to take a few minutes and a few GB of disk space.

### 3. Start Qdrant

```bash
docker compose up -d
```

(Skip this if you set `vector_db.mode: in_memory` for local testing.)

### 4. Pull a local LLM (optional, for `/chat`)

```bash
ollama pull llama3.2
```

### 5. Configure your data sources

Edit `config.yaml` and point `sources.docs.paths` at the folders you want indexed.

### 6. Run the indexer

```bash
python -c "
from connectors.base import Settings
from pipeline.indexer import Indexer

settings = Settings.from_yaml('config.yaml')
indexer = Indexer(settings)
stats = indexer.index_docs()
print(stats)
indexer.close()
"
```

Or, once implemented, simply:

```bash
python main.py --index
```

### 7. Start the API

```bash
uvicorn api.chat:app --reload --port 8000
```

### 8. Query it

```bash
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"query": "quarterly budget report"}'

curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "What did the budget report say about marketing spend?"}'
```

---

## Configuration

Everything is driven by `config.yaml`, loaded via `connectors.base.Settings`:

| Section | Controls |
|---|---|
| `sources.*` | Which connectors are enabled, which paths/extensions they scan |
| `chunking` | Chunk size, overlap, tokenizer, minimum chunk size |
| `embedding` | Embedding model, device (`cpu`/`cuda`/`mps`), batch size |
| `vector_db` | Qdrant host/port, collection name, distance metric, `server` vs `in_memory` mode |
| `fts_db` | SQLite FTS5 database path and table name |
| `metadata_store` | Path to the change-tracking database |
| `search` | Top-K per retriever, RRF constant, fusion weights |
| `reranker` | Whether to rerank, which cross-encoder to use |
| `llm` | Provider (`ollama` / `anthropic`), model, host, system prompt |
| `api` / `ui` | Ports, hosts |
| `scheduler` | Cron expression or interval for automatic re-indexing |
| `logging` | Log level, file, rotation |

To enable a new source (e.g. notes), set `sources.notes.enabled: true` and fill in `paths`/`extensions` — once `connectors/notes.py` is implemented and registered in `pipeline/indexer.py`'s `connector_map`.

---

## Running the pipeline

The core abstraction is `pipeline.indexer.Indexer`, which wires together a connector, the chunker, the embedder, and both storage backends:

```python
from connectors.base import Settings
from pipeline.indexer import Indexer

settings = Settings.from_yaml("config.yaml")
indexer = Indexer(settings)

# Index everything enabled in config.yaml
stats = indexer.index_all()

# Or just one source
stats = indexer.index_docs(force=False)  # force=True re-indexes unchanged files too

# Or a single file
stats = indexer.reindex_file("~/Documents/report.pdf")

indexer.close()
```

Re-running the indexer is safe and cheap: `storage/metadata_store.py` hashes every file's content, so unchanged files are skipped and only new/modified files go through embedding again.

---

## API reference

Base URL: `http://localhost:8000`

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/health` | – | Liveness check |
| GET | `/stats` | – | Vector/FTS index sizes |
| POST | `/search` | `{"query": str, "top_k"?: int}` | Hybrid search, returns ranked chunks, no LLM call |
| POST | `/chat` | `{"query": str, "top_k"?: int}` | Full RAG: search → LLM answer grounded in retrieved chunks + cited sources |

`/chat` response shape:

```json
{
  "answer": "...",
  "sources": [
    {
      "chunk_id": "...",
      "doc_id": "...",
      "content": "...",
      "source_path": "...",
      "title": "...",
      "source_type": "docs",
      "score": 0.83
    }
  ]
}
```

---

## Adding a new connector

1. Create `connectors/<name>.py`.
2. Subclass `BaseConnector` from `connectors/base.py`:
   ```python
   class MyConnector(BaseConnector):
       SOURCE_TYPE = SourceType.NOTES  # or add a new SourceType

       def can_handle(self, path: Path) -> bool:
           return path.suffix.lower() in {".ext"}

       def fetch(self, source_cfg):
           for file_path in self._iter_files(source_cfg):
               text = ...  # your extraction logic
               yield self._make_document(text, file_path)
   ```
3. Add a `sources.<name>` block to `config.yaml`.
4. Register the connector in `pipeline/indexer.py`'s `connector_map` inside `index_all()`.
5. Add a manual smoke test similar to `test_docs_connector.py`.

Everything downstream — chunking, embedding, storage, search, RAG — works automatically once a connector yields valid `Document` objects.

---

## Project status

| Component | Status |
|---|---|
| `config.yaml` | ✅ Complete |
| `connectors/base.py` | ✅ Complete |
| `connectors/docs.py` (PDF/DOCX/TXT/MD) | ✅ Complete |
| `connectors/email.py`, `notes.py`, `chats.py` | 🚧 Stubs — not implemented |
| `pipeline/chunker.py` | ✅ Complete |
| `pipeline/embedder.py` | ✅ Complete |
| `pipeline/indexer.py` | ✅ Complete |
| `storage/vector_db.py` (Qdrant) | ✅ Complete |
| `storage/fts_db.py` (SQLite FTS5) | ✅ Complete |
| `storage/metadata_store.py` | ✅ Complete |
| `api/reranker.py` | ✅ Complete |
| `api/chat.py` (FastAPI: `/search`, `/chat`) | ✅ Complete |
| `ui/app.py` (Streamlit) | 🚧 Not implemented |
| `scheduler.py` (APScheduler auto reindex) | 🚧 Not implemented |
| `main.py` (CLI entry point) | 🚧 Not implemented |
| `docker-compose.yml` | 🚧 Not implemented |

---

## Roadmap

- [ ] Implement `connectors/email.py` (`.mbox` / `.eml`)
- [ ] Implement `connectors/notes.py` (Obsidian/Markdown vaults with wikilinks)
- [ ] Implement `connectors/chats.py` (JSON/text chat exports)
- [ ] Build `ui/app.py` — Streamlit search + chat interface
- [ ] Build `scheduler.py` — periodic re-indexing via APScheduler, using the cron/interval settings already in `config.yaml`
- [ ] Write `main.py` as the unified CLI/entry point (`--index`, `--serve`, `--ui`)
- [ ] Write `docker-compose.yml` (Qdrant + optionally the API/UI containers)
- [ ] Add automated tests (pytest) beyond the manual smoke test
- [ ] OCR fallback for scanned PDFs with no extractable text

---

## Contributing

This is a personal-scale project, but the connector interface is intentionally generic. If you build a connector for a new source, keep it self-contained in `connectors/`, follow the `BaseConnector` contract, and it should plug into indexing, search, and RAG without touching any other file.