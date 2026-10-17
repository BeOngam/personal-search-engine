
__all__ = ["VectorDB", "FTSDB", "MetadataStore"]


def __getattr__(name: str):
    if name == "VectorDB":
        from storage.vector_db import VectorDB

        return VectorDB
    if name == "FTSDB":
        from storage.fts_db import FTSDB

        return FTSDB
    if name == "MetadataStore":
        from storage.metadata_store import MetadataStore

        return MetadataStore
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
