# Home Assistant

The bridge publishes MQTT discovery configs, so there is nothing to add to
`configuration.yaml`. Once `wiiscale` connects to the broker, a device called
**Wii Balance Board** appears under Settings → Devices & Services → MQTT with
four entities:

| Entity | Type | Notes |
| --- | --- | --- |
| `sensor.wii_balance_board_weight` | sensor | kg, `device_class: weight`, `state_class: measurement`. Carries the whole measurement payload as attributes. |
| `sensor.wii_balance_board_quality` | sensor | 0–100. How much the samples agreed. Diagnostic. |
| `sensor.wii_balance_board_last_measurement` | sensor | Timestamp of the last weighing. Diagnostic. |
| `binary_sensor.wii_balance_board_occupied` | binary sensor | Live occupancy, `device_class: occupancy`. |

## Topics

```
wiiscale/wii_balance_board/availability   online | offline   (retained, LWT)
wiiscale/wii_balance_board/state          last weighing, JSON (retained)
wiiscale/wii_balance_board/live           current load, JSON  (retained)
```

A finished weighing looks like this:

```json
{
  "weight": 82.35,
  "quality": 94,
  "stable": true,
  "spread": 0.12,
  "std_dev": 0.03,
  "samples": 96,
  "duration": 2.0,
  "settle_time": 3.4,
  "sensors": [20.1, 21.3, 20.5, 20.45],
  "tare": 0.05,
  "timestamp": "2026-09-19T07:14:32+00:00"
}
```

## Reading the quality score

It scores how tightly the samples in the measurement window agreed:

- **100** — dead still.
- **80** — exactly at `stability_tolerance_kg`, the loosest window still
  accepted as stable.
- **≤ 50** — the weighing never settled. It was published anyway, either
  because you stepped off or because `settle_timeout_seconds` ran out, using
  the steadiest window of the visit. Treat these with suspicion, but note that
  for somebody who never manages to stand still these are the only readings
  there will ever be.

`stable` says the same thing as a boolean: `true` means the window held still
on its own, `false` means it was published without ever settling.

## Only recording measurements you trust

The weight sensor updates on every weighing, good or bad. If you log long-term
trends, filter on quality rather than on the raw sensor:

```yaml
# configuration.yaml
template:
  - sensor:
      - name: "Weight (trusted)"
        unique_id: weight_trusted
        unit_of_measurement: "kg"
        device_class: weight
        state_class: measurement
        availability: >
          {{ states('sensor.wii_balance_board_weight') not in
             ['unknown', 'unavailable'] }}
        state: >
          {% set w = states('sensor.wii_balance_board_weight') %}
          {% set q = states('sensor.wii_balance_board_quality') | int(0) %}
          {% if q >= 80 %}{{ w }}{% else %}{{ states('sensor.weight_trusted') }}{% endif %}
```

## Example automation

Notify on a good weighing, and stay quiet about a bad one:

```yaml
automation:
  - alias: "Weight recorded"
    triggers:
      - trigger: state
        entity_id: sensor.wii_balance_board_weight
    conditions:
      - condition: numeric_state
        entity_id: sensor.wii_balance_board_quality
        above: 79
    actions:
      - action: notify.mobile_app
        data:
          message: >
            {{ states('sensor.wii_balance_board_weight') }} kg
            ({{ states('sensor.wii_balance_board_quality') }} %)
```

Because the state topic is retained, this fires again whenever Home Assistant
restarts and re-reads the topic if the value differs from what it had. Add a
`for:` or a condition on `sensor.wii_balance_board_last_measurement` if that
bothers you.

## Long-term statistics

`state_class: measurement` means Home Assistant keeps long-term statistics for
the weight sensor automatically — a statistics graph card on
`sensor.wii_balance_board_weight` gives you a trend line with no extra setup.
