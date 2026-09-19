from wiiscale.config import Config
from wiiscale.daemon import Daemon
from wiiscale.measure import Sample


class FakeBoard:
    def __init__(self, samples):
        self._samples = samples
        self.closed = False
        self.name = "Fake Balance Board"
        self.path = "/dev/input/event2"

    def stream(self, timeout=1.0):
        yield from self._samples

    def close(self):
        self.closed = True


class SilentPublisher:
    def __init__(self):
        self.measurements = []

    def connect(self):
        pass

    def close(self):
        pass

    def publish_live(self, occupied, total, force=False):
        pass

    def publish_measurement(self, measurement):
        self.measurements.append(measurement)


def daemon(**board_overrides):
    config = Config()
    for key, value in board_overrides.items():
        setattr(config.board, key, value)
    instance = Daemon(config)
    instance.publisher = SilentPublisher()
    return instance


def load(total_kg, seconds, start=0.0, rate=50.0):
    samples, timestamp = [], start
    for _ in range(int(seconds * rate)):
        per_cell = total_kg / 4.0
        samples.append(Sample(timestamp=timestamp, sensors=(per_cell,) * 4))
        timestamp += 1.0 / rate
    return samples


def test_an_empty_board_is_released_so_it_can_power_down():
    # Nothing on Linux turns an idle Wii peripheral off, so a board left
    # connected stays awake until its batteries die.
    instance = daemon(idle_disconnect_seconds=10.0)
    assert instance._pump(FakeBoard(load(0.0, seconds=12.0))) is True


def test_the_idle_timer_resets_while_somebody_is_standing_on_it():
    # Without the reset, a board that was woken and then stood on would be
    # dropped mid-weighing once the idle limit ran out.
    instance = daemon(idle_disconnect_seconds=10.0, release_after_weighing_seconds=0.0)
    samples = load(0.0, 6.0) + load(80.0, 6.0, start=6.0) + load(0.0, 6.0, start=12.0)
    assert instance._pump(FakeBoard(samples)) is False


def test_the_board_is_released_as_soon_as_the_weighing_is_done():
    # The point of the whole thing: step off, and the link ends. Waiting out
    # the idle timer would keep the board awake for minutes after a weighing
    # that is already published.
    instance = daemon(idle_disconnect_seconds=600.0, release_after_weighing_seconds=2.0)
    samples = load(80.0, 4.0) + load(0.0, 4.0, start=4.0)

    released = instance._pump(FakeBoard(samples))

    assert released is True
    assert instance.publisher.measurements, "released without publishing anything"


def test_a_board_woken_without_a_weighing_waits_for_the_idle_timer():
    instance = daemon(idle_disconnect_seconds=10.0, release_after_weighing_seconds=2.0)
    assert instance._pump(FakeBoard(load(0.0, seconds=6.0))) is False
    assert instance._pump(FakeBoard(load(0.0, seconds=12.0))) is True


def test_idle_release_can_be_switched_off():
    instance = daemon(idle_disconnect_seconds=0.0, release_after_weighing_seconds=0.0)
    assert instance._pump(FakeBoard(load(0.0, seconds=60.0))) is False


def test_the_l2cap_backend_needs_no_disconnect(monkeypatch):
    # It owns the sockets, so closing them has already dropped the link.
    import wiiscale.release as release

    def explode(*args, **kwargs):
        raise AssertionError("should not have shelled out to bluetoothctl")

    monkeypatch.setattr(release, "disconnect", explode)
    daemon(backend="l2cap", address="34:AF:2C:E4:EA:C5")._release("/dev/input/event2")


def test_waiting_for_a_board_does_not_busy_loop(monkeypatch):
    # The daemon spends nearly all of its life here, so the gap between scans
    # has to be a wait on the stop event, not a chain of short sleeps. The
    # previous version woke ten times a second just to re-check a flag.
    import threading
    import time

    import pytest

    from wiiscale import board as board_module
    from wiiscale.config import BoardConfig

    monkeypatch.setattr(board_module, "_require_evdev", lambda: None)
    monkeypatch.setattr(board_module, "find_board", lambda config: None)
    monkeypatch.setattr(
        board_module.time, "sleep", lambda *_: pytest.fail("polled with sleep()")
    )

    stop = threading.Event()
    threading.Timer(0.05, stop.set).start()

    started = time.monotonic()
    assert board_module.wait_for_board(BoardConfig(scan_interval_seconds=30.0), stop) is None
    assert time.monotonic() - started < 5.0, "shutdown waited out the scan interval"
