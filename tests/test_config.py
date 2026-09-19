import pytest

from wiiscale.config import Config, load_config, validate


def write(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


def test_defaults_when_no_file(tmp_path):
    config = load_config(tmp_path / "missing.yaml", environ=False)
    assert config.mqtt.port == 1883
    assert config.board.unit_scale == 0.01
    assert config.measurement.auto_tare is True


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
        (lambda c: setattr(c.mqtt, "port", 0), "port"),
    ],
)
def test_validation_rejects_nonsense(mutate, message):
    config = Config()
    mutate(config)
    with pytest.raises(ValueError, match=message):
        validate(config)
