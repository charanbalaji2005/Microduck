#!/bin/sh
# Stop Microduck X cleanly: zero the twist, then robotd, tofd, the control service (which stops
# the MuJoCo body it owns). Verifies the ports are closed rather than assuming it.
set -eu
. "$(dirname "$0")/x-common.sh"

if port_open "$MDX_PORT"; then
    curl -s -m 2 -X POST "http://127.0.0.1:$MDX_PORT/api/cancel" >/dev/null 2>&1 || true
fi
stop_pid x-robotd "$SOCK"
stop_pid x-tofd "$STATE/$DUCK-tof.sock"
stop_pid x-backend "microduck_x"
stop_pid x-body "xbody.py"
rm -f "$STATE/duck.sock"
left=""
for p in "$BODY_PORT" "$STATE_PORT" "$MDX_PORT"; do port_open "$p" && left="$left $p"; done
[ -z "$left" ] || { warn "still listening:$left (not started by x-start.sh?)"; exit 1; }
say "stopped"
