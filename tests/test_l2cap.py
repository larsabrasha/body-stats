"""Tests for the raw-L2CAP backend.

The socket handling needs a board, but everything that decides what a weighing
says — the calibration table and the report decoding — is arithmetic, and that
is what breaks silently. The reference values below are read off a real board.
"""

import socket
import struct

import pytest

from wiiscale.config import BoardConfig
from wiiscale.l2cap import Calibration, L2CAPBoard, u16

# Rows are 0 kg, 17 kg, 34 kg; columns are top-right, bottom-right, top-left,
# bottom-left, which is the order both the report and the registers use.
REAL_BOARD = [
    [5959, 18633, 18430, 14346],
    [7621, 20284, 20087, 16010],
    [9293, 21942, 21747, 17697],
]


def registers(rows=REAL_BOARD) -> bytes:
    return b"".join(struct.pack(">4H", *row) for row in rows)


def report(raw_values) -> bytes:
    """One 0x32 report: 0xa1, report id, two button bytes, four 16-bit cells."""
    return bytes([0xA1, 0x32, 0x00, 0x00]) + struct.pack(">4H", *raw_values)


def test_u16_is_big_endian():
    assert u16(bytes([0x12, 0x34]), 0) == 0x1234


def test_calibration_parses_three_rows_of_four():
    calibration = Calibration.from_registers(registers())
    assert calibration.rows == REAL_BOARD


def test_calibration_rejects_a_short_block():
    with pytest.raises(ValueError, match="expected 24"):
        Calibration.from_registers(registers()[:20])


@pytest.mark.parametrize("sensor", range(4))
def test_reference_points_map_to_their_own_weights(sensor):
    calibration = Calibration.from_registers(registers())
    assert calibration.to_kg(REAL_BOARD[0][sensor], sensor) == pytest.approx(0.0)
    assert calibration.to_kg(REAL_BOARD[1][sensor], sensor) == pytest.approx(17.0)
    assert calibration.to_kg(REAL_BOARD[2][sensor], sensor) == pytest.approx(34.0)


def test_below_zero_reference_reads_as_empty():
    # An unloaded cell drifts under its own 0 kg reference; that is not a
    # negative weight.
    calibration = Calibration.from_registers(registers())
    assert calibration.to_kg(REAL_BOARD[0][0] - 500, 0) == 0.0


def test_above_the_top_reference_keeps_extrapolating():
    """A person exceeds 34 kg on a cell, and the table stops there."""
    calibration = Calibration.from_registers(registers())
    top = REAL_BOARD[2][0]
    span = top - REAL_BOARD[1][0]
    assert calibration.to_kg(top + span, 0) == pytest.approx(51.0)


def test_a_flat_calibration_row_does_not_divide_by_zero():
    flat = [[1000] * 4, [1000] * 4, [1000] * 4]
    calibration = Calibration.from_registers(registers(flat))
    assert calibration.to_kg(5000, 0) == 17.0


def test_sample_carries_four_calibrated_cells():
    calibration = Calibration.from_registers(registers())
    midpoints = [REAL_BOARD[1][i] for i in range(4)]
    sample = calibration.sample_from_report(report(midpoints), scale=1.0)
    assert sample.sensors == pytest.approx((17.0, 17.0, 17.0, 17.0))
    assert sample.total == pytest.approx(68.0)


def test_weight_scale_is_applied():
    calibration = Calibration.from_registers(registers())
    midpoints = [REAL_BOARD[1][i] for i in range(4)]
    sample = calibration.sample_from_report(report(midpoints), scale=1.02)
    assert sample.total == pytest.approx(68.0 * 1.02)


def board_over_socketpair():
    """A board whose interrupt channel is one end of a real socket pair.

    Datagrams, not a stream: L2CAP is SOCK_SEQPACKET and each report arrives as
    its own message. Over a byte stream two reports would arrive in one recv()
    and the decoder would see a single malformed packet.
    """
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    config = BoardConfig(address="34:AF:2C:E4:EA:C5")
    board = L2CAPBoard(
        config.address, config, ours, ours, Calibration.from_registers(registers())
    )
    return board, theirs


def test_stream_yields_a_sample_per_data_report():
    board, remote = board_over_socketpair()
    try:
        remote.send(report([REAL_BOARD[1][i] for i in range(4)]))
        stream = board.stream(timeout=1.0)
        sample = next(stream)
        assert sample is not None
        assert sample.total == pytest.approx(68.0)
    finally:
        remote.close()
        board.close()


def test_stream_ignores_reports_that_are_not_weight_data():
    board, remote = board_over_socketpair()
    try:
        remote.send(bytes([0xA1, 0x20, 0x00, 0x00, 0x00, 0x00, 0x80]))  # status
        remote.send(report([REAL_BOARD[2][i] for i in range(4)]))
        sample = next(board.stream(timeout=1.0))
        assert sample.total == pytest.approx(136.0)
    finally:
        remote.close()
        board.close()


def test_stream_yields_none_when_the_board_goes_quiet():
    board, remote = board_over_socketpair()
    try:
        assert next(board.stream(timeout=0.05)) is None
    finally:
        remote.close()
        board.close()


def test_stream_ends_when_the_board_disconnects():
    board, remote = board_over_socketpair()
    try:
        remote.close()
        assert list(board.stream(timeout=0.5)) == []
    finally:
        board.close()
