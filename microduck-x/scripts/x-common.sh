# Shared by x-setup.sh, x-start.sh, x-stop.sh, x-status.sh. POSIX sh, sourced.
#
# The state directory and socket names are scripts/duck-sim's on purpose (~/.cache/duck-sim,
# duck-a.sock), so `scripts/duck-sim ctl …` and `robotctl` work against a Microduck X duck too.
# The two launchers must not run at the same time; x-start refuses if duck-sim's body is up.

X_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$X_ROOT/.." && pwd)"
STATE="${DUCK_SIM_STATE:-$HOME/.cache/duck-sim}"
RL="${DUCK_SIM_RL:-$HOME/Pollen/microduck_rl}"
BODY_PORT="${DUCK_SIM_PORT:-7801}"
STATE_PORT="${MDX_STATE_PORT:-7811}"
MDX_PORT="${MDX_PORT:-8765}"
DUCK=duck-a
SOCK="$STATE/$DUCK.sock"
XVENV="$X_ROOT/.venv"

say()  { printf '\033[32m==\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

is_wsl() { grep -qi microsoft /proc/version 2>/dev/null; }

port_open() {
    python3 -c "import socket,sys;s=socket.socket();s.settimeout(.3);sys.exit(0 if s.connect_ex(('127.0.0.1',$1))==0 else 1)" 2>/dev/null
}

cmdline_of() { tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null; }

# Stop one process this toolkit started: by pidfile, and only if /proc still says it is ours.
stop_pid() {
    file="$STATE/$1.pid"; expect="$2"
    [ -f "$file" ] || return 0
    pid="$(tr -dc '0-9' < "$file")"; rm -f "$file"
    [ -n "$pid" ] && [ "$pid" -gt 1 ] 2>/dev/null || return 0
    cmdline_of "$pid" | grep -q -- "$expect" || return 0
    kill -TERM "$pid" 2>/dev/null || true
    n=0; while [ $n -lt 25 ] && kill -0 "$pid" 2>/dev/null; do n=$((n + 1)); sleep 0.2; done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
}

ort_lib() {
    find "$RL"/.venv/lib/python*/site-packages/onnxruntime/capi -name 'libonnxruntime.so*' -type f 2>/dev/null | head -1
}

# The version microduck_rl's own lockfile pins, so the light simulator venv matches it.
locked() {
    sed -n "/^name = \"$1\"\$/{n;s/^version = \"\(.*\)\"/\1/p;}" "$RL/uv.lock" 2>/dev/null | head -1
}
