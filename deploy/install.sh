#!/usr/bin/env bash
#
# Install wiiscale as a systemd service on a Raspberry Pi (or any Debian-ish
# Linux). Run from a checkout of this repository:
#
#     sudo ./deploy/install.sh
#
set -euo pipefail

PREFIX=/opt/wiiscale
CONFIG_DIR=/etc/wiiscale
SERVICE_USER=wiiscale
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "This script needs root: sudo $0" >&2
    exit 1
fi

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y -qq python3-venv python3-dev bluez

echo "==> Preparing Bluetooth and the board driver"
# Load hid-wiimote now and on every boot. Order matters: if the board connects
# before this module is available, the kernel's built-in hid_generic claims it
# and you get a HID device without the load-cell axes.
modprobe hid-wiimote || echo "    WARNING: could not load hid-wiimote; see docs/pairing.md"
echo hid-wiimote > /etc/modules-load.d/wiiscale.conf

# An rfkill-blocked adapter reports 'Failed to set mode: Failed (0x03)', which
# reads like a firmware fault. Clear it here so it never gets diagnosed twice.
if command -v rfkill >/dev/null && rfkill list bluetooth | grep -q "Soft blocked: yes"; then
    echo "    Bluetooth was rfkill-blocked; unblocking"
    rfkill unblock bluetooth
fi

# BlueZ deleted its wiimote plugin in 5.80, and without it the board asks for a
# PIN nobody can type, so pairing cannot complete. Warn loudly rather than let
# it look like a hardware fault later.
# This starts a second bluetoothd alongside the running one; it gets far enough
# to log its plugin list before failing on the management socket. If it does not
# get that far we learn nothing, so only warn when the probe actually worked.
plugin_probe="$(timeout 5 /usr/libexec/bluetooth/bluetoothd -n -d 2>&1 || true)"
if grep -q "Loading .* plugin" <<<"$plugin_probe"; then
    if ! grep -qi "Loading wiimote plugin" <<<"$plugin_probe"; then
        echo "    NOTE: this bluetoothd has no wiimote plugin (removed upstream in"
        echo "          BlueZ 5.80). The board cannot be paired here at all."
        echo "          Weighing still works - set board.backend to l2cap, which"
        echo "          needs no pairing - but you must press SYNC each time."
        echo "          Raspberry Pi OS bookworm has the plugin. See docs/pairing.md."
    fi
else
    echo "    (could not determine whether bluetoothd has the wiimote plugin)"
fi

echo "==> Creating service user '$SERVICE_USER'"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi
# Reading the board's input device requires membership in the input group.
usermod -aG input "$SERVICE_USER"

echo "==> Installing into $PREFIX"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$PREFIX"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet "$REPO_DIR"

# So `sudo wiiscale monitor` works without spelling out the venv path.
ln -sf "$PREFIX/venv/bin/wiiscale" /usr/local/bin/wiiscale

echo "==> Installing configuration into $CONFIG_DIR"
install -d "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    install -m 0644 "$REPO_DIR/deploy/config.example.yaml" "$CONFIG_DIR/config.yaml"
    echo "    wrote $CONFIG_DIR/config.yaml - edit it before starting the service"
else
    echo "    $CONFIG_DIR/config.yaml already exists, left alone"
fi
if [[ ! -f "$CONFIG_DIR/wiiscale.env" ]]; then
    cat > "$CONFIG_DIR/wiiscale.env" <<'ENVEOF'
# Secrets for wiiscale, kept out of config.yaml.
# WIISCALE_MQTT_PASSWORD=
ENVEOF
    chmod 0640 "$CONFIG_DIR/wiiscale.env"
    chgrp "$SERVICE_USER" "$CONFIG_DIR/wiiscale.env"
fi

echo "==> Installing systemd units"
install -m 0644 "$REPO_DIR/deploy/wiiscale.service" /etc/systemd/system/wiiscale.service
# Without this the board's power button works until the first reboot and then
# stops, because "connectable" is runtime state that nothing restores.
install -d /usr/local/lib/wiiscale
install -m 0755 "$REPO_DIR/deploy/keep-connectable.sh" \
    /usr/local/lib/wiiscale/keep-connectable.sh
install -m 0644 "$REPO_DIR/deploy/wiiscale-connectable.service" \
    /etc/systemd/system/wiiscale-connectable.service
systemctl daemon-reload
systemctl enable wiiscale.service
if command -v btmgmt >/dev/null; then
    systemctl enable --now wiiscale-connectable.service
else
    echo "    WARNING: btmgmt not found; the adapter will not be kept connectable"
    echo "             and the board's power button will stop working on reboot."
fi

cat <<'DONE'

Installed.

Next:
  1. Pair the balance board:   see docs/pairing.md - on bookworm it is
                               bluetoothctl, the SYNC button and three
                               commands; on trixie it cannot be done at all
                               and you use the l2cap backend instead
  2. Edit /etc/wiiscale/config.yaml (broker host, username)
  3. Put the broker password in /etc/wiiscale/wiiscale.env
  4. Check the board is readable: sudo -u wiiscale wiiscale devices
  5. Start it:                  sudo systemctl start wiiscale
  6. Watch it:                  journalctl -u wiiscale -f

DONE
