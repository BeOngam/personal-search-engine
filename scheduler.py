from __future__ import annotations

import signal
import sys
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from connectors.base import Settings
from pipeline.indexer import Indexer


class IndexScheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cfg = settings.scheduler
        self.indexer = Indexer(settings)
        self.scheduler = BackgroundScheduler()

    def _run_job(self) -> None:
        logger.info("Starting scheduled reindex...")
        try:
            stats_list = self.indexer.index_all()
            for stats in stats_list:
                logger.info(f"Reindex result: {stats}")
        except Exception as e:
            logger.error(f"Error while running scheduled reindex: {e}")

    def _build_trigger(self):
        if self.cfg.interval_seconds:
            return IntervalTrigger(seconds=self.cfg.interval_seconds)
        return CronTrigger.from_crontab(self.cfg.cron)

    def start(self, run_immediately: bool = False) -> None:
        if not self.cfg.enabled:
            logger.warning("scheduler is disabled in config.yaml — not starting.")
            return

        trigger = self._build_trigger()
        self.scheduler.add_job(
            self._run_job,
            trigger=trigger,
            id="reindex_job",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info(f"scheduler started — trigger: {trigger}")

        if run_immediately:
            self._run_job()

    def stop(self) -> None:
        logger.info("Stopping scheduler...")
        self.scheduler.shutdown(wait=True)
        self.indexer.close()

    def run_forever(self, run_immediately: bool = False) -> None:
        self.start(run_immediately=run_immediately)

        def _handle_signal(signum, frame):
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        logger.info("scheduler is running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)


if __name__ == "__main__":
    settings = Settings.from_yaml("config.yaml")
    scheduler = IndexScheduler(settings)
    scheduler.run_forever(run_immediately=True)