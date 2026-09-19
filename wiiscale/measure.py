"""Turning a stream of load-cell samples into a single trustworthy weight.

The board reports roughly 100 samples a second and every one of them wobbles:
you sway, the platform flexes, the sensors are cheap. So rather than grabbing
one reading, the tracker waits for a window of samples that agree with each
other, then reduces that window to one number and scores how much it agreed.
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Deque, Dict, List, Optional, Sequence, Tuple

from .config import MeasurementConfig


def monotonic_to_iso(monotonic_timestamp: float) -> str:
    """Convert a time.monotonic() stamp to a wall-clock ISO 8601 string.

    Samples are stamped with the monotonic clock so that durations stay correct
    across NTP steps; anything outside this module wants a real date, so the
    offset between the two clocks is applied on the way out.
    """
    wall = time.time() - (time.monotonic() - monotonic_timestamp)
    return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Sample:
    """One synchronised reading of the four load cells, in kilograms."""

    timestamp: float
    sensors: Tuple[float, float, float, float]

    @property
    def total(self) -> float:
        return sum(self.sensors)


@dataclass(frozen=True)
class Measurement:
    """A finished weighing."""

    timestamp: float
    weight_kg: float
    quality: int
    spread_kg: float
    std_dev_kg: float
    sample_count: int
    duration_s: float
    settle_s: float
    sensors_kg: Tuple[float, float, float, float]
    tare_kg: float
    stable: bool

    #: JSON key -> field name. The published payload and the Home Assistant
    #: value templates are both derived from this, so a new field on a weighing
    #: is spelled out here once instead of in three places that must agree.
    PAYLOAD_FIELDS: ClassVar[Dict[str, str]] = {
        "weight": "weight_kg",
        "quality": "quality",
        "stable": "stable",
        "spread": "spread_kg",
        "std_dev": "std_dev_kg",
        "samples": "sample_count",
        "duration": "duration_s",
        "settle_time": "settle_s",
        "sensors": "sensors_kg",
        "tare": "tare_kg",
        "timestamp": "timestamp",
    }

    def as_payload(self) -> Dict[str, Any]:
        """The JSON body of a published weighing."""
        payload: Dict[str, Any] = {
            key: getattr(self, name) for key, name in self.PAYLOAD_FIELDS.items()
        }
        # JSON has no tuple, and the monotonic stamp means nothing outside this
        # process.
        payload[self.payload_key("sensors_kg")] = list(self.sensors_kg)
        payload[self.payload_key("timestamp")] = monotonic_to_iso(self.timestamp)
        return payload

    @classmethod
    def payload_key(cls, field_name: str) -> str:
        """The JSON key a field is published under.

        Callers building templates against the payload go through this, so
        renaming a field breaks loudly here instead of quietly producing a
        Home Assistant entity that never updates.
        """
        for key, name in cls.PAYLOAD_FIELDS.items():
            if name == field_name:
                return key
        raise KeyError(f"{field_name} is not published")


# Below this, spread and standard deviation say nothing about the reading, so
# a timed-out weighing is dropped rather than published as a number.
MIN_PUBLISHABLE_SAMPLES = 2


class State(Enum):
    IDLE = "idle"
    SETTLING = "settling"
    COOLDOWN = "cooldown"


def trimmed_mean(values: Sequence[float], proportion: float = 0.2) -> float:
    """Mean of the values left after dropping the extremes from both ends.

    Cheap insurance against the odd spike from a foot shifting: a plain mean
    would carry it, a median would throw away most of the window.
    """
    ordered = sorted(values)
    cut = int(len(ordered) * proportion)
    core = ordered[cut : len(ordered) - cut] or ordered
    return statistics.fmean(core)


def quality_score(spread: float, tolerance: float, stable: bool) -> int:
    """Score a window's agreement from 0 (useless) to 100 (rock steady).

    Anchored so that a window exactly at the stability tolerance — the loosest
    reading we are willing to accept as stable — scores 80, and the score
    reaches 0 at five times the tolerance. A window published only because the
    settle timeout expired is capped at 50, whatever its spread, because we
    never saw it hold still.
    """
    if tolerance <= 0:
        return 0
    if spread <= tolerance:
        score = 100.0 - 20.0 * (spread / tolerance)
    else:
        score = 80.0 - 80.0 * ((spread - tolerance) / (4.0 * tolerance))
    score = max(0.0, min(100.0, score))
    if not stable:
        score = min(score, 50.0)
    return int(round(score))


@dataclass
class _Window:
    """Samples inside the sliding measurement window."""

    samples: Deque[Sample] = field(default_factory=deque)

    def add(self, sample: Sample, window_seconds: float) -> None:
        self.samples.append(sample)
        cutoff = sample.timestamp - window_seconds
        # Keep one sample from before the cutoff. Dropping it would cap the
        # window's span at just under window_seconds, and the "is the window
        # full yet" check would then hinge on two floats landing exactly equal.
        while len(self.samples) > 2 and self.samples[1].timestamp <= cutoff:
            self.samples.popleft()

    def clear(self) -> None:
        self.samples.clear()

    @property
    def duration(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return self.samples[-1].timestamp - self.samples[0].timestamp


class MeasurementTracker:
    """Feed it samples, it hands back a Measurement once one is complete.

    The lifecycle is IDLE -> SETTLING -> COOLDOWN -> IDLE. Tare is only ever
    updated in IDLE, when the board is reading close to empty.
    """

    def __init__(self, config: MeasurementConfig) -> None:
        self.config = config
        self.state = State.IDLE
        self._window = _Window()
        self._tare = 0.0
        self._zero_samples: Deque[float] = deque()
        self._settle_start: Optional[float] = None
        self._empty_since: Optional[float] = None
        self._occupied = False
        # Steadiest full window seen during this visit, and its spread. Kept so
        # that stepping off, or the settle timeout, can publish the best moment
        # of the weighing rather than whatever is in the window at the end -
        # which, on a step-off, is the load falling away.
        self._best: Optional[List[Sample]] = None
        self._best_spread = float("inf")

    # -- introspection -------------------------------------------------

    @property
    def tare_kg(self) -> float:
        return self._tare

    @property
    def occupied(self) -> bool:
        """Is there weight on the board right now?

        Deliberately independent of the state machine: a weighing finishes and
        enters COOLDOWN while the user is still standing there, so tying this
        to SETTLING would report the board as empty with someone on it.
        """
        return self._occupied

    def adjusted_total(self, sample: Sample) -> float:
        return sample.total - self._tare

    # -- main entry point ----------------------------------------------

    def feed(self, sample: Sample) -> Optional[Measurement]:
        """Consume one sample; return a Measurement when one just completed."""
        total = self.adjusted_total(sample)
        self._update_occupied(total)

        if self.state is State.IDLE:
            self._track_zero(sample)
            if total >= self.config.step_on_threshold_kg:
                self.state = State.SETTLING
                self._settle_start = sample.timestamp
                self._zero_samples.clear()
                self._window.clear()
                self._forget_best()
            return None

        if self.state is State.SETTLING:
            return self._feed_settling(sample, total)

        # COOLDOWN: hold off until the board has been empty long enough that a
        # single weighing cannot be published twice.
        if total < self.config.step_off_threshold_kg:
            if self._empty_since is None:
                self._empty_since = sample.timestamp
            elif sample.timestamp - self._empty_since >= self.config.cooldown_seconds:
                self.state = State.IDLE
                self._empty_since = None
        else:
            self._empty_since = None
        return None

    # -- internals -----------------------------------------------------

    def _update_occupied(self, total: float) -> None:
        """Track load with hysteresis, using the same thresholds as the states.

        Two thresholds rather than one so a load hovering at the boundary
        cannot flap the binary sensor on and off in Home Assistant.
        """
        if self._occupied:
            self._occupied = total >= self.config.step_off_threshold_kg
        else:
            self._occupied = total >= self.config.step_on_threshold_kg

    def _feed_settling(self, sample: Sample, total: float) -> Optional[Measurement]:
        if total < self.config.step_off_threshold_kg:
            # Stepped off before anything settled. Nothing was published, so
            # there is nothing to debounce: go straight back to IDLE. Cooling
            # down here would lock the board for cooldown_seconds after every
            # aborted attempt, and COOLDOWN only clears once the board has been
            # *empty* that long, so stepping back on too soon left the tracker
            # stuck with someone standing on it.
            #
            # Publish the steadiest window of the visit rather than discarding
            # it: somebody who sways more than the stability tolerance would
            # otherwise get nothing at all, having stood there and stepped off
            # believing they had been weighed. The saved window is used instead
            # of the live one because the live one now holds the load falling
            # away.
            if self._best is not None:
                return self._finalise(sample.timestamp, stable=False, samples=self._best)
            # Too brief to have filled a single window: there is no honest
            # number here, so this was not a weighing.
            self._abandon()
            return None

        self._window.add(sample, self.config.window_seconds)
        assert self._settle_start is not None
        elapsed = sample.timestamp - self._settle_start
        full = (
            self._window.duration >= self.config.window_seconds
            and len(self._window.samples) >= self.config.min_samples
        )

        if full:
            totals = [self.adjusted_total(s) for s in self._window.samples]
            spread = max(totals) - min(totals)
            if spread <= self.config.stability_tolerance_kg:
                return self._finalise(sample.timestamp, stable=True)
            if spread < self._best_spread:
                self._best = list(self._window.samples)
                self._best_spread = spread

        if elapsed >= self.config.settle_timeout_seconds:
            # Never held still. Publish whatever the window holds anyway, with
            # a quality score capped at 50 so automations can filter it out —
            # a weak reading beats Home Assistant seeing no weighing at all.
            # A board reporting slower than min_samples/window_seconds never
            # fills the window, and used to fall through here silently.
            if self._best is not None:
                return self._finalise(sample.timestamp, stable=False, samples=self._best)
            if len(self._window.samples) >= MIN_PUBLISHABLE_SAMPLES:
                return self._finalise(sample.timestamp, stable=False)
            self._enter_cooldown()

        return None

    def _finalise(
        self, now: float, stable: bool, samples: Optional[List[Sample]] = None
    ) -> Measurement:
        samples = list(samples if samples is not None else self._window.samples)
        totals = [self.adjusted_total(s) for s in samples]
        spread = max(totals) - min(totals)
        weight = trimmed_mean(totals)
        std_dev = statistics.pstdev(totals) if len(totals) > 1 else 0.0
        sensors = tuple(
            statistics.fmean([s.sensors[i] for s in samples]) for i in range(4)
        )
        settle_s = now - self._settle_start if self._settle_start is not None else 0.0

        measurement = Measurement(
            timestamp=now,
            weight_kg=round(weight, 2),
            quality=quality_score(spread, self.config.stability_tolerance_kg, stable),
            spread_kg=round(spread, 3),
            std_dev_kg=round(std_dev, 3),
            sample_count=len(samples),
            duration_s=round(self._window.duration, 2),
            settle_s=round(settle_s, 2),
            sensors_kg=tuple(round(v, 2) for v in sensors),  # type: ignore[arg-type]
            tare_kg=round(self._tare, 3),
            stable=stable,
        )
        self._enter_cooldown()
        return measurement

    def _enter_cooldown(self) -> None:
        """Hold off after a published weighing, so one step-on gives one result."""
        self.state = State.COOLDOWN
        self._window.clear()
        self._settle_start = None
        self._empty_since = None
        self._forget_best()

    def _abandon(self) -> None:
        """Drop an unfinished attempt and be ready for the next one at once."""
        self.state = State.IDLE
        self._window.clear()
        self._settle_start = None
        self._empty_since = None
        self._forget_best()

    def _forget_best(self) -> None:
        self._best = None
        self._best_spread = float("inf")

    def _track_zero(self, sample: Sample) -> None:
        """Re-learn where zero is while the board sits empty."""
        if not self.config.auto_tare:
            return
        raw = sample.total
        if abs(raw - self._tare) > self.config.max_tare_kg:
            # Something is on the board, or the board is wildly off. Either way
            # this is not a reading of "empty".
            self._zero_samples.clear()
            return
        self._zero_samples.append(raw)
        if len(self._zero_samples) > 200:
            self._zero_samples.popleft()
        if len(self._zero_samples) >= 50:
            candidate = statistics.median(self._zero_samples)
            if abs(candidate) <= self.config.max_tare_kg:
                self._tare = candidate

    def reset(self) -> None:
        """Forget all state; used when the board disconnects and comes back."""
        self.state = State.IDLE
        self._window.clear()
        self._zero_samples.clear()
        self._settle_start = None
        self._empty_since = None
        self._occupied = False
        self._forget_best()
