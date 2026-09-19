"""The long-running bridge: board in, Home Assistant out."""

from __future__ import annotations

import logging
import signal
import threading
from typing import Optional

from .backend import wait_for_board
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
                board = wait_for_board(self.config.board, self._stop)
                if board is None:
                    break
                idle = self._pump(board)
                path = board.path
                board.close()
                if idle:
                    self._release(path)
                # The board powers down between weighings, so a disconnect is
                # routine; drop any half-finished measurement and go back to
                # waiting.
                self.tracker.reset()
                self.publisher.publish_live(False, 0.0, force=True)
        finally:
            self.publisher.close()
        return 0

    def _pump(self, board) -> bool:
        """Feed samples from one connected board until it goes away.

        Returns whether it went away because it had been standing idle, which
        is the case where the link has to be dropped for the board to sleep.
        """
        idle_limit = self.config.board.idle_disconnect_seconds
        done_limit = self.config.board.release_after_weighing_seconds
        empty_since: Optional[float] = None
        weighed = False

        for sample in board.stream(timeout=1.0):
            if self._stop.is_set():
                return False
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
                weighed = True

            if self.tracker.occupied:
                empty_since = None
                continue
            if empty_since is None:
                empty_since = sample.timestamp
                continue

            empty_for = sample.timestamp - empty_since
            if weighed and done_limit and empty_for >= done_limit:
                log.info("Weighing finished; releasing the board so it can sleep")
                return True
            if idle_limit and empty_for >= idle_limit:
                log.info(
                    "Board idle for %.0f s without a weighing; releasing it",
                    idle_limit,
                )
                return True
        return False

    def _release(self, path: str) -> None:
        """Drop the Bluetooth link so the board powers itself off.

        Only the evdev backend needs this. On l2cap we opened the connection
        ourselves and closing the sockets has already ended it.
        """
        if self.config.board.backend != "evdev":
            return
        from .release import address_from_input_device, disconnect

        address = self.config.board.address or address_from_input_device(path)
        if address is None:
            log.warning(
                "Cannot release the board: its Bluetooth address is unknown, so "
                "it stays connected and awake. Set board.address to fix it, or "
                "board.release_after_weighing_seconds and "
                "board.idle_disconnect_seconds to 0 to stop trying."
            )
            return
        if disconnect(address):
            log.info("Released %s", address)
