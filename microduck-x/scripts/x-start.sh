#!/bin/sh
# Start Microduck X: the MuJoCo body (original duck-body + state port), tofd, robotd and the
# control service, in that order, then stand the duck up.
#
#   microduck-x/scripts/x-start.sh                 # the scenery stage (photographed worlds)
#   MDX_ENV=lab microduck-x/scripts/x-start.sh     # stage | hangar | bare | apartment | lab | garden | arena
#   MDX_AUTOSTAND=0 …                              # leave the duck seated
#   MDX_NATIVE_VIEWER=1 …                          # also open MuJoCo's own window (WSLg)
#
# Then open http://localhost:8765 (from Windows too, under WSL2). Stop with x-stop.sh.
set -eu
. "$(dirname "$0")/x-common.sh"

ENV_NAME="${MDX_ENV:-stage}"

# ── preflight ────────────────────────────────────────────────────────────────
[ -x "$RL/.venv/bin/python" ] || die "no simulator venv in $RL — run microduck-x/scripts/x-setup.sh"
[ -x "$XVENV/bin/python" ] || die "no control-service venv — run microduck-x/scripts/x-setup.sh"
[ -x "$REPO/target/debug/robotd" ] || die "robotd is not built — run microduck-x/scripts/x-setup.sh"
[ -f "$X_ROOT/frontend/dist/index.html" ] || warn "dashboard not built (x-setup.sh builds it); the API still starts"
ORT="$(ort_lib)"; [ -n "$ORT" ] || die "no libonnxruntime in $RL/.venv"
if [ -f "$STATE/body.pid" ] && kill -0 "$(cat "$STATE/body.pid")" 2>/dev/null; then
    die "scripts/duck-sim is running (it owns the same sockets and ports). Run: scripts/duck-sim down"
fi
for p in "$BODY_PORT" "$STATE_PORT" "$MDX_PORT"; do
    port_open "$p" && die "port $p is in use. microduck-x/scripts/x-stop.sh, or find it with: ss -ltnp | grep $p"
done
mkdir -p "$STATE"

# ── a params file naming the policies that exist ─────────────────────────────
# robotd's defaults point under /opt/robot (a board's layout); a laptop has to name every file,
# exactly as scripts/duck-sim does. A slot whose file is absent is set to "none" so the duck reports
# what it really has, instead of failing to load a skill nobody installed.
POL="$REPO/policies"
[ -f "$POL/velstand.onnx" ] || [ -f "$POL/alpha_walking.onnx" ] || POL="$STATE/policies/current"
slot() { if [ -f "$POL/$2" ]; then echo "$1 = \"$POL/$2\""; else echo "$1 = \"none\""; fi; }
if [ -f "$POL/velstand.onnx" ]; then
    WALK="walk = \"$POL/velstand.onnx\""; STAND=""
elif [ -f "$POL/alpha_walking.onnx" ]; then
    WALK="walk = \"$POL/alpha_walking.onnx\""; STAND="$(slot stand alpha_stand.onnx)"
else
    warn "no policies in $POL: the duck will hold its pose; movement will be refused (x-setup.sh fetches them)"
    WALK="walk = \"$POL/velstand.onnx\""; STAND=""
fi
PARAMS="$STATE/robotd-x-$DUCK.toml"
cat > "$PARAMS" <<TOML
# Written by microduck-x/scripts/x-start.sh — regenerated on every start.
[policy]
enabled = true
$WALK
$STAND
$(slot sitstand alpha_sitstand.onnx)
$(slot ground_pick alpha_ground_pick.onnx)
$(slot kick_left ball_kick_left.onnx)
$(slot kick_right ball_kick_right.onnx)
$(slot roulade roulade.onnx)

[audio]
device = "default"

[theremin]
socket = "$STATE/$DUCK-tof.sock"
TOML

# ── 1. control service, which starts and owns the MuJoCo body ────────────────
say "starting the control service and the MuJoCo body ($ENV_NAME)"
(cd "$X_ROOT/backend" && MDX_STATE="$STATE" MDX_MANAGE_SIM=1 MDX_ENV="$ENV_NAME" DUCK_SIM_RL="$RL" \
    MDX_PORT="$MDX_PORT" MDX_STATE_PORT="$STATE_PORT" DUCK_SIM_PORT="$BODY_PORT" \
    setsid nohup "$XVENV/bin/python" -m microduck_x > "$STATE/x-backend.log" 2>&1 < /dev/null &
    echo $! > "$STATE/x-backend.pid")
n=0
until port_open "$STATE_PORT" && port_open "$MDX_PORT"; do
    n=$((n + 1)); [ $n -gt 150 ] && die "the simulator or control service did not come up — see $STATE/x-backend.log and $STATE/x-body.log"
    sleep 0.2
done

# ── 2. tofd and robotd, against the body ──────────────────────────────────────
say "starting tofd and robotd (--sim 127.0.0.1:$BODY_PORT)"
DUCK_RUNTIME_DIR="$STATE" setsid nohup "$REPO/target/debug/tofd" --sim "127.0.0.1:$BODY_PORT" \
    --socket "$STATE/$DUCK-tof.sock" > "$STATE/$DUCK-tof.log" 2>&1 < /dev/null &
echo $! > "$STATE/x-tofd.pid"
DUCK_IDENTITY="$DUCK" DUCK_RUNTIME_DIR="$STATE" ORT_DYLIB_PATH="$ORT" RUST_LOG="${RUST_LOG:-info}" \
    setsid nohup "$REPO/target/debug/robotd" --sim "127.0.0.1:$BODY_PORT" --params "$PARAMS" --socket "$SOCK" \
    > "$STATE/$DUCK.log" 2>&1 < /dev/null &
echo $! > "$STATE/x-robotd.pid"
ln -sf "$STATE/$DUCK.sock" "$STATE/duck.sock"
n=0
until [ -S "$SOCK" ]; do n=$((n + 1)); [ $n -gt 100 ] && die "robotd did not come up — see $STATE/$DUCK.log"; sleep 0.2; done

# ── 3. stand up ───────────────────────────────────────────────────────────────
n=0
until curl -sf "http://127.0.0.1:$MDX_PORT/api/health" | grep -q '"robotd":true'; do
    n=$((n + 1)); [ $n -gt 50 ] && break; sleep 0.2
done
if [ "${MDX_AUTOSTAND:-1}" = 1 ]; then
    say "standing up (robot.enable through the control service)"
    curl -s -H 'content-type: application/json' -d '{"command":{"action":"enable"},"source":"api","wait":true,"timeout":15}' \
        "http://127.0.0.1:$MDX_PORT/api/command" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   ", d.get("status"), "—", d.get("message"))' || true
fi

cat <<EOF

  Microduck X is up.  Dashboard: http://localhost:$MDX_PORT
     robotd socket  $SOCK   (scripts/duck-sim ctl health  works too)
     logs           $STATE/x-backend.log  x-body.log  $DUCK.log
     stop           microduck-x/scripts/x-stop.sh

EOF
