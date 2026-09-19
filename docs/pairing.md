# Pairing the balance board with the Raspberry Pi

This only has to be done once. Afterwards the board reconnects by itself when
you press its power button, and `wiiscale` picks it up within a couple of
seconds.

## Why this works on Linux

The kernel has a driver for this hardware: `hid-wiimote`. Once the board is
paired it appears as an ordinary input device whose four absolute axes carry
the load cells, already run through the board's own calibration table. Nothing
has to speak raw Bluetooth HID or decode calibration registers.

BlueZ also ships a Wii-specific plugin that knows how these devices pair. Wii
peripherals don't do modern Secure Simple Pairing; they use a legacy PIN which
is the Bluetooth address of one side in reverse byte order. The BlueZ plugin
recognises the `Nintendo RVL-` name prefix and supplies that PIN for you, so
`bluetoothctl pair` works without any tricks.

Both of those are the reason this project targets a Pi rather than a Mac.
macOS has neither, so a Mac version would mean implementing the HID protocol
and the calibration maths by hand, against a Bluetooth stack that is famously
unhappy with Wii peripherals.

## Before you start

```bash
# Confirm the driver is present (it ships with any current kernel)
modinfo hid-wiimote | head -3

# Confirm Bluetooth is up
sudo systemctl status bluetooth
bluetoothctl show
```

Put four fresh AA batteries in the board. A board with weak batteries will
show up in a scan and then drop the connection seconds later, which looks
exactly like a pairing problem and is not one.

## Pairing

The **SYNC** button is the red one inside the battery compartment. Pressing it
makes the board discoverable for about 20 seconds, so have the scan running
first.

```bash
sudo bluetoothctl
```

```
[bluetooth]# power on
[bluetooth]# agent on
[bluetooth]# default-agent
[bluetooth]# scan on
```

Now press the red SYNC button. Within a few seconds:

```
[NEW] Device 00:1E:35:XX:XX:XX Nintendo RVL-WBC-01
```

`RVL-WBC-01` is the balance board (`RVL-CNT-01` would be a Wii remote). Note
the address and finish up — press SYNC again before each command if the board
has had time to give up:

```
[bluetooth]# scan off
[bluetooth]# pair 00:1E:35:XX:XX:XX
[bluetooth]# trust 00:1E:35:XX:XX:XX
[bluetooth]# connect 00:1E:35:XX:XX:XX
[bluetooth]# quit
```

`trust` is the part that matters for daily use: it tells BlueZ to accept the
board's incoming connection later without asking anybody.

## Check it worked

```bash
lsmod | grep wiimote          # hid_wiimote should be loaded
wiiscale devices              # should list a device with "Balance Board" in the name
```

Then watch the numbers, which is also how you sanity-check the board itself:

```bash
sudo wiiscale monitor
```

An empty board should hover around 0.00 kg. Step on and the total should match
what your bathroom scale says within a few hundred grams. Stand still and a
measurement should print after a couple of seconds.

## Daily use

Press the board's power button (front left, next to the LED). It reconnects to
the Pi on its own, `wiiscale` sees the input device appear, and you weigh
yourself. The board powers itself down after a few minutes of inactivity —
that is normal, and `wiiscale` just goes back to waiting.

## When it misbehaves

**The board never appears in a scan.** Batteries. Then try pressing SYNC
again while `scan on` is already running — the discoverable window is short.

**It pairs but never reconnects on the power button.** Check that it is
trusted:

```bash
bluetoothctl info 00:1E:35:XX:XX:XX   # look for "Trusted: yes"
```

If the pairing has gone stale, remove it and start over:

```bash
bluetoothctl remove 00:1E:35:XX:XX:XX
```

**You'd rather connect from the Pi than press the power button.** Use the
helper script, which retries until the board answers:

```bash
./deploy/connect-board.sh 00:1E:35:XX:XX:XX
```

**`wiiscale devices` finds nothing but `lsmod` shows the driver.** The service
user needs to be in the `input` group to read `/dev/input/event*`:

```bash
sudo usermod -aG input wiiscale
sudo systemctl restart wiiscale
```

**Weights read consistently high or low.** The board's own calibration is
usually good to a few hundred grams. If yours is off by a fixed percentage,
correct it with `board.unit_scale`: `new_scale = 0.01 * (true_kg / reported_kg)`.
A constant offset instead of a percentage is what `measurement.auto_tare`
already handles.
