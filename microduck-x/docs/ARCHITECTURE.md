# Microduck X architecture

Four processes on one machine (inside WSL2 on Windows), plus the browser.

```
                    ┌──────────────────────── Windows ─────────────────────────┐
                    │  Browser: React + Three.js dashboard  (microphone here)   │
                    └───────────────▲───────────────────────┬──────────────────┘
                      HTTP + WS     │ localhost:8765         │ WAV clips, commands, teleop
┌────────────────────────────────── WSL2 / Ubuntu ──────────┼───────────────────────────────────┐
│                                   │                       ▼                                   │
│  control service  (microduck-x/backend, FastAPI, Python 3.12)                                 │
│   parser ─► command schema (allowlist) ─► safety gate ─► executor ─► robotd client            │
│   speech (faster-whisper | pocketsphinx)   telemetry hub   experiments   navigation           │
│   supervisor (owns the body process)                                                         │
│        │ JSON-RPC, ~/.cache/duck-sim/duck-a.sock                │ TCP 127.0.0.1:7811 (state)  │
│        ▼                                                        ▼                             │
│  robotd --sim   ◄──── TCP 127.0.0.1:7801, original protocol ──► xbody.py                      │
│  (unmodified: 50 Hz loop, ONNX policies,                         = microduck_rl body_server   │
│   safety, fall reflex, odometry)                                   World/Body/Handler (reused) │
│  tofd --sim   ◄────────────────────────────── 7801 ─────────────   + scenes + state port      │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Authority

* **MuJoCo is authoritative for physics.** The viewer is built from the compiled model and
  driven by MuJoCo's body poses. Interpolation between frames is display-only and never written
  back.
* **robotd is authoritative for control.** The control service sends intents (`robot.move`,
  `robot.enable`, `robot.do`), never joint targets; robotd's policies, clamps, deadman and fall
  reflex stay in charge.
* **The control service is authoritative for what may be asked.** Every route — voice, LLM, UI,
  keyboard, experiments, HTTP — becomes a `commands.Envelope`, validated, gated, and logged.

## Layers (as the brief asked them to be separated)

| Layer | Module | Notes |
|---|---|---|
| Language interpretation | `parser.py` (deterministic, incl. one/two-word `SHORT` table), `llm.py` (optional Ollama, validated), `persona.py` (chat replies, taught aliases), `brain.py` (optional Groq conversation for open questions; allowlisted actions only) | aliases expand to commands that are re-validated; see [`HANGAR.md`](HANGAR.md) |
| Task planning | `executor.py` (sequences, experiments), `navigation.py` (A* on scene geometry) | |
| Motion command generation | `executor.py` `_drive_tick`, `heading_command` | 20 Hz `robot.move` |
| Low-level control | robotd (original) | policies at 50 Hz |
| Safety monitoring | `safety.py` + robotd's own safety | two independent layers |
| Perception / world model | `telemetry.py`, xbody frames, `navigation.obstacles_from_model` | ground truth, labelled as such; no camera perception is implemented |

## Ports and files

| What | Where |
|---|---|
| Dashboard + API | `127.0.0.1:8765` |
| Body ↔ robotd (original) | `127.0.0.1:7801` |
| Body state port (xbody) | `127.0.0.1:7811` |
| robotd socket | `~/.cache/duck-sim/duck-a.sock` (`duck.sock` links to it) |
| Logs | `~/.cache/duck-sim/x-backend.log`, `x-body.log`, `duck-a.log`, `duck-a-tof.log` |
| Experiment runs | `~/.cache/duck-sim/x-runs/*.json` |
| Duck memory (names, aliases, speed) | `~/.cache/duck-sim/x-duck-memory.json` |
| Speech models | `~/.cache/duck-sim/models/whisper/` |

## Failure behaviour

| Event | What happens |
|---|---|
| Browser tab closes mid-drive | teleop deadman (350 ms) in the service, then robotd's own (500 ms) |
| Control service dies | intents stop; robotd's deadman zeroes the twist |
| robotd dies or stalls | state goes stale (> 0.6 s) → movement refused; client reconnects with backoff |
| Body dies or restarts | frames stale (> 1 s) → movement refused; robotd retries every tick; scene switch re-arms torque (relax → enable) |
| Robot falls | robotd's fall reflex; the service sees `safety.fallen` or tilt > 60°, fails the running command, records the fall window for "why did it fall" |
| E-stop | latched in the service, independent of speech and LLM; zero twist + `robot.stop`; all tasks cancelled; movement refused until cleared |
