"""Configuration loading.

Values come from three layers, later layers winning:

1. the dataclass defaults below,
2. a YAML file (``/etc/wiiscale/config.yaml`` by default),
3. environment variables named ``WIISCALE_<SECTION>_<KEY>``, e.g.
   ``WIISCALE_MQTT_PASSWORD``.

The environment layer exists mainly so secrets can stay out of the YAML file
and be handed to the service through a systemd ``EnvironmentFile``.
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

DEFAULT_CONFIG_PATH = Path("/etc/wiiscale/config.yaml")
ENV_PREFIX = "WIISCALE_"


@dataclass
class BoardConfig:
    """How to find the board and how to read its numbers."""

    # Substring matched (case-insensitively) against evdev device names. The
    # kernel's hid-wiimote driver calls the extension device
    # "Nintendo Wii Remote Balance Board".
    device_name: str = "Balance Board"
    # hid-wiimote reports each load cell in hundredths of a kilogram, so the
    # raw units are multiplied by 0.01 to get kilograms. Only touch this if you
    # are feeding the daemon from something other than hid-wiimote.
    unit_scale: float = 0.01
    # How long to wait between scans of /dev/input while no board is present.
    scan_interval_seconds: float = 2.0


@dataclass
class MeasurementConfig:
    """Thresholds for the step-on / settle / step-off state machine."""

    # Total load above which we consider somebody to be standing on the board.
    step_on_threshold_kg: float = 5.0
    # Total load below which the board counts as empty again. Kept lower than
    # step_on_threshold_kg so a wobble near the edge cannot rattle the state
    # machine back and forth.
    step_off_threshold_kg: float = 3.0
    # Length of the sliding window the final weight is computed from.
    window_seconds: float = 2.0
    # A window whose max-min spread is at or below this counts as stable.
    stability_tolerance_kg: float = 0.3
    # Give up waiting for stability after this long and publish the best window
    # we have, flagged with a lower quality score.
    settle_timeout_seconds: float = 20.0
    # Refuse to publish a window built from fewer samples than this.
    min_samples: int = 20
    # After publishing, ignore the board until it has been empty this long.
    cooldown_seconds: float = 5.0
    # Track the empty-board reading and subtract it, so a board that drifts a
    # few hundred grams off zero does not bias every measurement.
    auto_tare: bool = True
    # Never trust an auto-tare offset larger than this; beyond it something is
    # resting on the board rather than the board being miscalibrated.
    max_tare_kg: float = 2.0


@dataclass
class MqttConfig:
    """Broker connection and topic layout."""

    host: str = "homeassistant.local"
    port: int = 1883
    username: Optional[str] = None
    password: Optional[str] = None
    client_id: str = "wiiscale"
    # Topics are built as <base_topic>/<node_id>/<suffix>.
    base_topic: str = "wiiscale"
    node_id: str = "wii_balance_board"
    # Where Home Assistant listens for MQTT discovery messages.
    discovery_prefix: str = "homeassistant"
    device_name: str = "Wii Balance Board"
    tls: bool = False
    keepalive: int = 60
    # Minimum seconds between live (occupancy / current load) publishes.
    live_interval_seconds: float = 0.5


@dataclass
class LoggingConfig:
    level: str = "INFO"


@dataclass
class Config:
    board: BoardConfig = field(default_factory=BoardConfig)
    measurement: MeasurementConfig = field(default_factory=MeasurementConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


_SECTIONS = {f.name: f.type for f in dataclasses.fields(Config)}


# ``from __future__ import annotations`` makes dataclasses store field types as
# strings, so the coercion table is keyed by name rather than by type object.
_TRUTHY = {"1", "true", "yes", "on"}


def _type_name(target_type: Any) -> str:
    name = target_type if isinstance(target_type, str) else getattr(target_type, "__name__", str(target_type))
    match = re.fullmatch(r"Optional\[(.+)\]", name)
    if match:
        name = match.group(1)
    return name


def _coerce(value: Any, target_type: Any) -> Any:
    """Convert a YAML/env scalar to the type declared on the dataclass field."""
    if value is None:
        return None
    name = _type_name(target_type)
    if name == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in _TRUTHY
    if name == "int":
        return int(value)
    if name == "float":
        return float(value)
    if name == "str":
        return str(value)
    return value


def _apply(section: Any, values: Dict[str, Any], source: str) -> None:
    known = {f.name: f for f in dataclasses.fields(section)}
    for key, value in values.items():
        field_def = known.get(key)
        if field_def is None:
            raise ValueError(f"{source}: unknown option {key!r} in section {type(section).__name__}")
        setattr(section, key, _coerce(value, field_def.type))


def _env_overrides(config: Config) -> None:
    for section_name in _SECTIONS:
        section = getattr(config, section_name)
        for field_def in dataclasses.fields(section):
            env_name = f"{ENV_PREFIX}{section_name}_{field_def.name}".upper()
            if env_name in os.environ:
                raw = os.environ[env_name]
                setattr(section, field_def.name, _coerce(raw, field_def.type))


def load_config(path: Optional[Path] = None, environ: bool = True) -> Config:
    """Build a Config from an optional YAML file plus the environment.

    A missing file is not an error: the defaults plus environment variables are
    enough to run against a broker on the default port.
    """
    config = Config()

    if path is not None and path.exists():
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected a mapping at the top level")
        for section_name, values in raw.items():
            if section_name not in _SECTIONS:
                raise ValueError(f"{path}: unknown section {section_name!r}")
            if values is None:
                continue
            if not isinstance(values, dict):
                raise ValueError(f"{path}: section {section_name!r} must be a mapping")
            _apply(getattr(config, section_name), values, str(path))

    if environ:
        _env_overrides(config)

    validate(config)
    return config


def validate(config: Config) -> None:
    """Reject configurations that would make the state machine misbehave."""
    m = config.measurement
    if m.step_off_threshold_kg >= m.step_on_threshold_kg:
        raise ValueError("measurement.step_off_threshold_kg must be below step_on_threshold_kg")
    if m.window_seconds <= 0:
        raise ValueError("measurement.window_seconds must be positive")
    if m.settle_timeout_seconds <= m.window_seconds:
        raise ValueError("measurement.settle_timeout_seconds must exceed window_seconds")
    if m.min_samples < 2:
        raise ValueError("measurement.min_samples must be at least 2")
    if m.stability_tolerance_kg <= 0:
        raise ValueError("measurement.stability_tolerance_kg must be positive")
    if config.board.unit_scale <= 0:
        raise ValueError("board.unit_scale must be positive")
    if not 1 <= config.mqtt.port <= 65535:
        raise ValueError("mqtt.port must be a valid port number")
