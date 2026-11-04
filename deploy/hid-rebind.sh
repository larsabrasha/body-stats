#!/bin/sh
#
# Make sure hid-wiimote ends up bound to a freshly connected balance board.
#
# The driver needs about 1 MB of physically contiguous memory per connection.
# Free memory is not the problem - fragmentation is: a machine running other
# services for weeks can have gigabytes available and no single free 1 MB
# block, and then the probe fails with error -12 and nothing tries again.
#
# So: give the kernel's own probe a moment; if it did not bind, compact memory
# and bind by hand; if that is still not enough, drop the page cache (which
# frees memory to compact into) and try once more. Dropping the cache is
# harmless but costs some disk reads afterwards, which is why it comes last.
set -u

dev="${1:?usage: $0 <hid device, e.g. 0005:057E:0306.0001>}"
path="/sys/bus/hid/devices/$dev"
bind=/sys/bus/hid/drivers/wiimote/bind

log() { logger -t wiiscale-hid-rebind "$dev: $*"; echo "$*"; }
bound() { [ -e "$path/driver" ]; }
gone() { [ ! -e "$path" ]; }

# udev reports the device as added before the kernel has finished probing it.
i=0
while [ "$i" -lt 6 ]; do
    bound && exit 0
    gone && exit 0
    sleep 0.5
    i=$((i + 1))
done

try_bind() {
    printf '%s' "$dev" > "$bind" 2>/dev/null || true
    bound
}

log "driver did not bind (probably out of contiguous memory); compacting"
echo 1 > /proc/sys/vm/compact_memory
if try_bind; then
    log "bound after compacting memory"
    exit 0
fi
gone && exit 0

log "still not bound; dropping page cache and compacting again"
sync
echo 3 > /proc/sys/vm/drop_caches
echo 1 > /proc/sys/vm/compact_memory
if try_bind; then
    log "bound after dropping page cache"
    exit 0
fi

log "could not bind hid-wiimote; the board is connected but cannot be read"
exit 1
