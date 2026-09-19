"""Reading the Wii Balance Board through the kernel's hid-wiimote driver.

Linux already knows this device. Once the board is paired, ``hid-wiimote``
exposes it as an ordinary evdev input device whose four absolute axes carry the
load cells, already run through the board's own calibration table. That saves
us from speaking raw Bluetooth HID and decoding the calibration registers by
hand, which is the whole reason this runs on a Pi rather than on a Mac.

Axis mapping, per the driver:

    ABS_HAT0X  top right      ABS_HAT1X  top left
    ABS_HAT0Y  bottom right   ABS_HAT1Y  bottom left

"Top" is the far edge from where the battery cover is, i.e. the edge you face.
Values are in hundredths of a kilogram; see ``BoardConfig.unit_scale``.
"""

from __future__ import annotations

import logging
import select
import threading
import time
from typing import Iterator, List, Optional, Tuple

from .config import BoardConfig
from .measure import Sample

log = logging.getLogger(__name__)

try:  # pragma: no cover - evdev is Linux-only; the rest of the package is not
    from evdev import InputDevice, ecodes, list_devices

    EVDEV_AVAILABLE = True
except ImportError:  # pragma: no cover
    InputDevice = None  # type: ignore[assignment]
    ecodes = None  # type: ignore[assignment]
    list_devices = None  # type: ignore[assignment]
    EVDEV_AVAILABLE = False


def _axes() -> Tuple[int, int, int, int]:
    return (ecodes.ABS_HAT0X, ecodes.ABS_HAT0Y, ecodes.ABS_HAT1X, ecodes.ABS_HAT1Y)


class BoardNotFound(Exception):
    """No input device on this system looks like a balance board."""


def _require_evdev() -> None:
    if not EVDEV_AVAILABLE:
        raise RuntimeError(
            "python-evdev is not installed. On Debian/Raspberry Pi OS: "
            "sudo apt install python3-evdev, or pip install evdev"
        )


def candidate_devices() -> List["InputDevice"]:
    """Every input device that carries the four balance-board axes."""
    _require_evdev()
    found = []
    for path in list_devices():
        try:
            device = InputDevice(path)
        except OSError:
            continue
        abs_codes = {code for code, _ in device.capabilities().get(ecodes.EV_ABS, [])}
        if set(_axes()).issubset(abs_codes):
            found.append(device)
        else:
            device.close()
    return found


def find_board(config: BoardConfig) -> Optional["InputDevice"]:
    """Locate the board, preferring a device whose name matches the config.

    The axis check alone is a strong signal — almost nothing else reports all
    four hat axes as absolute — so a board whose name the driver spells
    differently is still picked up, just with a warning.
    """
    devices = candidate_devices()
    if not devices:
        return None

    wanted = config.device_name.lower()
    for device in devices:
        if wanted in device.name.lower():
            for other in devices:
                if other is not device:
                    other.close()
            return device

    chosen = devices[0]
    log.warning(
        "No input device matched %r; falling back to %r, which has the right axes",
        config.device_name,
        chosen.name,
    )
    for other in devices[1:]:
        other.close()
    return chosen


class BalanceBoard:
    """A connected board, yielding one Sample per synchronised HID report."""

    def __init__(self, device: "InputDevice", config: BoardConfig) -> None:
        self.device = device
        self.config = config
        self._raw = self._initial_values()

    @property
    def name(self) -> str:
        return self.device.name

    @property
    def path(self) -> str:
        return self.device.path

    def _initial_values(self) -> List[int]:
        """Seed from the driver's current axis values.

        Without this the first report would be built from zeros for any axis
        that has not changed since we opened the device.
        """
        values = []
        for axis in _axes():
            try:
                values.append(self.device.absinfo(axis).value)
            except OSError:
                values.append(0)
        return values

    def stream(self, timeout: float = 1.0) -> Iterator[Optional[Sample]]:
        """Yield a Sample per report, or None each time `timeout` elapses.

        The Nones are what lets a caller notice a shutdown request or a stalled
        board without blocking forever inside the read.
        """
        axis_index = {axis: i for i, axis in enumerate(_axes())}
        while True:
            ready, _, _ = select.select([self.device.fd], [], [], timeout)
            if not ready:
                yield None
                continue
            try:
                events = list(self.device.read())
            except (OSError, BlockingIOError) as exc:
                log.info("Balance board disconnected: %s", exc)
                return
            for event in events:
                if event.type == ecodes.EV_ABS and event.code in axis_index:
                    self._raw[axis_index[event.code]] = event.value
                elif event.type == ecodes.EV_SYN and event.code == ecodes.SYN_REPORT:
                    scale = self.config.unit_scale
                    yield Sample(
                        timestamp=time.monotonic(),
                        sensors=tuple(v * scale for v in self._raw),  # type: ignore[arg-type]
                    )

    def close(self) -> None:
        try:
            self.device.close()
        except OSError:
            pass


def wait_for_board(
    config: BoardConfig, stop: Optional[threading.Event] = None
) -> Optional[BalanceBoard]:
    """Block until a board shows up, rescanning /dev/input periodically.

    The board powers itself down between weighings, so its input device
    appearing and vanishing is the normal state of affairs rather than an
    error worth logging loudly.

    The wait between scans is a wait on the stop event, not a sleep loop: this
    is where the daemon spends nearly all of its life, and waking ten times a
    second to re-check a flag costs more than the scanning does.
    """
    _require_evdev()
    stop = stop if stop is not None else threading.Event()
    announced = False
    while not stop.is_set():
        device = find_board(config)
        if device is not None:
            log.info("Balance board connected: %s (%s)", device.name, device.path)
            return BalanceBoard(device, config)
        if not announced:
            log.info("Waiting for the balance board to connect...")
            announced = True
        if stop.wait(config.scan_interval_seconds):
            break
    return None
