"""Tests for the measurement state machine.

Samples are synthesised at a fixed rate so each test can describe a weighing in
terms of "stand here for this long" rather than fiddling with timestamps.
"""

import pytest

from wiiscale.config import MeasurementConfig
from wiiscale.measure import (
    MeasurementTracker,
    Sample,
    State,
    quality_score,
    trimmed_mean,
)

SAMPLE_RATE = 50.0  # Hz


def config(**overrides) -> MeasurementConfig:
    base = MeasurementConfig(
        step_on_threshold_kg=5.0,
        step_off_threshold_kg=3.0,
        window_seconds=1.0,
        stability_tolerance_kg=0.3,
        settle_timeout_seconds=5.0,
        min_samples=10,
        cooldown_seconds=1.0,
        auto_tare=False,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def feed_for(tracker, total_kg, seconds, start=0.0, jitter=0.0, rate=SAMPLE_RATE):
    """Feed a constant (optionally jittering) load, returning any measurements.

    Timestamps accumulate the way the real stream's do, so anything that
    depends on floats landing exactly on a boundary will show up here.
    """
    results = []
    count = int(seconds * rate)
    timestamp = start
    for i in range(count):
        offset = jitter * (1 if i % 2 else -1)
        per_cell = (total_kg + offset) / 4.0
        sample = Sample(
            timestamp=timestamp,
            sensors=(per_cell, per_cell, per_cell, per_cell),
        )
        result = tracker.feed(sample)
        if result is not None:
            results.append(result)
        timestamp += 1.0 / rate
    return results, timestamp


def test_stable_weighing_publishes_once():
    tracker = MeasurementTracker(config())
    results, _ = feed_for(tracker, 82.4, seconds=3.0)

    assert len(results) == 1
    measurement = results[0]
    assert measurement.weight_kg == pytest.approx(82.4, abs=0.01)
    assert measurement.quality == 100
    assert measurement.stable is True
    assert measurement.sample_count >= 10


def test_no_second_measurement_until_the_board_is_empty_again():
    tracker = MeasurementTracker(config())
    results, end = feed_for(tracker, 82.4, seconds=6.0)
    assert len(results) == 1
    assert tracker.state is State.COOLDOWN

    # Step off, wait out the cooldown, step back on: that is a new weighing.
    _, end = feed_for(tracker, 0.0, seconds=2.0, start=end)
    assert tracker.state is State.IDLE
    more, _ = feed_for(tracker, 79.1, seconds=3.0, start=end)
    assert len(more) == 1
    assert more[0].weight_kg == pytest.approx(79.1, abs=0.01)


def test_brief_step_on_is_discarded():
    tracker = MeasurementTracker(config())
    results, end = feed_for(tracker, 60.0, seconds=0.4)
    assert results == []
    results, _ = feed_for(tracker, 0.0, seconds=0.2, start=end)
    assert results == []


def test_unstable_load_publishes_after_the_timeout_with_capped_quality():
    tracker = MeasurementTracker(config())
    # Swing well beyond the stability tolerance for longer than the timeout.
    results, _ = feed_for(tracker, 80.0, seconds=6.0, jitter=1.5)

    assert len(results) == 1
    measurement = results[0]
    assert measurement.stable is False
    assert measurement.quality <= 50
    assert measurement.spread_kg > 0.3


def test_load_below_the_step_on_threshold_is_ignored():
    tracker = MeasurementTracker(config())
    results, _ = feed_for(tracker, 2.0, seconds=5.0)
    assert results == []
    assert tracker.state is State.IDLE


def test_auto_tare_removes_a_constant_offset():
    tracker = MeasurementTracker(config(auto_tare=True, max_tare_kg=2.0))
    # The empty board reads 0.8 kg high.
    _, end = feed_for(tracker, 0.8, seconds=3.0)
    assert tracker.tare_kg == pytest.approx(0.8, abs=0.01)

    results, _ = feed_for(tracker, 83.2, seconds=3.0, start=end)
    assert len(results) == 1
    # 83.2 raw minus the learned 0.8 offset.
    assert results[0].weight_kg == pytest.approx(82.4, abs=0.02)


def test_auto_tare_ignores_something_left_on_the_board():
    tracker = MeasurementTracker(config(auto_tare=True, max_tare_kg=2.0))
    feed_for(tracker, 4.5, seconds=4.0)  # a bag, under step-on but over max_tare
    assert tracker.tare_kg == 0.0


def test_occupied_turns_on_when_somebody_steps_on():
    tracker = MeasurementTracker(config())
    assert tracker.occupied is False
    feed_for(tracker, 70.0, seconds=0.5)
    assert tracker.occupied is True


def test_occupied_stays_on_while_standing_there_after_the_weighing():
    """Regression: occupied used to mean "settling", so it went off the moment
    the weighing published, reporting an empty board with someone on it."""
    tracker = MeasurementTracker(config())
    results, timestamp = feed_for(tracker, 82.4, seconds=3.0)
    assert len(results) == 1
    assert tracker.state is State.COOLDOWN

    feed_for(tracker, 82.4, seconds=2.0, start=timestamp)
    assert tracker.occupied is True


def test_occupied_turns_off_after_stepping_off():
    tracker = MeasurementTracker(config())
    _, timestamp = feed_for(tracker, 82.4, seconds=3.0)
    feed_for(tracker, 0.0, seconds=1.0, start=timestamp)
    assert tracker.occupied is False


def test_occupied_does_not_flap_at_the_threshold():
    # Between step_off (3.0) and step_on (5.0): whatever it was, it stays.
    tracker = MeasurementTracker(config())
    feed_for(tracker, 4.0, seconds=1.0)
    assert tracker.occupied is False

    _, timestamp = feed_for(tracker, 82.4, seconds=3.0)
    feed_for(tracker, 4.0, seconds=1.0, start=timestamp)
    assert tracker.occupied is True


def test_timeout_publishes_even_when_the_window_never_fills():
    """Regression: a board reporting slower than min_samples/window_seconds
    never satisfied "full", so the settle timeout dropped the weighing
    silently and Home Assistant saw nothing at all."""
    tracker = MeasurementTracker(
        config(window_seconds=1.0, min_samples=10, settle_timeout_seconds=5.0)
    )
    # 5 Hz over a 1 s window is at most 6 samples — min_samples is never met.
    results, _ = feed_for(tracker, 82.4, seconds=8.0, jitter=0.5, rate=5.0)

    assert len(results) == 1
    assert results[0].stable is False
    assert results[0].quality <= 50
    assert results[0].sample_count < 10
    assert results[0].weight_kg == pytest.approx(82.4, abs=0.6)


def test_timeout_drops_a_weighing_with_too_few_samples_to_mean_anything():
    tracker = MeasurementTracker(
        config(window_seconds=1.0, min_samples=10, settle_timeout_seconds=2.0)
    )
    # One sample per two seconds: the window holds at most one sample at a time.
    results, _ = feed_for(tracker, 82.4, seconds=12.0, rate=0.5)
    assert results == []


def test_trimmed_mean_drops_the_extremes():
    values = [80.0] * 8 + [200.0, 0.0]
    assert trimmed_mean(values, proportion=0.2) == pytest.approx(80.0)


def test_trimmed_mean_survives_tiny_inputs():
    assert trimmed_mean([81.0]) == pytest.approx(81.0)
    assert trimmed_mean([80.0, 82.0]) == pytest.approx(81.0)


@pytest.mark.parametrize(
    "spread,expected",
    [
        (0.0, 100),
        (0.3, 80),   # exactly at the tolerance
        (0.9, 40),
        (1.5, 0),    # five times the tolerance
        (9.0, 0),
    ],
)
def test_quality_score_anchors(spread, expected):
    assert quality_score(spread, tolerance=0.3, stable=True) == expected


def test_quality_score_is_capped_when_never_stable():
    assert quality_score(0.0, tolerance=0.3, stable=False) == 50


def test_reset_returns_to_idle():
    tracker = MeasurementTracker(config())
    feed_for(tracker, 70.0, seconds=0.5)
    assert tracker.state is State.SETTLING
    tracker.reset()
    assert tracker.state is State.IDLE


@pytest.mark.parametrize("rate", [30.0, 40.0, 50.0, 62.5, 97.0, 100.0])
def test_measurement_completes_whatever_the_sample_rate(rate):
    """Regression: the window has to actually reach window_seconds.

    It used to be trimmed to strictly less than window_seconds, so whether a
    weighing ever completed came down to two accumulated floats comparing
    exactly equal.
    """
    tracker = MeasurementTracker(config(window_seconds=1.0, min_samples=10))
    results, _ = feed_for(tracker, 82.4, seconds=3.0, rate=rate)

    assert len(results) == 1
    assert results[0].duration_s >= 1.0
    assert results[0].weight_kg == pytest.approx(82.4, abs=0.01)


def test_window_does_not_grow_past_its_length():
    """The window slides; it must not accumulate the whole weighing."""
    tracker = MeasurementTracker(config(window_seconds=1.0, min_samples=10))
    results, _ = feed_for(tracker, 82.4, seconds=6.0)

    assert len(results) == 1
    # One second at 50 Hz, plus the single sample kept from before the cutoff.
    assert results[0].sample_count <= int(SAMPLE_RATE) + 2
