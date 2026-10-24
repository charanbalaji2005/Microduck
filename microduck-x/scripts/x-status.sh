#!/bin/sh
# What is running, what robotd says about itself, and whether the duck is standing.
set -eu
. "$(dirname "$0")/x-common.sh"

for name in x-backend x-body x-robotd x-tofd; do
    pid="$(cat "$STATE/$name.pid" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then printf '  %-10s pid %s\n' "$name" "$pid"; else printf '  %-10s -\n' "$name"; fi
done
if port_open "$MDX_PORT"; then
    curl -s "http://127.0.0.1:$MDX_PORT/api/status" | python3 -c "$(cat <<'PY'
import json, sys
s = json.load(sys.stdin)
r, m, f = s["robotd"], s["sim"], s["safety"]
print(f"  robotd   connected={r['connected']} loop={r['loop_hz']} policy={r['policy']} walk={r['walk']}")
if r["policy_unavailable"]:
    print(f"           ! {r['policy_unavailable']}")
print(f"  MuJoCo   connected={m['connected']} env={m['env']} rtf={m['rtf']} paused={m['paused']}")
print("  motion   allowed=%s %s" % (f["motion_allowed"], "; ".join(f["blockers"])))
print(f"  speech   {s['speech']['engine'] or s['speech']['reason']}")
PY
)"
else
    echo "  control service not running (microduck-x/scripts/x-start.sh)"
fi
