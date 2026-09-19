# Connecting the balance board to the Raspberry Pi

**Use Raspberry Pi OS bookworm and the board just pairs.** Everything else on
this page exists because trixie does not, and the difference is one version
number in BlueZ.

Verified end to end on a Pi Zero W with Raspberry Pi OS (Legacy, 32-bit) Lite,
BlueZ 5.66-1+deb12u2, and an RVL-WBC-01: pairing completes, the board binds to
`hid-wiimote`, and pressing its power button reconnects it on its own with no
SYNC button and no configuration beyond `backend: evdev`.

## The short version

Check first, because if this prints nothing the rest will not work:

```bash
sudo systemctl stop bluetooth; sudo /usr/libexec/bluetooth/bluetoothd -n -d 2>&1 | grep -i wiimote; sudo systemctl start bluetooth
```

You want `src/plugin.c:add_plugin() Loading wiimote plugin`.

Load the driver **before** connecting anything. If the board turns up first,
the kernel's built-in `hid_generic` claims it and you get a HID device without
the load-cell axes:

```bash
sudo modprobe hid-wiimote && echo hid-wiimote | sudo tee /etc/modules-load.d/wiiscale.conf
```

```bash
sudo bluetoothctl
```

```
power on
agent on
default-agent
scan on
```

Press the red SYNC button inside the battery compartment. When
`Nintendo RVL-WBC-01` appears, run all three **while the scan is still running
and the board is still in pairing mode**, each exactly once — the plugin
answers only the first PIN attempt:

```
trust 34:AF:2C:E4:EA:C5
pair 34:AF:2C:E4:EA:C5
connect 34:AF:2C:E4:EA:C5
quit
```

One thing does not survive a reboot on its own. The adapter has to be page
scanning for the board's call to be heard at all, and that is runtime state
that `bluetoothd` does not restore:

```bash
sudo btmgmt info | grep "current settings"
```

`connectable` must be in that list. It is not, after a fresh boot, and
without it the power button silently stops working - no error, no journal
line, nothing even in `btmon`, because the controller never sees the call.
`deploy/install.sh` installs `wiiscale-connectable.service` to set it on
every boot; if you did not use the installer:

```bash
sudo btmgmt connectable on
```

Confirm the board reached the driver:

```bash
cat /proc/bus/input/devices | grep -A5 -i nintendo
```

A `Name="Nintendo Wii Remote Balance Board"` block with an `event*` handler is
what you want. Then set `board.backend` to `evdev` and you are done; the power
button works from here on.

`ClassicBondedOnly` did not need touching. Leave it alone unless `connect`
fails with `Connection reset by peer`.

## If you are on trixie

Raspberry Pi OS trixie ships BlueZ 5.82, which cannot pair with this board at
all — [Why pairing is hard](#why-pairing-is-hard) explains exactly why, and it
is not something you can configure your way out of. The `l2cap` backend below
still reads the board there, but you press SYNC before every weighing.

Reinstalling on bookworm took fifteen minutes and a spare SD card, and is the
only thing in this session that actually worked.

## The l2cap backend

Find the board's address. Press the red **SYNC** button inside the battery
compartment, then:

```bash
bluetoothctl --timeout 10 scan on | grep RVL-WBC
```

Put that address in `/etc/wiiscale/config.yaml`:

```yaml
board:
  backend: l2cap
  address: 34:AF:2C:E4:EA:C5
```

Press SYNC and check the reading:

```bash
sudo wiiscale monitor
```

An empty board should hover around 0.00 kg, and stepping on should match your
bathroom scale within a few hundred grams. `deploy/l2cap-probe.py` does the
same thing without any configuration, printing the calibration table as well,
which is the quickest way to tell a board problem from a wiiscale problem.

**The catch: press SYNC before every weighing.** The board only accepts an
incoming connection while it is in pairing mode. Verified on a real board:
with the power button, every connection attempt over a 30-second window
returns `Host is down`. The board is not listening then — it is calling out,
to a host it has stored, and answering that call is what needs a real pairing.

SYNC sits inside the battery compartment, so this is fine for checking that
everything works and poor as a daily scale. That is the reason to move to
bookworm rather than to live with it.

### How it works

Two L2CAP channels, the same ones a Wii uses: `0x11` for commands, `0x13` for
reports. wiiscale asks the board for the 24 calibration bytes at register
`0xa40024` — three reference rows saying what each load cell reads at 0, 17 and
34 kg — and interpolates every reading against them. That is the work
`hid-wiimote` does for free, and the only real cost of this route.

## Why pairing is hard

Wii peripherals do not do modern Secure Simple Pairing. They use legacy
pairing with a PIN that is six raw bytes: the **host adapter's** Bluetooth
address, in the kernel's byte order. That is not a typo for the board's own
address — BlueZ's own plugin used `btd_adapter_get_address(adapter)`, the
adapter's.

Six arbitrary bytes are not printable characters, so nobody can type them,
and a D-Bus agent cannot return them either: a D-Bus string has to be valid
UTF-8 and an address usually is not. So the PIN has to be supplied below the
agent layer.

BlueZ used to do exactly that, in `plugins/wiimote.c`, which recognised the
device name `Nintendo RVL-WBC-01` among others and answered the first PIN
attempt with the adapter address. **Upstream deleted that plugin in BlueZ
5.80** (commit `7679c96`, 7 January 2025, whose entire message is "build:
Remove wiimote plugin"). It was never behind a configure flag — it was built
unconditionally until it was removed outright, so there is no
`--enable-wiimote` to turn it back on. Anything you read that tells you to
rebuild BlueZ with that flag predates the removal, or is repeating something
that was never true.

Which side of 5.80 a distribution sits on decides everything:

| Distribution | BlueZ | Plugin |
| --- | --- | --- |
| Debian bookworm / Raspberry Pi OS bookworm | 5.66 | yes |
| Ubuntu 22.04 LTS | 5.64 | yes |
| Ubuntu 24.04 LTS | 5.72 | yes |
| Ubuntu 25.04 | 5.79 | yes — the last one |
| Debian trixie / Raspberry Pi OS trixie | 5.82 | **no** |
| Ubuntu 25.10 and later | 5.83+ | **no** |

Check your own:

```bash
dpkg-query -W -f='${Version}\n' bluez
```

On a version that still has it, ordinary `bluetoothctl` pairing works and the
`evdev` backend is the nicer route. On 5.80 or later, read on.

## What does not work, so you do not spend an evening on it

The PIN can be supplied from outside bluetoothd. The kernel's management
socket takes `MGMT_OP_PIN_CODE_REPLY` as raw bytes, so a small program can
pair the board where D-Bus cannot, and that much does work: the bond
completes and a legacy combination link key lands in
`/var/lib/bluetooth/<adapter>/<board>/info`.

It does not help. The board still never calls the host on its power button,
which is the only thing the bond was wanted for. Tested on real hardware with
the adapter confirmed `connectable` and `btmon` watching: pressing power
produces no connection attempt at all.

Nor does the reverse. Binding the two HID PSMs and waiting for the board to
call in finds nothing to accept, for the same reason.

Both tools were written, both were run against a real board, and both are
deleted rather than left in `deploy/` to look like options. Their code is in
the history if the question ever comes up again.


## Other ways out

**Run wiiscale on a machine whose BlueZ still has the plugin.** Any host from
the table above with a Bluetooth adapter will pair the board with plain
`bluetoothctl` and give you the `evdev` backend and a working power button.
A USB dongle is enough; pick Broadcom, CSR or Realtek RTL8761B, and avoid the
cheap "BLE 5.0" sticks, several of which cannot do Bluetooth Classic at all.

**Reinstall the Pi with Raspberry Pi OS bookworm.** BlueZ 5.66, plugin
present. Fifteen minutes with an SD card writer, no building.

Both of these are the same thing: staying on the far side of 5.80. They work
today and get worse with time, because upstream has removed the plugin and
every distribution follows eventually.

**Patch the plugin back in and rebuild.** `plugins/wiimote.c` from the 5.79
tag is 130 lines and `Makefile.plugins` needs three lines adding. Expect
hours on a Pi Zero. Untried here.

## Why the evdev backend exists

The kernel has a driver for this hardware: `hid-wiimote`. Once the board is
connected it appears as an ordinary input device whose four absolute axes carry
the load cells, already run through the board's own calibration table. Nothing
has to speak raw Bluetooth HID or decode calibration registers.

That driver is also the reason this project targets a Pi rather than a Mac.
macOS has neither it nor BlueZ, so a Mac version would mean implementing the
transport again on IOBluetooth, against a Bluetooth stack that is famously
unhappy with Wii peripherals — and the Mac would have to be awake and nearby
every time you weigh yourself.

## Before you start

```bash
# Confirm the driver is present (it ships with any current kernel)
modinfo hid-wiimote | head -3

# Load it now, and on every boot. Do this BEFORE connecting the board: if the
# board turns up first, the kernel's built-in hid_generic claims it instead and
# you get a HID device without the load-cell axes.
sudo modprobe hid-wiimote
echo hid-wiimote | sudo tee /etc/modules-load.d/wiiscale.conf

# Confirm Bluetooth is up AND not blocked
sudo systemctl status bluetooth
rfkill list bluetooth
bluetoothctl show
```

`rfkill` must say `Soft blocked: no`. If `bluetoothctl show` reports
`PowerState: off-blocked`, the adapter is rfkill-blocked and every attempt to
use it fails with `Failed to set mode: Failed (0x03)` in the journal — which
reads like a firmware fault and is a one-line fix:

```bash
sudo rfkill unblock bluetooth
```

`systemd-rfkill` saves the block across reboots, so check it again after the
next restart. If it comes back, something sets it actively at boot.

Put four fresh AA batteries in the board. A board with weak batteries will
show up in a scan and then drop the connection seconds later, which looks
exactly like a pairing problem and is not one. Note that the blue LED lights
on batteries that are already too weak for the radio, so the LED alone does not
clear them — check the RSSI instead: at one metre you should see around -50,
and -78 means the board is barely transmitting.

## Pairing with bluetoothctl

This is the route on a BlueZ that still has the plugin. The **SYNC** button is
the red one inside the battery compartment; pressing it makes the board
discoverable for about 20 seconds, so have the scan running first.

```bash
sudo bluetoothctl
```

```
[bluetooth]# power on
[bluetooth]# agent NoInputNoOutput
[bluetooth]# default-agent
[bluetooth]# scan on
```

Now press the red SYNC button. Within a few seconds:

```
[NEW] Device 00:1E:35:XX:XX:XX Nintendo RVL-WBC-01
```

`RVL-WBC-01` is the balance board (`RVL-CNT-01` would be a Wii remote). Note
the address and, **while the scan is still running and the board is still in
pairing mode**, run all three:

```
[bluetooth]# trust 00:1E:35:XX:XX:XX
[bluetooth]# pair 00:1E:35:XX:XX:XX
[bluetooth]# connect 00:1E:35:XX:XX:XX
```

`trust` first is what the people who got this working report; it tells BlueZ
to accept the board's incoming connection later without asking anybody, and
doing it before the bond avoids a race with the reconnect.

Do not run `scan off` before `pair`. The board stops advertising when its
20-second window closes, and stopping the scan makes BlueZ drop an unpaired
device it can no longer see — the next command then fails with
`Device ... not available` and you have to press SYNC and start over.

Run each command **once**. The plugin answers only the first PIN attempt
(`if (attempt > 1) return 0;`), so retrying in a loop guarantees the later
attempts get no PIN at all. If a command fails, `remove` the device and start
from a clean state rather than trying again.

## The second obstacle: ClassicBondedOnly

The fix for CVE-2023-45866 made BlueZ require HID connections to come from a
bonded device, and a board that has lost its link key falls back to a PIN
request. Relaxing that is a documented workaround:

```bash
sudo sed -i 's/^#*ClassicBondedOnly *=.*/ClassicBondedOnly=false/' /etc/bluetooth/input.conf
sudo systemctl restart bluetooth
```

The setting must end up under `[General]`, and it lowers a security guarantee:
it lets unbonded devices open HID connections.

Background: [bluez#673](https://github.com/bluez/bluez/issues/673),
[bluez#765](https://github.com/bluez/bluez/issues/765),
[xwiimote(7)](https://manpages.debian.org/testing/xwiimote/xwiimote.7.en.html).

## Check it worked

```bash
cat /proc/bus/input/devices | grep -A5 -i nintendo
wiiscale devices              # should list a device with "Balance Board" in the name
```

What you want is a `Name="Nintendo Wii Remote Balance Board"` block whose
`Sysfs=` path runs through `0005:057E:0306`, and a `Handlers=` line naming an
`event*` device. That is the board bound to `hid-wiimote`.

Do not judge this by `lsmod`. The use count after `hid_wiimote` stays at `0`
even with the board attached and working, so a zero there means nothing.

Then watch the numbers, which is also how you sanity-check the board itself:

```bash
sudo wiiscale monitor
```

An empty board should hover around 0.00 kg. Step on and the total should match
what your bathroom scale says within a few hundred grams. Stand still and a
measurement should print after a couple of seconds.

## Daily use

With a working pairing: press the board's power button (front left, next to
the LED). It reconnects to the Pi on its own, `wiiscale` sees the input device
appear, and you weigh yourself. The board powers itself down after a few
minutes of inactivity — that is normal, and `wiiscale` just goes back to
waiting.

Without one: press SYNC instead, and `wiiscale` picks the board up on its next
poll.

## When it misbehaves

**The board never appears in a scan.** Batteries, then rfkill, then try
pressing SYNC again while `scan on` is already running — the discoverable
window is short.

**`Failed to set mode: Failed (0x03)` in the journal.** The adapter is
rfkill-blocked. See [Before you start](#before-you-start).

**`Device ... not available` right after it appeared in the scan.** You ran
`scan off`, or the board's 20-second window closed. Press SYNC and pair with
the scan still running.

**It asks for a PIN.** Your BlueZ has no wiimote plugin. See
[Why pairing is hard](#why-pairing-is-hard).

**`org.bluez.Error.Failed br-connection-create-socket`.** This one means three
different things depending on the journal line next to it: `Host is down (112)`
is a sleeping board, `Connection reset by peer (104)` is the board refusing an
unauthenticated HID connection, and `Invalid exchange (52)` is the PIN problem.
Always read `journalctl -u bluetooth` before acting on this error.

**It pairs but never reconnects on the power button.** Most likely the input
service was never opened while the bond was fresh, so the board never stored
this host — see
[Pairing over the management socket](#pairing-over-the-management-socket).
Otherwise check that it is trusted:

```bash
bluetoothctl info 00:1E:35:XX:XX:XX   # look for "Trusted: yes"
```

**`wiiscale devices` finds nothing but `lsmod` shows the driver.** The service
user needs to be in the `input` group to read `/dev/input/event*`:

```bash
sudo usermod -aG input wiiscale
sudo systemctl restart wiiscale
```

**Weights read consistently high or low.** The board's own calibration is
usually good to a few hundred grams. If yours is off by a fixed percentage,
correct it with `board.weight_scale`. A constant offset instead of a
percentage is what `measurement.auto_tare` already handles.
