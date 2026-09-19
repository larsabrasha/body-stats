"""Tests for decoding evdev events into samples.

These pin down the two things that would silently produce wrong weights: the
mapping from hat axes to load cells, and the raw-unit-to-kilogram scale.
"""

import os
from collections import namedtuple

import pytest

from wiiscale.board import EVDEV_AVAILABLE
from wiiscale.config import BoardConfig

pytestmark = pytest.mark.skipif(not EVDEV_AVAILABLE, reason="evdev is Linux-only")

if EVDEV_AVAILABLE:
    from evdev import ecodes

    from wiiscale.board import BalanceBoard, _axes

FakeEvent = namedtuple("FakeEvent", "type code value")


def abs_event(code, value):
    return FakeEvent(ecodes.EV_ABS, code, value)


def syn():
    return FakeEvent(ecodes.EV_SYN, ecodes.SYN_REPORT, 0)


class FakeDevice:
    """Duck-types just enough of evdev.InputDevice for BalanceBoard.

    A real pipe backs `fd` so the select() inside stream() behaves; it is kept
    permanently readable so every loop iteration goes straight to read().
    """

    def __init__(self, batches):
        self._r, self._w = os.pipe()
        os.write(self._w, b"x")
        self.batches = list(batches)
        self.name = "Nintendo Wii Remote Balance Board"
        self.path = "/dev/input/event42"

    @property
    def fd(self):
        return self._r

    def absinfo(self, axis):
        raise OSError("no absinfo in the fake")

    def read(self):
        if not self.batches:
            raise OSError("device disconnected")
        return self.batches.pop(0)

    def close(self):
        os.close(self._r)
        os.close(self._w)


def collect(batches, config=None):
    device = FakeDevice(batches)
    board = BalanceBoard(device, config or BoardConfig())
    try:
        return [s for s in board.stream(timeout=0.01) if s is not None]
    finally:
        device.close()


def test_one_report_becomes_one_sample_in_kilograms():
    top_right, bottom_right, top_left, bottom_left = _axes()
    batch = [
        abs_event(top_right, 2010),     # 20.10 kg
        abs_event(bottom_right, 2130),  # 21.30 kg
        abs_event(top_left, 2050),      # 20.50 kg
        abs_event(bottom_left, 2045),   # 20.45 kg
        syn(),
    ]
    samples = collect([batch])

    assert len(samples) == 1
    assert samples[0].sensors == pytest.approx((20.10, 21.30, 20.50, 20.45))
    assert samples[0].total == pytest.approx(82.35)


def test_axes_keep_their_load_cell_positions():
    top_right, bottom_right, top_left, bottom_left = _axes()
    samples = collect(
        [[abs_event(top_right, 100), abs_event(bottom_left, 400), syn()]]
    )
    # Only the two axes that were reported moved; the others stayed at zero.
    assert samples[0].sensors == pytest.approx((1.0, 0.0, 0.0, 4.0))


def test_values_persist_until_the_axis_changes_again():
    top_right, _, _, _ = _axes()
    samples = collect(
        [
            [abs_event(top_right, 1000), syn()],
            [syn()],  # a report with no axis changes at all
        ]
    )
    assert len(samples) == 2
    assert samples[0].sensors[0] == pytest.approx(10.0)
    assert samples[1].sensors[0] == pytest.approx(10.0)


def test_several_reports_in_one_read_become_several_samples():
    top_right, _, _, _ = _axes()
    samples = collect(
        [[abs_event(top_right, 100), syn(), abs_event(top_right, 200), syn()]]
    )
    assert [s.sensors[0] for s in samples] == pytest.approx([1.0, 2.0])


def test_unit_scale_is_configurable():
    top_right, _, _, _ = _axes()
    samples = collect(
        [[abs_event(top_right, 1000), syn()]], BoardConfig(unit_scale=0.001)
    )
    assert samples[0].sensors[0] == pytest.approx(1.0)


def test_stream_ends_cleanly_when_the_board_disconnects():
    # FakeDevice raises OSError once its batches run out, which is what a real
    # board vanishing mid-read looks like.
    samples = collect([[syn()]])
    assert len(samples) == 1  # and the generator returned instead of raising
