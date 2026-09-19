"""The long-running bridge: board in, Home Assistant out."""

from __future__ import annotations

import logging
import signal
import threading
from typing import Optional

from .board import BalanceBoard, wait_for_board
from .config import Config
from .measure import MeasurementTracker
from .mqtt import HomeAssistantPublisher

log = logging.getLogger(__name__)


class Daemon:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.tracker = MeasurementTracker(config.measurement)
        self.publisher = HomeAssistantPublisher(config.mqtt)
        self._stop = threading.Event()

    def request_stop(self, *_args) -> None:
        log.info("Shutting down")
        self._stop.set()

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.request_stop)

    def run(self) -> int:
        self.publisher.connect()
        try:
            while not self._stop.is_set():
                board = wait_for_board(self.config.board, self._stop.is_set)
                if board is None:
                    break
                try:
                    self._pump(board)
                finally:
                    board.close()
                # The board powers down on its own between weighings, so a
                # disconnect is routine; drop any half-finished measurement and
                # go back to waiting.
                self.tracker.reset()
                self.publisher.publish_live(False, 0.0, force=True)
        finally:
            self.publisher.close()
        return 0

    def _pump(self, board: BalanceBoard) -> None:
        """Feed samples from one connected board until it goes away."""
        for sample in board.stream(timeout=1.0):
            if self._stop.is_set():
                return
            if sample is None:
                # A second passed with no report. The board streams constantly
                # while connected, so this only happens on the way out; the
                # stream ends by itself once the read fails.
                continue
            measurement = self.tracker.feed(sample)
            self.publisher.publish_live(
                self.tracker.occupied, self.tracker.adjusted_total(sample)
            )
            if measurement is not None:
                self.publisher.publish_measurement(measurement)
