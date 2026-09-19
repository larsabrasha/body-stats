"""Dropping the Bluetooth link so the board powers itself down.

A Wii turns its peripherals off when they go idle. Nothing on Linux does, so
a balance board left connected stays awake on four AA batteries until they
run out - the board has no timer of its own while a host holds the link.

Only the evdev backend needs this. The l2cap backend owns the connection
directly, so closing its sockets is already enough.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from typing import Optional

log = logging.getLogger(__name__)

MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")

DISCONNECT_TIMEOUT_SECONDS = 10.0

# Overridden in tests; there is no other way to exercise the sysfs walk.
SYS_CLASS = "/sys/class"

# How far up the device tree to look for the HID parent. The real distance is
# two (input2 -> input -> 0005:057E:0306.0001); the rest is slack.
MAX_DEPTH = 8


def _hid_uniq(directory: str) -> Optional[str]:
    """Read HID_UNIQ out of one sysfs uevent file, if it holds an address."""
    try:
        with open(os.path.join(directory, "uevent")) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    for line in lines:
        key, _, value = line.partition("=")
        if key == "HID_UNIQ" and MAC.match(value.strip()):
            return value.strip().upper()
    return None


def address_from_input_device(path: str) -> Optional[str]:
    """Find the board's Bluetooth address from its /dev/input/eventN path.

    The input device does not carry it - evdev's `uniq` is empty for this
    driver - but its HID parent does: hidp puts the remote address in
    hid->uniq, and hid-core publishes that as HID_UNIQ in the device's uevent.

    An earlier version read it from the ACL connection object in sysfs
    instead. That attribute no longer exists: net/bluetooth/hci_sysfs.c
    defines only `reset` today, so the lookup silently returned nothing on
    real hardware.
    """
    name = os.path.basename(path)
    try:
        directory = os.path.realpath(f"{SYS_CLASS}/input/{name}/device")
    except OSError:
        return None

    for _ in range(MAX_DEPTH):
        address = _hid_uniq(directory)
        if address:
            return address
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent

    log.debug("No HID_UNIQ above %s", path)
    return None


def disconnect(address: str) -> bool:
    """Ask BlueZ to drop the link. Returns whether it looks like it worked."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "disconnect", address],
            capture_output=True,
            text=True,
            timeout=DISCONNECT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        log.warning("Cannot release the board: bluetoothctl is not installed")
        return False
    except subprocess.SubprocessError as exc:
        log.warning("Cannot release the board: %s", exc)
        return False

    if result.returncode == 0:
        return True
    # bluetoothctl is chatty and its exit code is not always the whole story,
    # so say what it actually printed rather than just that it failed.
    output = (result.stdout + result.stderr).strip().replace("\n", " ")
    log.warning("Could not disconnect %s: %s", address, output or "no output")
    return False
