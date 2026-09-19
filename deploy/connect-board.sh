#!/usr/bin/env bash
#
# Connect a paired balance board from the Pi side, retrying until it answers.
#
#     ./deploy/connect-board.sh 00:1E:35:XX:XX:XX [attempts]
#
# Normally you don't need this: a trusted board reconnects by itself when you
# press its power button. It's here for when you'd rather trigger the
# connection from the Pi, or as a one-liner to drop into a systemd timer.
#
set -euo pipefail

MAC="${1:-}"
ATTEMPTS="${2:-10}"

if [[ -z "$MAC" ]]; then
    echo "usage: $0 <board-mac-address> [attempts]" >&2
    echo "Find the address with: bluetoothctl devices | grep RVL-WBC" >&2
    exit 1
fi

for ((i = 1; i <= ATTEMPTS; i++)); do
    if bluetoothctl connect "$MAC" | grep -q "Connection successful"; then
        echo "Connected to $MAC"
        exit 0
    fi
    echo "Attempt $i/$ATTEMPTS failed; press the board's power button if it is off"
    sleep 3
done

echo "Gave up after $ATTEMPTS attempts" >&2
exit 1
