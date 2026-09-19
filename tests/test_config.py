import textwrap

import pytest

from wiiscale.config import Config, load_config, validate


def write(tmp_path, text):
    # dedent so an indented triple-quoted block ends up at column zero.
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(text))
    return path


def valid_config() -> Config:
    return Config()


def test_defaults_when_no_file(tmp_path):
    config = load_config(tmp_path / "missing.yaml")
    assert config.mqtt.port == 1883
    assert config.board.backend == "evdev"
    assert config.board.weight_scale == 1.0
    assert config.measurement.auto_tare is True


def test_l2cap_backend_demands_an_address():
    config = Config()
    config.board.backend = "l2cap"
    assert config.board.address is None
    with pytest.raises(ValueError, match="board.address is required"):
        validate(config)


def test_evdev_backend_needs_no_address():
    config = Config()
    assert config.board.backend == "evdev"
    assert config.board.address is None
    validate(config)


def test_unknown_backend_is_rejected():
    config = valid_config()
    config.board.backend = "hidraw"
    with pytest.raises(ValueError, match="board.backend"):
        validate(config)


def test_yaml_overrides_defaults(tmp_path):
    path = write(
        tmp_path,
        """
        mqtt:
          host: 10.0.0.5
          port: 8883
          username: scale
        measurement:
          window_seconds: 3.5
          auto_tare: false
        """,
    )
    config = load_config(path, environ=False)
    assert config.mqtt.host == "10.0.0.5"
    assert config.mqtt.port == 8883
    assert config.mqtt.username == "scale"
    assert config.measurement.window_seconds == 3.5
    assert config.measurement.auto_tare is False
    # Untouched values keep their defaults.
    assert config.mqtt.discovery_prefix == "homeassistant"


def test_environment_beats_yaml(tmp_path, monkeypatch):
    path = write(tmp_path, "mqtt:\n  host: from-yaml\n  port: 1883\n")
    monkeypatch.setenv("WIISCALE_MQTT_HOST", "from-env")
    monkeypatch.setenv("WIISCALE_MQTT_PASSWORD", "s3cret")
    monkeypatch.setenv("WIISCALE_MQTT_TLS", "true")
    config = load_config(path)
    assert config.mqtt.host == "from-env"
    assert config.mqtt.password == "s3cret"
    assert config.mqtt.tls is True


def test_environment_values_are_typed(monkeypatch):
    monkeypatch.setenv("WIISCALE_MQTT_PORT", "8884")
    monkeypatch.setenv("WIISCALE_MEASUREMENT_WINDOW_SECONDS", "2.5")
    monkeypatch.setenv("WIISCALE_MEASUREMENT_AUTO_TARE", "off")
    config = load_config(None)
    assert config.mqtt.port == 8884
    assert config.measurement.window_seconds == 2.5
    assert config.measurement.auto_tare is False


def test_unknown_option_is_rejected(tmp_path):
    path = write(tmp_path, "mqtt:\n  hostname: oops\n")
    with pytest.raises(ValueError, match="unknown option"):
        load_config(path, environ=False)


def test_unknown_section_is_rejected(tmp_path):
    path = write(tmp_path, "broker:\n  host: x\n")
    with pytest.raises(ValueError, match="unknown section"):
        load_config(path, environ=False)


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda c: setattr(c.measurement, "step_off_threshold_kg", 9.0), "step_off"),
        (lambda c: setattr(c.measurement, "window_seconds", 0), "window_seconds"),
        (lambda c: setattr(c.measurement, "settle_timeout_seconds", 1.0), "settle_timeout"),
        (lambda c: setattr(c.measurement, "min_samples", 1), "min_samples"),
        (lambda c: setattr(c.measurement, "stability_tolerance_kg", 0), "stability_tolerance"),
        (lambda c: setattr(c.board, "unit_scale", 0), "unit_scale"),
        (lambda c: setattr(c.board, "weight_scale", 0), "weight_scale"),
        (lambda c: setattr(c.mqtt, "port", 0), "port"),
    ],
)
def test_validation_rejects_nonsense(mutate, message):
    config = valid_config()
    mutate(config)
    with pytest.raises(ValueError, match=message):
        validate(config)
