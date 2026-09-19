# body-stats

Weigh yourself on a Wii Balance Board and get the number into Home Assistant.

A small Python service that runs on a Raspberry Pi: it reads the four load
cells in the board, waits until you stop moving, and publishes the weight to
Home Assistant over MQTT with auto-discovery. Nothing to add to
`configuration.yaml` — the entities show up on their own.

You step on the board, stand still for a couple of seconds, step off. Done.

## Hardware

Runs on a **Raspberry Pi Zero WH** — cheapest, smallest, and the code in this
repository works on it unchanged. A 3B+ is fine too, but it is overkill.
[docs/hardware.md](docs/hardware.md) goes through the choice, including why an
ESP32 (M5Stack Atom Lite, M5StickC Plus) is possible but means writing the
firmware yourself — and which ESP32 variants cannot talk to the board at all.

## Why a Raspberry Pi and not a Mac app

Linux already has a driver for the hardware, `hid-wiimote`. Once the board is
paired it appears as an ordinary input device whose four absolute axes carry
the load cells, already run through the board's own calibration table. BlueZ
also ships a Wii-specific plugin that knows the odd PIN these devices use, so
`bluetoothctl pair` just works.

macOS has neither. A Mac app would have meant implementing the HID protocol
and the calibration maths by hand, against a Bluetooth stack that is famously
unhappy with Wii peripherals — and the Mac would have to be awake and nearby
every time you weigh yourself. A Pi that sits next to the scale and is always
on is simply the right tool.

## How it measures

The raw data from the board shakes constantly — you sway, the platform flexes,
the sensors are cheap. So the service does not grab one value; it waits for a
*window* of samples that agree with each other:

1. **Idle.** The board is empty. The zero point is learned continuously
   (`auto_tare`), so a board that drifts a few hundred grams does not skew
   every measurement.
2. **Settling.** Somebody is on the board. A 2-second sliding window fills up.
   When the spread (max − min) in the window is below 0.3 kg, it counts as
   stable.
3. **Publishing.** The weight is a trimmed mean of the window — the extremes
   are thrown away, so a foot shifting does not drag the result with it. A
   quality score of 0–100 comes along, saying how well the samples agreed.
4. **Lockout.** The board is ignored until it has been empty for a while, so
   one step-on gives exactly one measurement.

If it never settles, the service gives up after 20 seconds and publishes
anyway, but with a quality score of at most 50 so you can filter it out in
Home Assistant. The one case it publishes nothing is a board reporting so
slowly that fewer than two samples landed in the window — there is nothing to
average.

## Getting started

```bash
git clone https://github.com/larsabrasha/body-stats.git
cd body-stats
sudo ./deploy/install.sh
```

Then:

1. **Pair the board** — see [docs/pairing.md](docs/pairing.md). A one-time job;
   after that the board connects by itself when you press its power button.
2. **Configure** `/etc/wiiscale/config.yaml` (broker address and username), and
   put the password in `/etc/wiiscale/wiiscale.env` as
   `WIISCALE_MQTT_PASSWORD=...`.
3. **Test the readings** before you wire up MQTT:

   ```bash
   sudo wiiscale monitor
   ```

   An empty board should show around 0.00 kg. Step on — the total should match
   your usual bathroom scale within a few hundred grams.
4. **Start the service:**

   ```bash
   sudo systemctl start wiiscale
   journalctl -u wiiscale -f
   ```

## Entities in Home Assistant

| Entity | Description |
| --- | --- |
| `sensor.wii_balance_board_weight` | The weight in kg. The whole measurement comes along as attributes. |
| `sensor.wii_balance_board_quality` | 0–100, how stable the measurement was. |
| `sensor.wii_balance_board_last_measurement` | When the last weighing happened. |
| `binary_sensor.wii_balance_board_occupied` | Whether somebody is on the board right now. |

Because the weight sensor has `state_class: measurement`, Home Assistant keeps
long-term statistics automatically — a statistics card gives you the trend line
with no further work. Example automations, and how to filter out bad
measurements, are in [docs/home-assistant.md](docs/home-assistant.md).

## Commands

```bash
wiiscale run        # the service (what systemd starts)
wiiscale monitor    # live readings in the terminal, does not touch MQTT
wiiscale devices    # lists input devices that look like a balance board
wiiscale config     # prints the configuration as it was actually parsed
```

All of them take `--config <file>` and `--log-level DEBUG`, on either side of
the subcommand.

## Configuration

Every value is documented in
[deploy/config.example.yaml](deploy/config.example.yaml). Each setting can also
be set with an environment variable named `WIISCALE_<SECTION>_<KEY>`, for
example `WIISCALE_MQTT_PASSWORD` or `WIISCALE_MEASUREMENT_WINDOW_SECONDS` —
handy for secrets that should not sit in the YAML file.

What you are most likely to touch:

- `measurement.stability_tolerance_kg` — raise it if measurements never
  complete, lower it for stricter readings.
- `measurement.window_seconds` — a longer window gives a steadier value but
  means standing still for longer.
- `board.unit_scale` — only if the board reads consistently wrong by a fixed
  *percentage*. A fixed number of grams is already handled by `auto_tare`.

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The tests run anywhere — the measurement and MQTT logic is free of evdev, and
the board is fed in as synthetic samples.

## License

MIT.
