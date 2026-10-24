# Setting up Microduck X on Windows 11 (WSL2 + Ubuntu)

Everything runs locally. Nothing is deployed to a cloud. No Docker.

## What your laptop needs

| | Minimum | Comfortable | Measured (2-core cloud VM, no GPU) |
|---|---|---|---|
| CPU | 4 cores x86-64 | 6–8 cores | body 1.00× real time with 1 duck; robotd loop 50.0 Hz |
| RAM | 8 GB (WSL2 gets half by default) | 16 GB | body ~540 MB, service ~130 MB, robotd ~30 MB, browser tab ~400 MB |
| GPU | any WebGL2 browser GPU | integrated is fine | MuJoCo runs on the CPU; the dashboard draws ~1.6 M triangles |
| Disk | 6 GB | 10 GB | Rust `target/` 2.5 GB, service venv 0.5 GB, simulator venv 0.22 GB, node_modules 0.1 GB, whisper base.en 0.15 GB |

The real-time constraint is the one that matters: robotd's loop is wall-clock and its health gate
fails under 45 Hz. If the status bar shows `rtf` below ~0.95× or robotd under 48 Hz, close other
heavy programs, turn off soft shadows/SSAO in Settings, or disable the native MuJoCo window.

## 1. WSL2 and Ubuntu (once)

PowerShell as administrator:

```powershell
wsl --install -d Ubuntu-24.04
```

Reboot, open **Ubuntu**, create your user. Optional, in `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=8GB
processors=6
```

Then `wsl --shutdown` in PowerShell and reopen Ubuntu.

## 2. Packages (once)

In Ubuntu:

```sh
sudo apt update
sudo apt install -y build-essential pkg-config git curl python3 ffmpeg espeak-ng nodejs npm
node -v   # needs 18+. If older:  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
```

`x-setup.sh` installs Rust (rustup) and `uv` itself if they are missing, after asking.

## 3. Get the code and run setup

Keep the code **inside the Linux filesystem** (`~/…`), not under `/mnt/c` — builds there are
several times slower.

```sh
mkdir -p ~/Pollen && cd ~/Pollen
# put the microduck-main folder here (unzip it, or git clone pollen-robotics/microduck)
cd ~/Pollen/microduck-main
microduck-x/scripts/x-setup.sh                    # ~10 min the first time
microduck-x/scripts/x-setup.sh --whisper base.en  # optional: ~145 MB accurate speech model (asks first)
```

What `x-setup.sh` does, in order: checks packages → clones `microduck_rl` (develop) to
`~/Pollen/microduck_rl` → builds a light simulator venv with the versions `microduck_rl/uv.lock`
pins (mujoco, numpy, onnxruntime; `--full-rl` runs the official multi-GB `uv sync` instead) →
`cargo build -p robotd -p robotctl -p tof` → fetches the official policy set
(`scripts/seed-policies.sh`, ~6 MB from huggingface.co) → service venv → dashboard build.
It is idempotent; run it again after pulling.

## 4. Start, use, stop

```sh
microduck-x/scripts/x-start.sh                 # hangar; or MDX_ENV=lab|garden|arena|apartment|bare
microduck-x/scripts/x-status.sh
microduck-x/scripts/x-stop.sh
```

Open **http://localhost:8765** in Edge or Chrome on Windows (WSL2 forwards localhost). The
browser asks for the microphone the first time you press *hold to talk*; `localhost` counts as a
secure origin, so it works without HTTPS.

Start order (x-start does it): control service → it starts the MuJoCo body and waits for both
ports → `tofd --sim` → `robotd --sim` → wait for the socket → `robot.enable` (skip with
`MDX_AUTOSTAND=0`). Stop order is the reverse, and `x-stop.sh` checks the ports are closed.

The original tools keep working against the same duck: `scripts/duck-sim ctl health`,
`scripts/duck-sim monitor`. Do not run `scripts/duck-sim` (up/boot) at the same time — it owns the
same ports and sockets; `x-start.sh` refuses if it is running.

## Options

| Variable | Default | |
|---|---|---|
| `MDX_ENV` | `hangar` | starting scene |
| `MDX_AUTOSTAND` | `1` | enable the policy after start |
| `MDX_NATIVE_VIEWER` | `0` | also open MuJoCo's own window (needs WSLg; costs real time) |
| `MDX_STT` | `auto` | `faster-whisper`, `pocketsphinx`, `none` |
| `MDX_WHISPER_MODEL` | `base.en` | `tiny.en` for slow CPUs, `small.en` for accuracy |
| `MDX_WHISPER_DEVICE` | `auto` | `cpu` forces CPU; `auto` uses CUDA if ctranslate2 sees a GPU |
| `MDX_STT_MIN_CONFIDENCE` | `0.45` | below this, a voice command waits for confirmation |
| `MDX_CONFIRM_UNCALIBRATED` | `1` | pocketsphinx has no confidence score: always confirm |
| `MDX_REQUIRE_WAKE_WORD` | `0` | hands-free mode always requires "Microduck …" |
| `MDX_OLLAMA_MODEL` | unset | e.g. `llama3.2:3b` with Ollama running locally; output is validated |
| `GROQ_API_KEY`, `GROQ_MODEL` | unset, `openai/gpt-oss-120b` | turns on the chat's Groq brain, which sends open questions to Groq; other `GROQ_*` options in `.env.example` ([`HANGAR.md`](HANGAR.md#open-questions-the-groq-brain-backendmicroduck_xbrainpy)) |
| `MDX_HOST`, `MDX_TOKEN` | `127.0.0.1`, unset | binding elsewhere refuses to start without a token |
| `DUCK_SIM_RL`, `DUCK_SIM_STATE` | `~/Pollen/microduck_rl`, `~/.cache/duck-sim` | as in `scripts/duck-sim` |

## Developing the dashboard

```sh
cd microduck-x/frontend && npm run dev     # http://localhost:5173, proxies /api and /ws to :8765
```

Only for UI work with nothing else installed: `MDX_MOCK=1 … python -m microduck_x` serves a fake
robot made of three boxes. **That is not the Microduck** — the real robot only appears through
`microduck-x/scripts/x-start.sh`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "policy held", every movement refused | no policy set: `ls ~/.cache/duck-sim/policies/current`; rerun `x-setup.sh` (needs huggingface.co once) |
| robotd < 45 Hz, duck falls on its own | the machine cannot keep real time: fewer apps, no SSAO/shadows, no native viewer |
| dashboard says NOT LIVE | the body is down: `tail ~/.cache/duck-sim/x-body.log` |
| "port 7801 is in use" | `scripts/duck-sim down`, or `x-stop.sh` |
| voice: "no speech engine" | `ffmpeg` missing, or MDX_STT=faster-whisper without the model downloaded |
| voice keeps asking to confirm | pocketsphinx is in use; download whisper (Settings, or `x-setup.sh --whisper base.en`) |
