
__all__ = ["Indexer"]


def __getattr__(name: str):
    if name == "Indexer":
        from pipeline.indexer import Indexer

        return Indexer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
