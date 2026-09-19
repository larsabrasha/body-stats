"""Publishing to Home Assistant over MQTT, with discovery.

Home Assistant creates the entities itself from the retained discovery configs
published here, so there is nothing to add to configuration.yaml. Everything
hangs off one device so the four entities group together in the UI.

Topic layout::

    <base>/<node>/availability   online | offline   (retained, also the LWT)
    <base>/<node>/state          the last finished weighing, JSON (retained)
    <base>/<node>/live           current load and occupancy, JSON (retained)
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt

from . import __version__
from .config import MqttConfig
from .measure import Measurement

log = logging.getLogger(__name__)

PAYLOAD_ONLINE = "online"
PAYLOAD_OFFLINE = "offline"


def monotonic_to_iso(monotonic_timestamp: float) -> str:
    """Convert a time.monotonic() stamp to a wall-clock ISO 8601 string.

    Samples are stamped with the monotonic clock so that durations stay correct
    across NTP steps; Home Assistant wants a real date, so the offset between
    the two clocks is applied at publish time.
    """
    wall = time.time() - (time.monotonic() - monotonic_timestamp)
    return datetime.fromtimestamp(wall, tz=timezone.utc).isoformat(timespec="seconds")


class HomeAssistantPublisher:
    """Owns the broker connection and the Home Assistant entity definitions."""

    def __init__(self, config: MqttConfig) -> None:
        self.config = config
        self._last_live = 0.0
        self._last_occupied: Optional[bool] = None
        self._client = self._build_client()

    # -- topics --------------------------------------------------------

    @property
    def _prefix(self) -> str:
        return f"{self.config.base_topic}/{self.config.node_id}"

    @property
    def availability_topic(self) -> str:
        return f"{self._prefix}/availability"

    @property
    def state_topic(self) -> str:
        return f"{self._prefix}/state"

    @property
    def live_topic(self) -> str:
        return f"{self._prefix}/live"

    # -- discovery -----------------------------------------------------

    def _device_block(self) -> Dict[str, Any]:
        return {
            "identifiers": [f"wiiscale_{self.config.node_id}"],
            "name": self.config.device_name,
            "manufacturer": "Nintendo",
            "model": "RVL-WBC-01",
            "sw_version": __version__,
        }

    def _common(self) -> Dict[str, Any]:
        return {
            "device": self._device_block(),
            "origin": {"name": "wiiscale", "sw_version": __version__},
            "availability_topic": self.availability_topic,
            "payload_available": PAYLOAD_ONLINE,
            "payload_not_available": PAYLOAD_OFFLINE,
        }

    def discovery_payloads(self) -> Dict[str, Dict[str, Any]]:
        """Map of discovery topic -> config payload, one per entity."""
        node = self.config.node_id
        prefix = self.config.discovery_prefix
        common = self._common()

        weight = {
            **common,
            "name": "Weight",
            "unique_id": f"{node}_weight",
            "object_id": f"{node}_weight",
            "state_topic": self.state_topic,
            "value_template": "{{ value_json.weight }}",
            # Everything in the state payload also lands as attributes, so the
            # sensor's more-info dialog shows how the number was arrived at.
            "json_attributes_topic": self.state_topic,
            "device_class": "weight",
            "state_class": "measurement",
            "unit_of_measurement": "kg",
            "suggested_display_precision": 2,
            "icon": "mdi:scale-bathroom",
        }
        quality = {
            **common,
            "name": "Measurement quality",
            "unique_id": f"{node}_quality",
            "object_id": f"{node}_quality",
            "state_topic": self.state_topic,
            "value_template": "{{ value_json.quality }}",
            "state_class": "measurement",
            "unit_of_measurement": "%",
            "entity_category": "diagnostic",
            "icon": "mdi:check-decagram",
        }
        last = {
            **common,
            "name": "Last measurement",
            "unique_id": f"{node}_last_measurement",
            "object_id": f"{node}_last_measurement",
            "state_topic": self.state_topic,
            "value_template": "{{ value_json.timestamp }}",
            "device_class": "timestamp",
            "entity_category": "diagnostic",
        }
        occupied = {
            **common,
            "name": "Occupied",
            "unique_id": f"{node}_occupied",
            "object_id": f"{node}_occupied",
            "state_topic": self.live_topic,
            # Rendered to ON/OFF explicitly rather than relying on how Home
            # Assistant stringifies a JSON boolean.
            "value_template": "{{ 'ON' if value_json.occupied else 'OFF' }}",
            "payload_on": "ON",
            "payload_off": "OFF",
            "device_class": "occupancy",
            "icon": "mdi:foot-print",
        }

        return {
            f"{prefix}/sensor/{node}/weight/config": weight,
            f"{prefix}/sensor/{node}/quality/config": quality,
            f"{prefix}/sensor/{node}/last_measurement/config": last,
            f"{prefix}/binary_sensor/{node}/occupied/config": occupied,
        }

    # -- connection ----------------------------------------------------

    def _build_client(self) -> mqtt.Client:
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self.config.client_id,
        )
        if self.config.username:
            client.username_pw_set(self.config.username, self.config.password)
        if self.config.tls:
            client.tls_set()
        client.will_set(self.availability_topic, PAYLOAD_OFFLINE, qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code != 0:
            log.error("MQTT connection refused: %s", reason_code)
            return
        log.info("Connected to MQTT broker at %s:%s", self.config.host, self.config.port)
        # Republish on every (re)connect: a broker or Home Assistant restart
        # would otherwise leave the entities undefined or unavailable.
        self.publish_discovery()
        client.publish(self.availability_topic, PAYLOAD_ONLINE, qos=1, retain=True)

    def _on_disconnect(self, client, userdata, *args) -> None:
        log.warning("Disconnected from MQTT broker; paho will retry")

    def connect(self) -> None:
        self._client.connect_async(self.config.host, self.config.port, self.config.keepalive)
        self._client.loop_start()

    def close(self) -> None:
        try:
            self._client.publish(self.availability_topic, PAYLOAD_OFFLINE, qos=1, retain=True)
            self._client.loop_stop()
            self._client.disconnect()
        except Exception as exc:  # pragma: no cover - best effort on shutdown
            log.debug("Error during MQTT shutdown: %s", exc)

    # -- publishing ----------------------------------------------------

    def publish_discovery(self) -> None:
        for topic, payload in self.discovery_payloads().items():
            self._client.publish(topic, json.dumps(payload), qos=1, retain=True)

    @staticmethod
    def measurement_payload(measurement: Measurement) -> Dict[str, Any]:
        return {
            "weight": measurement.weight_kg,
            "quality": measurement.quality,
            "stable": measurement.stable,
            "spread": measurement.spread_kg,
            "std_dev": measurement.std_dev_kg,
            "samples": measurement.sample_count,
            "duration": measurement.duration_s,
            "settle_time": measurement.settle_s,
            "sensors": list(measurement.sensors_kg),
            "tare": measurement.tare_kg,
            "timestamp": monotonic_to_iso(measurement.timestamp),
        }

    def publish_measurement(self, measurement: Measurement) -> None:
        payload = self.measurement_payload(measurement)
        log.info(
            "Weight %.2f kg (quality %d%%, spread %.3f kg, %d samples)",
            measurement.weight_kg,
            measurement.quality,
            measurement.spread_kg,
            measurement.sample_count,
        )
        self._client.publish(self.state_topic, json.dumps(payload), qos=1, retain=True)

    def publish_live(self, occupied: bool, total_kg: float, force: bool = False) -> None:
        """Publish current load, throttled so a 100 Hz stream cannot flood MQTT.

        A change in occupancy always goes out immediately; the load in between
        is rate-limited by ``live_interval_seconds``.
        """
        now = time.monotonic()
        changed = occupied != self._last_occupied
        if not force and not changed and now - self._last_live < self.config.live_interval_seconds:
            return
        self._last_live = now
        self._last_occupied = occupied
        payload = {"occupied": occupied, "total": round(total_kg, 2)}
        self._client.publish(self.live_topic, json.dumps(payload), qos=0, retain=True)
