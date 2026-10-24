#!/bin/sh
# One-time setup for Microduck X on Ubuntu (native or WSL2 on Windows 11).
#
#   microduck-x/scripts/x-setup.sh            # everything, light simulator venv
#   microduck-x/scripts/x-setup.sh --full-rl  # microduck_rl's full `uv sync` (training stack, several GB)
#   microduck-x/scripts/x-setup.sh --whisper base.en   # also download a whisper model (asks first)
#
# Idempotent: run it again after a pull. It says what it is about to download and roughly how big.
set -eu
. "$(dirname "$0")/x-common.sh"

FULL_RL=0
WHISPER=""
YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --full-rl) FULL_RL=1 ;;
        --whisper) shift; WHISPER="${1:-base.en}" ;;
        -y|--yes) YES=1 ;;
        -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
        *) die "unknown option $1" ;;
    esac
    shift
done

confirm() {
    [ "$YES" = 1 ] && return 0
    printf '%s [y/N] ' "$1"; read -r answer || answer=n
    case "$answer" in y|Y|yes) return 0 ;; *) return 1 ;; esac
}

[ "$(uname -s)" = Linux ] || die "run this inside Ubuntu (WSL2 on Windows). See microduck-x/docs/SIMULATION_SETUP.md"
is_wsl && say "WSL detected — the dashboard will be at http://localhost:$MDX_PORT from Windows"

# ── system packages ───────────────────────────────────────────────────────────
missing=""
for cmd in git curl python3 cc pkg-config ffmpeg; do
    command -v "$cmd" >/dev/null 2>&1 || missing="$missing $cmd"
done
if [ -n "$missing" ]; then
    warn "missing:$missing"
    echo "    sudo apt update && sudo apt install -y build-essential pkg-config git curl python3 ffmpeg espeak-ng"
    die "install the packages above, then run this again"
fi
command -v espeak-ng >/dev/null 2>&1 || warn "espeak-ng not installed: spoken replies will use the browser's voices (optional: sudo apt install espeak-ng)"

if ! command -v cargo >/dev/null 2>&1; then
    confirm "Rust is not installed. Install it with rustup (~1 GB under ~/.rustup)?" || die "Rust is required to build robotd"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
    . "$HOME/.cargo/env"
fi
if ! command -v uv >/dev/null 2>&1; then
    confirm "uv (Python package manager) is not installed. Install it (~30 MB)?" || die "uv is required"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    PATH="$HOME/.local/bin:$PATH"
fi
command -v node >/dev/null 2>&1 || die "Node.js 18+ is required for the dashboard: https://nodejs.org (or: sudo apt install nodejs npm)"
node -e 'process.exit(parseInt(process.versions.node) >= 18 ? 0 : 1)' || die "Node.js $(node -v) is too old; 18 or newer"

# ── microduck_rl: the MuJoCo body, the model and its meshes ──────────────────
if [ ! -d "$RL/.git" ]; then
    say "cloning microduck_rl (develop) into $RL (~40 MB)"
    mkdir -p "$(dirname "$RL")"
    git clone --branch develop https://github.com/pollen-robotics/microduck_rl "$RL"
fi
if [ "$FULL_RL" = 1 ]; then
    say "microduck_rl: full uv sync (torch, mjlab, warp — several GB)"
    (cd "$RL" && uv sync)
elif [ ! -x "$RL/.venv/bin/python" ] || ! "$RL/.venv/bin/python" -c "import mujoco, onnxruntime" 2>/dev/null; then
    MJ="$(locked mujoco)"; NP="$(locked numpy)"; ORTV="$(locked onnxruntime)"
    say "simulator venv (light): mujoco ${MJ:-?}, numpy ${NP:-?}, onnxruntime ${ORTV:-?} — about 220 MB"
    say "  (the body server needs only these; --full-rl installs the training stack instead)"
    (cd "$RL" && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python \
        "mujoco${MJ:+==$MJ}" "numpy${NP:+==$NP}" "onnxruntime${ORTV:+==$ORTV}")
fi
[ -n "$(ort_lib)" ] || die "no libonnxruntime in $RL/.venv — robotd cannot load policies without it"

# ── the robot's daemons, built from this checkout ────────────────────────────
say "building robotd, robotctl and tofd (first build: a few minutes)"
(cd "$REPO" && cargo build --quiet -p robotd -p robotctl -p tof)

# ── the official policy set ───────────────────────────────────────────────────
mkdir -p "$STATE"
if [ ! -f "$REPO/policies/velstand.onnx" ] && [ ! -f "$STATE/policies/current/velstand.onnx" ] \
        && [ ! -f "$STATE/policies/current/alpha_walking.onnx" ]; then
    say "fetching the official policy set (pollen-robotics/microduck-policies, ~6 MB) — scripts/seed-policies.sh"
    sh "$REPO/scripts/seed-policies.sh" "$STATE/policies" || true
fi
if [ -d "$STATE/policies/current" ]; then
    say "policies: $(ls "$STATE/policies/current" | grep -c onnx) files in $STATE/policies/current"
else
    warn "no policy set installed: the duck will hold its pose and every movement will be refused."
    warn "it needs huggingface.co once; or put your own .onnx files in $REPO/policies"
fi

# ── the control service ───────────────────────────────────────────────────────
say "control service venv ($XVENV): fastapi, uvicorn, faster-whisper, pocketsphinx (~350 MB)"
[ -x "$XVENV/bin/python" ] || uv venv --python 3.12 "$XVENV"
uv pip install --python "$XVENV/bin/python" -q -r "$X_ROOT/backend/requirements.txt"

# ── the dashboard ─────────────────────────────────────────────────────────────
say "building the dashboard"
(cd "$X_ROOT/frontend" && npm ci --no-audit --no-fund --loglevel=error && npm run build --silent)

# ── speech model (explicit only) ──────────────────────────────────────────────
if [ -n "$WHISPER" ]; then
    case "$WHISPER" in tiny.en) size=75 ;; base.en) size=145 ;; small.en) size=485 ;; *) size="?" ;; esac
    if confirm "Download whisper '$WHISPER' (~$size MB) into $STATE/models/whisper?"; then
        "$XVENV/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('$WHISPER', device='cpu', compute_type='int8', download_root='$STATE/models/whisper')"
        say "whisper '$WHISPER' cached; set MDX_WHISPER_MODEL=$WHISPER if it is not base.en"
    fi
else
    say "speech: pocketsphinx works now (no download). For whisper accuracy: x-setup.sh --whisper base.en (~145 MB)"
fi

say "done. Start everything with: microduck-x/scripts/x-start.sh"
