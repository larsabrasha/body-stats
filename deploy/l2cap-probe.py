#!/usr/bin/env python3
"""Probe a balance board over raw L2CAP, bypassing BlueZ's HID profile.

    sudo ./deploy/l2cap-probe.py 34:AF:2C:E4:EA:C5

Wake the board first (SYNC or the power button) and run this within a few
seconds. It connects the two HID channels itself, so it needs neither pairing,
nor BlueZ's wiimote plugin, nor the hid-wiimote driver — which is the whole
point: those are what fail on a Raspberry Pi OS bluetoothd built without
--enable-wiimote. See docs/pairing.md.

Prints the calibration table and ten live readings, then exits.
"""

from __future__ import annotations

import socket
import sys
import time

PSM_CONTROL = 0x11
PSM_INTERRUPT = 0x13

CMD_STATUS = bytes([0x52, 0x15, 0x00])
# Read 0x18 = 24 bytes from register 0x04a40024: the 0 kg, 17 kg and 34 kg rows.
CMD_READ_CALIBRATION = bytes([0x52, 0x17, 0x04, 0xA4, 0x00, 0x24, 0x00, 0x18])
# Report mode 0x32: core buttons plus 8 extension bytes, sent continuously.
CMD_REPORTING = bytes([0x52, 0x12, 0x04, 0x32])

# WiiBrew's order for both the report and the calibration rows.
SENSORS = ("top-right", "bottom-right", "top-left", "bottom-left")


def connect(addr: str) -> tuple[socket.socket, socket.socket]:
    control = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    interrupt = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    control.connect((addr, PSM_CONTROL))
    interrupt.connect((addr, PSM_INTERRUPT))
    interrupt.settimeout(5.0)
    return control, interrupt


def u16(data: bytes, offset: int) -> int:
    return (data[offset] << 8) | data[offset + 1]


def parse_calibration(rows: dict[int, bytes]) -> list[list[int]]:
    """Three rows of four big-endian 16-bit references: 0 kg, 17 kg, 34 kg."""
    raw = rows[0] + rows[1]
    if len(raw) < 24:
        raise ValueError(f"calibration is {len(raw)} bytes, expected 24")
    return [[u16(raw, row * 8 + i * 2) for i in range(4)] for row in range(3)]


def to_kg(value: int, reference: list[list[int]], sensor: int) -> float:
    zero, mid, high = (reference[r][sensor] for r in range(3))
    if value <= zero:
        return 0.0
    if value < mid:
        return 17.0 * (value - zero) / float(mid - zero)
    return 17.0 + 17.0 * (value - mid) / float(high - mid)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <board-mac-address>", file=sys.stderr)
        return 2
    addr = argv[1]

    print(f"Connecting to {addr} ...")
    try:
        control, interrupt = connect(addr)
    except OSError as exc:
        print(f"Could not connect: {exc}", file=sys.stderr)
        print("Is the board awake? Press SYNC or the power button and retry.", file=sys.stderr)
        return 1
    print("Both L2CAP channels are up.")

    try:
        control.send(CMD_STATUS)
        control.send(CMD_READ_CALIBRATION)
        control.send(CMD_REPORTING)

        calibration_rows: dict[int, bytes] = {}
        samples = 0
        reference: list[list[int]] | None = None
        deadline = time.monotonic() + 20.0

        while time.monotonic() < deadline and samples < 10:
            try:
                packet = interrupt.recv(32)
            except socket.timeout:
                print("No reports arrived within 5 s.", file=sys.stderr)
                return 1
            if len(packet) < 2 or packet[0] != 0xA1:
                continue
            report = packet[1]

            if report == 0x21:  # read-register response
                size = (packet[4] >> 4) + 1
                offset = u16(packet, 5)
                body = packet[7 : 7 + size]
                calibration_rows[0 if offset == 0x0024 else 1] = body
                if len(calibration_rows) == 2 and reference is None:
                    reference = parse_calibration(calibration_rows)
                    print("\nCalibration (raw 16-bit references):")
                    for i, name in enumerate(SENSORS):
                        print(f"  {name:<14} 0kg={reference[0][i]:>6}  "
                              f"17kg={reference[1][i]:>6}  34kg={reference[2][i]:>6}")
                    print()

            elif report == 0x32 and reference is not None:
                raw = [u16(packet, 4 + i * 2) for i in range(4)]
                kg = [to_kg(raw[i], reference, i) for i in range(4)]
                print("  " + "  ".join(f"{n.split('-')[0][0]}{n.split('-')[1][0]}"
                                       f"={v:6.2f}" for n, v in zip(SENSORS, kg))
                      + f"   total={sum(kg):7.2f} kg")
                samples += 1

        if reference is None:
            print("Connected, but no calibration arrived.", file=sys.stderr)
            return 1
        print("\nThis works: raw L2CAP bypasses the HID profile entirely.")
        return 0
    finally:
        control.close()
        interrupt.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
