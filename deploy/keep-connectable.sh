#!/bin/sh
#
# Make the Bluetooth adapter page scan, and keep it that way long enough for
# bluetoothd to stop undoing it.
#
# The balance board reconnects by calling the Pi. The Pi only hears that call
# while the adapter is "connectable" - page scanning - and bluetoothd neither
# sets that itself nor restores it across a reboot.
#
# Setting it once at boot is not enough. bluetooth.service counts as started
# when bluetoothd takes its D-Bus name, but the adapter is configured some
# time after that, and whatever we set beforehand is overwritten. So: wait
# for the adapter to be powered, set it, and keep checking for a while in
# case bluetoothd settles late.
set -eu

DEADLINE=$(( $(date +%s) + ${WIISCALE_CONNECTABLE_TIMEOUT:-60} ))
SETTLED=${WIISCALE_CONNECTABLE_SETTLE:-5}
stable=0

for tool in btmgmt script; do
    command -v "$tool" >/dev/null || {
        echo "$tool is not installed (bluez and util-linux provide these)" >&2
        exit 1
    }
done

# btmgmt is built on bluez's shell framework and prints nothing at all when
# its standard input is not a terminal - which is exactly what systemd hands
# a service. It works by hand and is silent as a unit, with no error either
# way. So run it under a pty. hciconfig would also do the job, but it writes
# scan settings straight to HCI behind the management layer's back, and then
# bluetoothd can undo them without knowing it did.
run_btmgmt() {
    script -qec "btmgmt $*" /dev/null 2>/dev/null | tr -d '\r'
}

# btmgmt indents with tabs, so match any whitespace - matching only spaces
# made this read an empty string for a full minute and conclude there was no
# adapter at all.
settings() {
    run_btmgmt info |
        sed -n 's/^[[:space:]]*current settings:[[:space:]]*//p' |
        head -1
}

# The settings are a space-separated list, and "fast-connectable" contains
# "connectable" as a substring - so match whole words by padding both sides.
has() {
    case " $1 " in
        *" $2 "*) return 0 ;;
        *) return 1 ;;
    esac
}

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    current=$(settings)
    if has "$current" connectable; then
        stable=$(( stable + 1 ))
        # Seen set on several consecutive checks: bluetoothd has settled.
        [ "$stable" -ge "$SETTLED" ] && exit 0
    elif has "$current" powered; then
        stable=0
        run_btmgmt connectable on >/dev/null 2>&1 || true
    fi
    sleep 1
done

if ! has "${current:-}" connectable; then
    # Say what was actually seen. The first version of this failed silently
    # for a reason that one line of output would have given away.
    echo "Adapter never became connectable; the board's power button will not work" >&2
    echo "  last settings seen: ${current:-<none>}" >&2
    exit 1
fi
