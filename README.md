# body-stats

Weigh yourself on a Wii Balance Board and get the number into Home Assistant.

A small Python service that runs on a Raspberry Pi: it reads the four load
cells in the board, waits until you stop moving, and publishes the weight to
Home Assistant over MQTT with auto-discovery. Nothing to add to
`configuration.yaml` — the entities show up on their own.

You step on the board, stand still for a couple of seconds, step off. Done.

One thing to get right before you start: **install Raspberry Pi OS bookworm,
not trixie.** Wii peripherals pair with a PIN of six raw bytes, which BlueZ
supplied from a plugin that upstream deleted in version 5.80. Bookworm ships
5.66 and still has it, so the board pairs with plain `bluetoothctl` and its
power button works — verified on a Pi Zero W. Trixie ships 5.82, where the
board cannot be paired at all; wiiscale still reads it there over raw L2CAP,
but you press the SYNC button inside the battery compartment before every
weighing. [docs/pairing.md](docs/pairing.md) has the recipe and the whole
story, including the routes that look promising and are not.

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
used to ship a Wii-specific plugin that knew the odd PIN these devices use, so
`bluetoothctl pair` just worked — see [docs/pairing.md](docs/pairing.md) for
what happened to it and what to do instead.

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
5. **Release.** A couple of seconds after you step off, the Bluetooth link is
   dropped so the board powers itself down. It has no off timer of its own
   while a host is connected, so without this it would stay awake until the
   batteries ran out.

Not everybody can hold that still, so it never asks you to. If the spread
never drops below the tolerance, the steadiest two seconds of the visit are
published anyway — when you step off, or after `settle_timeout_seconds` if you
are still standing there. Those readings carry a quality score of at most 50,
so an automation can treat them differently, but you always get a number.
Stepping off is deliberately read from the *saved* window rather than the
current one, because the current one is full of the load falling away.

The one case that publishes nothing is standing on the board for less than one
window. There is no honest weight to report from half a second of somebody
shifting their balance onto a platform, so none is invented.

## Getting started

```bash
git clone https://github.com/larsabrasha/body-stats.git
cd body-stats
sudo ./deploy/install.sh
```

Then:

1. **Pair the board** — follow [docs/pairing.md](docs/pairing.md). On
   bookworm it is `bluetoothctl`, the SYNC button and three commands, and the
   default `evdev` backend then needs no configuration at all. On trixie you
   cannot pair, so set `board.backend` to `l2cap` and put the board's address
   in `board.address` instead.
2. **Configure** `/etc/wiiscale/config.yaml` — broker address and username —
   and put the password in `/etc/wiiscale/wiiscale.env` as
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

`deploy/l2cap-probe.py` is the one diagnostic worth keeping around: given the
board's address it connects over raw Bluetooth, prints the calibration table
and ten live readings, and needs neither pairing nor wiiscale itself. It is
the quickest way to tell a board problem from a software one.

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
- `measurement.settle_timeout_seconds` — how long it waits before publishing
  the steadiest window it saw rather than a settled one.
- `board.idle_disconnect_seconds` — how long the board may sit empty before
  the link is dropped so it can power itself off. Nothing on Linux does this
  for you, and a board left connected stays awake until its batteries die.
- `board.unit_scale` — only if the board reads consistently wrong by a fixed
  *percentage*. (The `l2cap` backend uses `board.weight_scale` for the same
  job.) A fixed number of grams is already handled by `auto_tare`.

## Backup and restore

Almost everything on the Pi comes back from this repository: both systemd
units, the script that keeps the adapter connectable, the module-load file
and a fresh config are all installed by `deploy/install.sh`. Three things do
not, and they are the ones worth keeping a copy of.

- `/var/lib/bluetooth/<adapter>/<board>/` — the pairing. Recreating it means
  the SYNC button, `bluetoothctl` and the whole dance in
  [docs/pairing.md](docs/pairing.md) again.
- `/etc/wiiscale/config.yaml` — your broker and your thresholds.
- `/etc/wiiscale/wiiscale.env` — the broker password.

```bash
sudo tar czf ~/wiiscale-backup-$(date +%F).tar.gz     /var/lib/bluetooth /etc/wiiscale
```

Move that file off the Pi: it protects you against a dead SD card, which is
what actually happens to SD cards, and it cannot do that while it is on one.
It holds the broker password in plain text and the board's link key, so treat
it as a secret.

To restore onto a fresh card: write Raspberry Pi OS **bookworm**, clone this
repository, run `sudo ./deploy/install.sh`, then unpack the archive over the
top and restart.

```bash
sudo tar xzf wiiscale-backup-<date>.tar.gz -C /
```

```bash
sudo systemctl restart bluetooth wiiscale
```

The board should reconnect on its power button without being paired again.

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
