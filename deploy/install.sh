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

echo "==> Installing systemd unit"
install -m 0644 "$REPO_DIR/deploy/wiiscale.service" /etc/systemd/system/wiiscale.service
systemctl daemon-reload
systemctl enable wiiscale.service

cat <<'DONE'

Installed.

Next:
  1. Pair the balance board:   see docs/pairing.md
  2. Edit /etc/wiiscale/config.yaml (broker host, username)
  3. Put the broker password in /etc/wiiscale/wiiscale.env
  4. Check the board is readable: sudo -u wiiscale wiiscale devices
  5. Start it:                  sudo systemctl start wiiscale
  6. Watch it:                  journalctl -u wiiscale -f

DONE
