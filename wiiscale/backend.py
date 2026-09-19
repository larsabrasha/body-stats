"""Choosing how to read the board.

Two routes exist and they fail in different places, so the choice belongs in
configuration rather than in a guess at runtime:

    l2cap  speaks the board's HID protocol over two L2CAP channels and applies
           the board's own calibration table. Needs no pairing and no kernel
           driver, but the board only accepts a connection while it is in
           pairing mode, so every weighing starts with a press of SYNC.
    evdev  reads the input device hid-wiimote creates. Supports the board
           reconnecting on its own, but getting that far needs a BlueZ with the
           wiimote plugin, which Raspberry Pi OS does not ship.

docs/pairing.md explains why that is, and how to tell which one you can use.
"""

from __future__ import annotations

import threading
from typing import Optional

from .config import BoardConfig


def wait_for_board(config: BoardConfig, stop: Optional[threading.Event] = None):
    """Block until the configured backend hands back a connected board.

    `stop` both ends the wait and is what the wait sleeps on, so a shutdown
    is immediate without anybody polling for it.
    """
    if config.backend == "l2cap":
        from .l2cap import wait_for_board as impl
    else:
        from .board import wait_for_board as impl
    return impl(config, stop)


def describe_boards(config: BoardConfig) -> Optional[str]:
    """One line per candidate board, or None when the backend cannot look.

    The l2cap backend connects to an address rather than scanning, so there is
    nothing for it to enumerate — the answer lives in `bluetoothctl`.
    """
    if config.backend == "l2cap":
        return None

    from .board import candidate_devices

    devices = candidate_devices()
    lines = []
    for device in devices:
        marker = "*" if config.device_name.lower() in device.name.lower() else " "
        lines.append(f"{marker} {device.path}  {device.name}")
        device.close()
    return "\n".join(lines) if lines else ""
