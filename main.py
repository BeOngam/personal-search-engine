from __future__ import annotations

import argparse
import subprocess
import sys

from loguru import logger

from connectors.base import Settings


def cmd_index(settings: Settings, args: argparse.Namespace) -> None:
    from pipeline.indexer import Indexer

    indexer = Indexer(settings)
    try:
        if args.file:
            stats = indexer.reindex_file(args.file)
            logger.info(f"Result: {stats}")
        elif args.source:
            if args.source == "docs":
                stats = indexer.index_docs(force=args.force)
                logger.info(f"Result: {stats}")
            else:
                logger.error(f"Source not supported or not yet implemented: {args.source}")
        else:
            stats_list = indexer.index_all(force=args.force)
            for stats in stats_list:
                logger.info(f"Result: {stats}")
    finally:
        indexer.close()


def cmd_serve(settings: Settings, args: argparse.Namespace) -> None:
    import uvicorn

    uvicorn.run(
        "api.chat:app",
        host=settings.api.host,
        port=args.port or settings.api.port,
        reload=settings.api.reload,
    )


def cmd_ui(settings: Settings, args: argparse.Namespace) -> None:
    port = args.port or settings.ui.port
    subprocess.run([
        sys.executable, "-m", "streamlit", "run", "ui/app.py",
        "--server.port", str(port),
    ])


def cmd_scheduler(settings: Settings, args: argparse.Namespace) -> None:
    from scheduler import IndexScheduler

    scheduler = IndexScheduler(settings)
    scheduler.run_forever(run_immediately=args.now)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="personal-search-engine",
        description="Personal search engine — index, API server, UI, and scheduler",
    )
    parser.add_argument(
        "--config", default="config.yaml", help="Path to the config file (default: config.yaml)"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_index = subparsers.add_parser("index", help="Run the indexing pipeline")
    p_index.add_argument("--source", type=str, default=None, help="Index only a specific source (e.g. docs)")
    p_index.add_argument("--file", type=str, default=None, help="Reindex only a specific file")
    p_index.add_argument("--force", action="store_true", help="Reindex even files that haven't changed")
    p_index.set_defaults(func=cmd_index)

    p_serve = subparsers.add_parser("serve", help="Run the API server (FastAPI)")
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.set_defaults(func=cmd_serve)

    p_ui = subparsers.add_parser("ui", help="Run the user interface (Streamlit)")
    p_ui.add_argument("--port", type=int, default=None)
    p_ui.set_defaults(func=cmd_ui)

    p_scheduler = subparsers.add_parser("scheduler", help="Run the scheduler for automatic reindexing")
    p_scheduler.add_argument("--now", action="store_true", help="Also run once immediately")
    p_scheduler.set_defaults(func=cmd_scheduler)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    settings = Settings.from_yaml(args.config)
    args.func(settings, args)


if __name__ == "__main__":
    main()