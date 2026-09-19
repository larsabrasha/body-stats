"""End-to-end test of the pump: samples in, Home Assistant payloads out."""

import json

import pytest

from wiiscale.config import Config
from wiiscale.daemon import Daemon
from wiiscale.measure import Sample
from tests.test_mqtt import FakeClient

SAMPLE_RATE = 50.0


class FakeBoard:
    """Replays a scripted weighing, then ends its stream like a disconnect."""

    name = "Nintendo Wii Remote Balance Board"
    path = "/dev/input/event42"

    def __init__(self, script):
        self.script = script
        self.closed = False

    def stream(self, timeout=1.0):
        t = 0.0
        for total_kg, seconds in self.script:
            for _ in range(int(seconds * SAMPLE_RATE)):
                per_cell = total_kg / 4.0
                yield Sample(timestamp=t, sensors=(per_cell,) * 4)
                t += 1 / SAMPLE_RATE

    def close(self):
        self.closed = True


@pytest.fixture
def daemon():
    config = Config()
    config.measurement.window_seconds = 1.0
    config.measurement.settle_timeout_seconds = 5.0
    config.measurement.min_samples = 10
    config.measurement.auto_tare = False
    config.mqtt.live_interval_seconds = 0.0  # don't throttle inside the test
    daemon = Daemon(config)
    daemon.publisher._client = FakeClient()
    return daemon


def published(daemon, topic):
    return [p for t, p, _ in daemon.publisher._client.published if t == topic]


def test_a_weighing_reaches_the_state_topic(daemon):
    daemon._pump(FakeBoard([(0.0, 1.0), (82.4, 4.0), (0.0, 2.0)]))

    states = published(daemon, daemon.publisher.state_topic)
    assert len(states) == 1
    payload = json.loads(states[0])
    assert payload["weight"] == pytest.approx(82.4, abs=0.01)
    assert payload["quality"] == 100
    assert payload["stable"] is True


def test_occupancy_goes_on_and_off(daemon):
    daemon._pump(FakeBoard([(0.0, 1.0), (82.4, 4.0), (0.0, 3.0)]))

    live = [json.loads(p) for p in published(daemon, daemon.publisher.live_topic)]
    occupancy = [p["occupied"] for p in live]
    assert True in occupancy
    assert occupancy[0] is False
    assert occupancy[-1] is False


def test_two_separate_weighings_publish_twice(daemon):
    daemon._pump(
        FakeBoard(
            [
                (0.0, 1.0),
                (82.4, 4.0),
                (0.0, 8.0),  # longer than cooldown_seconds
                (79.1, 4.0),
                (0.0, 1.0),
            ]
        )
    )

    weights = [json.loads(p)["weight"] for p in published(daemon, daemon.publisher.state_topic)]
    assert weights == pytest.approx([82.4, 79.1], abs=0.01)


def test_stepping_on_and_straight_off_publishes_nothing(daemon):
    daemon._pump(FakeBoard([(0.0, 1.0), (82.4, 0.4), (0.0, 2.0)]))
    assert published(daemon, daemon.publisher.state_topic) == []


def test_stop_request_ends_the_pump(daemon):
    daemon.request_stop()
    daemon._pump(FakeBoard([(82.4, 10.0)]))
    assert published(daemon, daemon.publisher.state_topic) == []
