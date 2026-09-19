"""Reading the Wii Balance Board over raw L2CAP, bypassing BlueZ's HID profile.

The kernel's ``hid-wiimote`` driver is the nicer way to read this board — see
``board.py`` — but getting the board as far as an input device needs BlueZ to
complete a legacy PIN pairing, and that needs BlueZ's wiimote plugin. Raspberry
Pi OS ships a bluetoothd built without it, so on a Pi that path stops before it
starts. ``docs/pairing.md`` has the whole story.

This module talks to the board the way a Wii does: two L2CAP channels, the
board's own HID reports, and the calibration table read out of its extension
registers. No pairing, no bonding, no input profile, no kernel driver.

    control (PSM 0x11)    commands out, prefixed 0x52
    interrupt (PSM 0x13)  reports in, prefixed 0xa1

The cost is that the calibration is ours to apply: the board reports raw 16-bit
counts per load cell, and three reference rows say what each cell reads at 0,
17 and 34 kg.
"""

from __future__ import annotations

import logging
import select
import socket
import threading
import time
from typing import Dict, Iterator, List, Optional, Tuple

from .config import BoardConfig
from .measure import Sample

log = logging.getLogger(__name__)

PSM_CONTROL = 0x11
PSM_INTERRUPT = 0x13

CMD_STATUS = bytes([0x52, 0x15, 0x00])
# Read 0x18 = 24 bytes from register 0x04a40024: the 0 kg, 17 kg and 34 kg rows.
CMD_READ_CALIBRATION = bytes([0x52, 0x17, 0x04, 0xA4, 0x00, 0x24, 0x00, 0x18])
# Report mode 0x32: core buttons plus 8 extension bytes, sent continuously.
CMD_REPORTING = bytes([0x52, 0x12, 0x04, 0x32])

REPORT_STATUS = 0x20
REPORT_READ_DATA = 0x21
REPORT_DATA = 0x32

# Report and calibration rows use the same order (WiiBrew, "Wii Balance Board").
SENSOR_ORDER = ("top_right", "bottom_right", "top_left", "bottom_left")

REFERENCE_KG = (0.0, 17.0, 34.0)

CALIBRATION_TIMEOUT_SECONDS = 10.0


class BoardNotFound(Exception):
    """No board answered on the configured address."""


def u16(data: bytes, offset: int) -> int:
    """One big-endian 16-bit value, the only integer encoding this board uses."""
    return (data[offset] << 8) | data[offset + 1]


class Calibration:
    """The board's own reference readings, one row per known weight."""

    def __init__(self, rows: List[List[int]]) -> None:
        if len(rows) != 3 or any(len(row) != 4 for row in rows):
            raise ValueError("calibration needs three rows of four references")
        self.rows = rows

    @classmethod
    def from_registers(cls, raw: bytes) -> "Calibration":
        if len(raw) < 24:
            raise ValueError(f"calibration is {len(raw)} bytes, expected 24")
        return cls([[u16(raw, row * 8 + i * 2) for i in range(4)] for row in range(3)])

    def to_kg(self, value: int, sensor: int) -> float:
        """Interpolate one raw count against that cell's own references.

        Above 34 kg the top pair keeps extrapolating, which is what makes a
        board usable for an adult at all — the references stop at 34 kg per
        cell, but a person is spread across four of them.
        """
        zero, mid, high = (self.rows[row][sensor] for row in range(3))
        if value <= zero:
            return 0.0
        if value < mid:
            span = mid - zero
            return REFERENCE_KG[1] * (value - zero) / float(span) if span else 0.0
        span = high - mid
        if not span:
            return REFERENCE_KG[1]
        return REFERENCE_KG[1] + (REFERENCE_KG[2] - REFERENCE_KG[1]) * (value - mid) / float(span)

    def sample_from_report(self, packet: bytes, scale: float) -> Sample:
        raw = [u16(packet, 4 + i * 2) for i in range(4)]
        return Sample(
            timestamp=time.monotonic(),
            sensors=tuple(self.to_kg(raw[i], i) * scale for i in range(4)),  # type: ignore[arg-type]
        )


class L2CAPBoard:
    """A connected board, yielding one Sample per report it sends."""

    def __init__(
        self,
        address: str,
        config: BoardConfig,
        control: socket.socket,
        interrupt: socket.socket,
        calibration: Calibration,
    ) -> None:
        self.address = address
        self.config = config
        self._control = control
        self._interrupt = interrupt
        self.calibration = calibration

    @property
    def name(self) -> str:
        return f"Wii Balance Board ({self.address})"

    @property
    def path(self) -> str:
        return self.address

    def stream(self, timeout: float = 1.0) -> Iterator[Optional[Sample]]:
        """Yield a Sample per report, or None each time `timeout` elapses.

        The Nones are what lets a caller notice a shutdown request or a stalled
        board without blocking forever inside the read.
        """
        scale = self.config.weight_scale
        while True:
            ready, _, _ = select.select([self._interrupt.fileno()], [], [], timeout)
            if not ready:
                yield None
                continue
            try:
                packet = self._interrupt.recv(32)
            except OSError as exc:
                log.info("Balance board disconnected: %s", exc)
                return
            if not packet:
                log.info("Balance board closed the connection")
                return
            if len(packet) >= 12 and packet[0] == 0xA1 and packet[1] == REPORT_DATA:
                yield self.calibration.sample_from_report(packet, scale)

    def close(self) -> None:
        for sock in (self._interrupt, self._control):
            try:
                sock.close()
            except OSError:
                pass


def _open_channel(address: str, psm: int, timeout: float) -> socket.socket:
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    sock.settimeout(timeout)
    sock.connect((address, psm))
    return sock


def _read_calibration(control: socket.socket, interrupt: socket.socket) -> Calibration:
    """Ask for the calibration block and assemble the two replies it arrives in.

    The board answers a 24-byte read with a 16-byte report followed by an
    8-byte one, each carrying the register offset it starts at.
    """
    control.send(CMD_STATUS)
    control.send(CMD_READ_CALIBRATION)

    chunks: Dict[int, bytes] = {}
    deadline = time.monotonic() + CALIBRATION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        ready, _, _ = select.select([interrupt.fileno()], [], [], remaining)
        if not ready:
            continue
        packet = interrupt.recv(32)
        if len(packet) < 8 or packet[0] != 0xA1 or packet[1] != REPORT_READ_DATA:
            continue
        size = (packet[4] >> 4) + 1
        offset = u16(packet, 5)
        chunks[offset] = packet[7 : 7 + size]
        if 0x0024 in chunks and 0x0034 in chunks:
            return Calibration.from_registers(chunks[0x0024] + chunks[0x0034])

    raise BoardNotFound(
        "Connected, but the board never sent its calibration block. "
        "Wake it and try again."
    )


def connect_board(config: BoardConfig) -> Optional[L2CAPBoard]:
    """Open both channels to the configured address, or return None.

    A board that is asleep simply refuses the connection, which is the normal
    state of affairs between weighings rather than an error worth logging.
    """
    if not config.address:
        raise RuntimeError(
            "board.address is not set. The L2CAP backend connects to the board "
            "by address; find it with: bluetoothctl devices | grep RVL-WBC"
        )

    control: Optional[socket.socket] = None
    interrupt: Optional[socket.socket] = None
    try:
        control = _open_channel(config.address, PSM_CONTROL, config.connect_timeout_seconds)
        interrupt = _open_channel(config.address, PSM_INTERRUPT, config.connect_timeout_seconds)
        interrupt.settimeout(None)
        calibration = _read_calibration(control, interrupt)
    except (OSError, BoardNotFound) as exc:
        log.debug("Could not connect to %s: %s", config.address, exc)
        for sock in (interrupt, control):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        return None

    control.send(CMD_REPORTING)
    log.info("Balance board connected over L2CAP: %s", config.address)
    return L2CAPBoard(config.address, config, control, interrupt, calibration)


def wait_for_board(
    config: BoardConfig, stop: Optional[threading.Event] = None
) -> Optional[L2CAPBoard]:
    """Retry the connection until the board answers.

    Unlike the evdev backend there is nothing to watch for: the board is only
    reachable while it is awake, so this is a poll. The gap between attempts
    is a wait on the stop event rather than a sleep loop, so an idle Pi is
    genuinely idle between them.
    """
    stop = stop if stop is not None else threading.Event()
    announced = False
    while not stop.is_set():
        board = connect_board(config)
        if board is not None:
            return board
        if not announced:
            # SYNC, not the power button: the board only listens for an
            # incoming connection while it is in pairing mode. On the power
            # button it calls out instead, and nothing here answers.
            log.info("Waiting for the balance board; press its red SYNC button")
            announced = True
        if stop.wait(config.scan_interval_seconds):
            break
    return None
