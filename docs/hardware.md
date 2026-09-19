# Which box should run this?

Short answer: **the Raspberry Pi Zero WH**. It is the cheapest thing that runs
the code in this repository unchanged, and it is small enough to tuck behind
the scale with a phone charger.

The rest of this page is why, and what the alternatives actually cost you.

## Raspberry Pi Zero WH — recommended

- **Bluetooth:** BCM43438, Bluetooth 4.1 BR/EDR. The balance board is a
  Bluetooth *Classic* HID device, so BR/EDR is the part that matters; BLE is
  irrelevant here.
- **Works out of the box:** `hid-wiimote` gives calibrated load-cell values as
  an evdev device, and BlueZ's Wii plugin handles the legacy PIN pairing. That
  is the entire hard part of this project, already done, in the kernel.
- **Power:** roughly 0.7–1.2 W. Leave it on.
- **Caveat:** ARMv6 single core. Plenty for a 100 Hz sensor stream, but boots
  in ~35 seconds and compiles things slowly. Raspberry Pi OS points pip at
  piwheels, so `evdev` installs as a prebuilt wheel rather than compiling.

### Making it survive as an appliance

An always-on Pi writing logs to an SD card will eventually corrupt the card.
Two cheap mitigations, worth doing on day one:

```bash
# Keep systemd's journal in RAM instead of on the card
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=16M\n' \
    | sudo tee /etc/systemd/journald.conf.d/volatile.conf
sudo systemctl restart systemd-journald
```

Use a decent endurance-rated card, and take an image of it once everything
works so a dead card is a 10-minute fix rather than an evening.

If you enable the serial console, note that on the Zero W and the 3B+ the
Bluetooth chip hangs off the same UART. Raspberry Pi OS defaults handle this
(Bluetooth gets the mini-UART), but do not go reassigning `/dev/ttyAMA0`.

## Raspberry Pi 3B+ — works, but save it for something else

Same software story, slightly better radio (CYW43455, Bluetooth 4.2) and far
more CPU than this needs. It also draws several times the power, runs warmer,
and is physically awkward to hide next to a bathroom scale. If the Zero W ever
turns out to have marginal Bluetooth range to where the board lives, this is
the fallback — but try the Zero first.

## M5Stack Atom Lite / M5StickC Plus — possible, but you are writing firmware

Both are usable in principle, and the chips are right:

| Board | SiP | Bluetooth Classic? | Flash / PSRAM |
| --- | --- | --- | --- |
| Atom Lite | ESP32-PICO-D4 | yes (BR/EDR + BLE) | 4 MB / none |
| M5StickC Plus | ESP32-PICO-D4 | yes (BR/EDR + BLE) | 4 MB / none |
| M5StickC Plus2 | ESP32-PICO-V3-02 | yes (BR/EDR + BLE) | 8 MB / 2 MB |

**Check the chip before you buy.** Bluetooth Classic is the requirement, and
it is exactly what Espressif dropped in the newer parts: anything built on an
ESP32-S3 or ESP32-C3 is BLE-only and cannot talk to this board at all. The
AtomS3 Lite looks like an Atom Lite and is an ESP32-S3, so it will not work.
Product listings are unreliable here — several list the Plus2 as "Bluetooth
Low Energy 4.2" when the underlying SiP is BR/EDR *and* LE.

What you would have to write:

- The Bluetooth transport exists: [`takeru/Wiimote`](https://github.com/takeru/Wiimote)
  is an ESP32 Arduino library that connects to Wiimotes and balance boards and
  streams HID reports. It is a small, lightly maintained project — check its
  recent activity before committing to it.
- It deliberately stops at raw reports. You would still write the calibration
  path yourself: read the 24 calibration bytes from the extension registers,
  then interpolate each load cell against its 0 kg / 17 kg / 34 kg reference
  points. That is the work `hid-wiimote` does for free on Linux.
- Plus reconnect handling, the measurement logic in
  [`../wiiscale/measure.py`](../wiiscale/measure.py) ported to C++, an MQTT
  client, and the Home Assistant discovery payloads.

And two things to watch on the hardware itself:

- **One radio.** Wi-Fi and Bluetooth Classic share the ESP32's 2.4 GHz front
  end and are time-sliced. It works, but a saturated Wi-Fi link can cost you
  HID reports during a weighing.
- **RAM.** BlueDroid with Bluetooth Classic enabled, plus Wi-Fi, plus MQTT is
  a tight fit in the Atom Lite's 520 KB with no PSRAM. Doable without TLS;
  the Plus2's 2 MB PSRAM gives you room.

### When the ESP32 is actually the better answer

- No SD card to corrupt and no OS to keep patched — it is a genuine appliance.
- Lower idle power, though the gap against a Zero W is smaller than it looks.
- Boots instantly, which matters only if you intend to power-cycle it.

Those are real advantages. They are just not worth a firmware project when a
Pi Zero WH is already in your drawer and the software is written. If you want
the ESP32 version later, the measurement logic and the MQTT payload format in
this repository are the spec to port — the tests in `tests/` describe the
behaviour the firmware would need to reproduce.
