from __future__ import annotations

import logging
import random
import threading
import time
from typing import Optional

from . import db, pipeline
from .config import Config
from .embed import Embedder

logger = logging.getLogger("urlbase_mcp.refresh")


class RefreshThread:
    def __init__(self, cfg: Config, embedder: Embedder):
        self.cfg = cfg
        self.embedder = embedder
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.cfg.refresh_interval_hours <= 0:
            logger.info("refresh disabled (URLBASE_REFRESH_INTERVAL_HOURS=0)")
            return
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="rag-refresh", daemon=True
        )
        self._thread.start()
        logger.info(
            "refresh thread started: every %dh (+/- up to %d min jitter)",
            self.cfg.refresh_interval_hours,
            self.cfg.refresh_jitter_min,
        )

    def stop(self) -> None:
        self._stop.set()

    def _sleep_with_jitter(self) -> None:
        base = self.cfg.refresh_interval_hours * 3600
        jitter = random.randint(0, max(self.cfg.refresh_jitter_min, 0) * 60)
        total = base + jitter
        # Sleep in 30s slices so stop() is responsive.
        end = time.monotonic() + total
        while not self._stop.is_set() and time.monotonic() < end:
            time.sleep(min(30, end - time.monotonic()))

    def _run(self) -> None:
        # Start with a short delay so we don't fight the first user request.
        time.sleep(60)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("refresh tick failed")
            self._sleep_with_jitter()

    def _tick(self) -> None:
        sources = db.list_sources(self.cfg.db_path)
        logger.info("refresh tick: %d sources", len(sources))
        for s in sources:
            if self._stop.is_set():
                return
            try:
                res = pipeline.add_or_refresh(
                    self.cfg, self.embedder, s["url"]
                )
                logger.info(
                    "refresh %s -> %s (%d chunks)%s",
                    s["url"],
                    res.status,
                    res.chunks,
                    f" err={res.error}" if res.error else "",
                )
            except Exception:
                logger.exception("refresh failed for %s", s["url"])
            # Be polite to remote servers.
            time.sleep(1.0)
