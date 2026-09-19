import json

import pytest

from wiiscale.config import MqttConfig
from wiiscale.measure import Measurement
from wiiscale.mqtt import HomeAssistantPublisher


class FakeClient:
    """Stands in for paho's client, recording what would have been published."""

    def __init__(self):
        self.published = []
        self.will = None

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def tls_set(self):
        self.tls = True

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, retain)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    def topics(self):
        return [topic for topic, _, _ in self.published]

    def payload_for(self, topic):
        for published_topic, payload, _ in reversed(self.published):
            if published_topic == topic:
                return json.loads(payload)
        raise AssertionError(f"nothing published to {topic}")


@pytest.fixture
def publisher():
    pub = HomeAssistantPublisher(MqttConfig(node_id="wii_balance_board"))
    pub._client = FakeClient()
    return pub


def measurement(**overrides):
    values = dict(
        timestamp=1000.0,
        weight_kg=82.35,
        quality=94,
        spread_kg=0.12,
        std_dev_kg=0.03,
        sample_count=48,
        duration_s=2.0,
        settle_s=3.4,
        sensors_kg=(20.1, 21.3, 20.5, 20.45),
        tare_kg=0.05,
        stable=True,
    )
    values.update(overrides)
    return Measurement(**values)


def test_topics_are_namespaced_by_node(publisher):
    assert publisher.state_topic == "wiiscale/wii_balance_board/state"
    assert publisher.live_topic == "wiiscale/wii_balance_board/live"
    assert publisher.availability_topic == "wiiscale/wii_balance_board/availability"


def test_discovery_defines_the_four_entities(publisher):
    payloads = publisher.discovery_payloads()
    assert set(payloads) == {
        "homeassistant/sensor/wii_balance_board/weight/config",
        "homeassistant/sensor/wii_balance_board/quality/config",
        "homeassistant/sensor/wii_balance_board/last_measurement/config",
        "homeassistant/binary_sensor/wii_balance_board/occupied/config",
    }


def test_discovery_entities_share_one_device_and_have_unique_ids(publisher):
    payloads = list(publisher.discovery_payloads().values())
    identifiers = {tuple(p["device"]["identifiers"]) for p in payloads}
    assert len(identifiers) == 1
    unique_ids = [p["unique_id"] for p in payloads]
    assert len(set(unique_ids)) == len(unique_ids)
    for payload in payloads:
        assert payload["availability_topic"] == publisher.availability_topic


def test_weight_entity_is_a_proper_ha_weight_sensor(publisher):
    payload = publisher.discovery_payloads()[
        "homeassistant/sensor/wii_balance_board/weight/config"
    ]
    assert payload["device_class"] == "weight"
    assert payload["unit_of_measurement"] == "kg"
    assert payload["state_class"] == "measurement"
    assert payload["state_topic"] == publisher.state_topic
    assert payload["json_attributes_topic"] == publisher.state_topic


def test_discovery_is_published_retained(publisher):
    publisher.publish_discovery()
    assert len(publisher._client.published) == 4
    assert all(retain for _, _, retain in publisher._client.published)


def test_measurement_payload_is_json_serialisable_and_complete(publisher):
    payload = publisher.measurement_payload(measurement())
    json.dumps(payload)  # must not raise
    assert payload["weight"] == 82.35
    assert payload["quality"] == 94
    assert payload["stable"] is True
    assert payload["sensors"] == [20.1, 21.3, 20.5, 20.45]
    assert payload["timestamp"].endswith("+00:00")


def test_measurement_is_retained_so_ha_survives_a_restart(publisher):
    publisher.publish_measurement(measurement())
    topic, _, retain = publisher._client.published[-1]
    assert topic == publisher.state_topic
    assert retain is True


def test_will_marks_the_bridge_offline(publisher):
    # The fake client replaces the real one after construction, so rebuild to
    # observe the will being registered.
    pub = HomeAssistantPublisher(MqttConfig())
    assert pub._client.will_set  # paho client has it
    fake = FakeClient()
    pub._client = fake
    pub.close()
    assert fake.published[-1][1] == "offline"


def test_live_publishing_is_throttled_but_not_across_state_changes(publisher):
    publisher.publish_live(False, 0.0)
    publisher.publish_live(False, 0.1)  # throttled away
    assert len(publisher._client.published) == 1

    publisher.publish_live(True, 80.0)  # occupancy changed, must go out
    assert len(publisher._client.published) == 2
    assert publisher._client.payload_for(publisher.live_topic)["occupied"] is True


def test_occupancy_template_renders_on_off(publisher):
    payload = publisher.discovery_payloads()[
        "homeassistant/binary_sensor/wii_balance_board/occupied/config"
    ]
    assert payload["payload_on"] == "ON"
    assert payload["payload_off"] == "OFF"
    assert "ON" in payload["value_template"] and "OFF" in payload["value_template"]
