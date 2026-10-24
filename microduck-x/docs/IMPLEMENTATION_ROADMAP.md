# Roadmap and verification status

"Verified" means run and checked, with the command below. "Not verified" means built and
unit-tested but not observed against the real thing, with the reason.

Where it was verified: a 2-core x86-64 Ubuntu 24.04 cloud VM, no GPU, MuJoCo 3.10.0, robotd
0.17.8 built from this tree, Chromium headless with software WebGL. That VM **could not reach
huggingface.co**, so the official policies and the whisper models were not available there.

## Definition of done — item by item

| # | Requirement | Status | Evidence / gap |
|---|---|---|---|
| 1 | Original Microduck model loads in MuJoCo | **Verified** | All six scenes compile and serve the original robot; `test_every_scene_serves_the_original_robot` |
| 2 | Scenery surrounds the robot and is physical | **Verified (geometry)** | Collision geoms by construction; viewer draws the compiled model. Robot–scenery contact is counted live; walking *into* scenery needs a gait (see 7) |
| 3 | Original control software still works | **Verified** | Original tree unmodified (`git status`); `cargo test -p duck-control -p duck-ipc-proto`: 151 passed; robotd runs 50.0 Hz against xbody |
| 4 | Viewer reflects real simulator state | **Verified** | Built from `op: model`, moved by `xpos/xquat`; LIVE/STALE/NOT LIVE badge from frame age; screenshots of arena, lab, garden |
| 5 | Offline speech recognition transcribes a spoken command | **Partly verified** | pocketsphinx (no download): synthetic espeak speech → 5/11 exact, e.g. "switch to the garden", "kick with the left foot", "follow the way points". faster-whisper: code path built and unit-tested, **not run** (model download blocked here) |
| 6 | Validated voice command reaches the real control interface | **Verified** | espeak audio → `/api/voice` → "go to arena" → confirmation → body restarted in the arena, robotd reconnected |
| 7 | Robot executes supported movements with its actual controller | **Not verified here** | Needs the official policy set. Verified instead: intents reach robotd and are reflected in `robot.state`; with robotd's untrained *contract fixture* in the walk slot, `robot.enable` switches torque on over the TCP seam and MuJoCo moves (`test_fixture_policy_actuates_mujoco`). Run `x-setup.sh` + `x-start.sh` on your machine for the gait |
| 8 | Emergency stop independent of AI | **Verified** | `POST /api/estop`, Esc key, red button; latch tested (`test_estop_latches_and_blocks`) |
| 9 | Telemetry and logs reflect actual events | **Verified** | Two labelled sources (MuJoCo ground truth, robotd `robot.state`); falls recorded from real tilt |
| 10 | Start/stop with documented commands | **Verified on Ubuntu 24.04** | `x-start.sh`, `x-status.sh`, `x-stop.sh` (port check after stop). **Not run on Windows/WSL2 itself** |
| 11 | Tests cover command validation and the adapter | **Verified** | 67 unit/mock tests (parser incl. short commands, schema, safety gate, executor races, planner, evaluator, API, persona/teaching) + 9 integration tests against real MuJoCo + robotd |
| 12 | Nothing mocked is presented as real | **By design** | Mock mode shows a red banner and `mock: true`; fixture policy shows a warning pill; "accepted" is never "done" |

## Phases

| Phase | State |
|---|---|
| 1. Audit, original simulator startup | Done — `ARCHITECTURE_AUDIT.md` (F1–F8) |
| 2. Control adapter, safe command execution | Done — allowlisted JSON-RPC client, schema, gate, single-activity executor, sequences, deadman |
| 3. Live telemetry | Done — xbody state port + `robot.subscribe`; staleness gating |
| 4. Model-accurate viewer, scenic environments | Done — 5 scenes, overlays (collision, contacts, joint axes, trail, heading, markers, plan, occupancy), 4 camera modes, SSAO, fullscreen, perf panel |
| 5. Offline speech + parser | Done — whisper/pocketsphinx, push-to-talk, VAD hands-free with wake word, confirmation, TTS (browser/espeak), optional validated LLM |
| 6. Dashboard integration | Done — Mission Control, Live, Voice, Environments, Telemetry, Navigation, Experiments, Policy, Logs, Settings |
| 7. Graphs, experiments, diagnostics | Done — 7 experiment definitions, JSON/CSV export, compare, fall explanation from recorded telemetry |
| 8. Windows 11 + WSL2 | Scripts and guide written; **needs a run on a real Windows laptop** |
| 9. Hangar page | Done — hangar scene (physical deck, pedestal, railing), studio rendering, thought bubble + chat with babble and moods, paint shop with share link, one/two-word commands, emote/dance sequences, taught aliases and memory. **Emote/dance motion quality not verified** (needs the official policies) |

## Measured here

| Quantity | Value |
|---|---|
| Body real-time factor (1 duck, headless) | 1.00× |
| robotd loop | 50.0 Hz, 0 missed |
| Ground-truth stream | 30 Hz, ~2.6 KB/frame |
| robotd state stream | ~19 Hz (25 Hz requested) |
| Dashboard WebSocket round trip | 1–2 ms (localhost) |
| Model download to the viewer | 7.8 MB meshes + 1.1 MB textures (lab), once per scene |
| Scene switch (body restart, model reload) | ~3 s; plus up to 8 s waiting for a policy to take the robot (11.8 s measured here, where none could) |
| pocketsphinx decode | 0.1–0.6 s per clip |
| Viewer frame rate | not meaningful here (software GL, ~1 fps); measure on your machine in the perf strip |

## Not done, or deliberately out of scope

* **Policy training / fine-tuning from the dashboard.** `microduck_rl` trains with mjlab + PPO
  on GPU; the dashboard shows what robotd loaded and evaluates it with experiments, but starts no
  training. Next step: a "Policy lab" page that runs `scripts/infer_policy.py` rehearsals and
  loads candidate ONNX files with `robot.loadPolicy` into a non-default slot.
* **Camera-based perception.** The body server can render the head camera (`--cameras a`) for
  `mediad`; no detector runs on it in Microduck X. The world model uses simulator ground truth and
  says so.
* **Recovery controller.** None is claimed. The velstand policy's own behaviour after a fall is
  whatever it was trained to do; the dashboard only reports it.
* **Several ducks.** xbody supports `--ducks N`; the dashboard controls duck-a only.
* **Deterministic replay.** Runs record full trajectories; exact replay is not possible because
  robotd is wall-clock (see `experiments.py`).
* **Learning in the ML sense.** The hangar duck remembers names, aliases and a speed factor in a
  JSON file; it does not learn from experience or train anything.
* **Wake-word model.** Hands-free mode uses energy VAD plus a wake *phrase* checked in the
  transcript, not a dedicated keyword-spotting network.

## How to verify on your machine

```sh
microduck-x/scripts/x-setup.sh --whisper base.en
microduck-x/scripts/x-start.sh
microduck-x/.venv/bin/python -m pytest -q microduck-x/tests                         # unit + mock
MDX_IT=1 DUCK_SIM_RL=~/Pollen/microduck_rl microduck-x/.venv/bin/python -m pytest -q -m integration microduck-x/tests
# then, in the dashboard: say "Microduck, start the walking test" and read the run's metrics
```

The walking test passes only if MuJoCo ground truth shows ≥ 0.6 m forward, ≤ 0.3 m drift and no
fall. If it fails, the run record says which criterion and by how much.

## Independent review

A separate review pass over `executor.py`, `safety.py`, `robotd.py` and the API handlers found
races in which the robot could keep receiving a non-zero twist after a stop: a supersede that was
not atomic (an orphaned activity), a stop landing mid-supersede, a cancelled sequence that kept
running because a cancel was swallowed, teleop not re-checking the gate after an await. It also
found completions without evidence (short walks without controller acknowledgement, skills
completing on acceptance alone), `enable` not gated on the e-stop latch, and NaN teleop
clamping to full speed. All were fixed; each race is now a regression test in
`tests/test_executor.py`.
