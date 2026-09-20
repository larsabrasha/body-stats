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

## Telling people apart

One board, one weight sensor, and several people in the house. If everybody's
weight sits in a clearly separate band — say a child at 25 kg, one adult at
59 and another at 106 — a weight range is enough to say who just stood on it.

It has to be a *trigger*-based template sensor. A plain template sensor, the
kind the Helpers UI creates, would follow the shared weight continuously and
show whoever weighed themselves last; these latch, keeping each person's own
last reading until they weigh themselves again.

```yaml
template:
  - trigger:
      - platform: state
        entity_id: sensor.wii_balance_board_last_measurement
    condition:
      - condition: numeric_state
        entity_id: sensor.wii_balance_board_weight
        above: 85
        below: 140
    sensor:
      - name: Weight adult one
        unique_id: wiiscale_weight_adult_one
        unit_of_measurement: kg
        device_class: weight
        state_class: measurement
        state: "{{ states('sensor.wii_balance_board_weight') | float }}"
        attributes:
          quality: "{{ state_attr('sensor.wii_balance_board_weight', 'quality') }}"
```

Repeat the block per person with that person's range. Two details are
deliberate.

The trigger is `last_measurement`, not the weight. The timestamp changes on
every weighing, while the weight can come out identical twice in a row — and
then nothing would fire.

The ranges have gaps between them. A weighing at 42 kg, between the child's
band and the adult's, belongs to nobody and is dropped. That is better than
assigning it to whichever band is nearest and quietly corrupting someone's
trend.

To ignore the readings that never settled, add one more condition:

```yaml
      - condition: template
        value_template: >
          {{ state_attr('sensor.wii_balance_board_weight', 'quality') | int(0) >= 30 }}
```

Each of these sensors carries `state_class: measurement`, so Home Assistant
keeps long-term statistics per person and a statistics graph card each gives
you three trend lines.

Two limits worth knowing before you rely on it. A guest lands in whichever
band they happen to fit and is recorded as that person: weight alone cannot
identify anybody. And a growing child walks up through the bands, so the
boundaries need moving apart every so often — check them whenever a trend
line does something surprising.

## One automation per person, without repeating yourself

`deploy/home-assistant/weighing-in-range.yaml` is a blueprint: write the
pattern once, then create one automation per person from it in the UI, filling
in that person's weight range and what should happen.

Copy it onto the Home Assistant machine:

```bash
scp deploy/home-assistant/weighing-in-range.yaml \
    root@homeassistant.local:/config/blueprints/automation/wiiscale/
```

Create the directory first if it does not exist. Then Settings → Automations
→ Blueprints → *Create automation*, pick it, and fill in the range.

It triggers on the MQTT message rather than on the weight sensor, which
matters more than it looks. The weight and the timestamp are two entities
reading the same message; Home Assistant updates them in no guaranteed order,
so an automation triggered on one and reading the other can report the
*previous* weighing. Reading the payload directly cannot race with itself, and
`trigger.payload_json` carries every field — `weight`, `quality`, `stable`,
`spread`, `timestamp` and the rest.

The text is built for you. An automation's actions can use `{{ message }}`
— "Lars: 106.19 kg", or just "106.19 kg" when you leave the name empty — and
`{{ title }}` for notifications that have one, as well as `{{ person }}`,
`{{ weight }}` and `{{ quality }}` separately, and `{{ trigger.payload_json }}`
for anything else in the weighing.

The blueprint also asks how old a weighing may be, defaulting to two minutes.
The state topic is retained so that entities survive a restart, which means
Home Assistant receives the last weighing again on every restart — without the
age check you would get a notification about a days-old weighing each time.
