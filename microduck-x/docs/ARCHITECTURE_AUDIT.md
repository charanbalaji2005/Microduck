# Architecture audit — what the uploaded Microduck project actually is

Audited: the uploaded `microduck-main` tree (465 files, Rust workspace, `API_VERSION` 44) and
`pollen-robotics/microduck_rl` at `develop` (`b02fdea`, 2026-10-12). Every claim below names the
file it was read from, or the command it was observed with. Nothing here is inferred from the
brief.

## 1. The two halves of the simulated duck

| Half | Lives in | Entry point | Evidence |
|---|---|---|---|
| **The body** — MuJoCo physics, the robot model, BAM-fitted actuators | `microduck_rl` | `mjlab_microduck.sim.body_server` (`duck-body`) | `microduck_rl/src/mjlab_microduck/sim/body_server.py`, `pyproject.toml [project.scripts]` |
| **The brain** — the robot's real daemons | `microduck-main` | `robotd --sim host:port`, `tofd --sim`, `mediad --sim-camera` | `duck-control/src/sim.rs` (`RemoteIo`), `scripts/duck-sim`, `docs/robot/simulation.md` |

The upload contains **no MuJoCo code and no robot model of its own**: the only MJCF in it is
`kinematics/assets/alpha/robot_walk.xml`, used for forward kinematics, not physics. The physical
model, meshes, scenes and keyframes are in `microduck_rl/src/mjlab_microduck/robot/microduck/`.

### The seam between them (verified by running both)

`robotd --sim 127.0.0.1:7801` opens a TCP connection to the body and, every 20 ms tick, sends
newline-delimited JSON: `hello {protocol: 1, joints: 15}`, `read`, `write {targets[15]}`,
`gain {kp}`, `torque {on}`, `slow`; `tofd --sim` sends `tof`. The body answers joint positions,
velocities, a stand-in current, projected gravity, gyro and quaternion — plus `trunk_z`, `trunk`
and `sim_time`, which no robot can measure and robotd ignores (`body_server.py`, `Body.sensors`).
Fifteen joints on the wire, fourteen in the model: `mouth` (index 9) is inserted and dropped by
the body server.

Observed with the cloud build: `robotd` connected, ran its loop at 50.0 Hz with 0 missed ticks,
reported `achieved_hz 49.996` in `robot.health`.

## 2. How `scripts/duck-sim` works

`scripts/duck-sim` (POSIX sh, 1120 lines) with no argument:

1. builds `robotd robotctl tof sounds configd updater` with cargo;
2. finds policies in `./policies/` or fetches the official set with `scripts/seed-policies.sh`
   into `~/.cache/duck-sim/policies/` (Hugging Face `pollen-robotics/microduck-policies`, tag `v8`);
3. writes `robotd-duck-a.toml` naming every policy file explicitly (a laptop has no `/opt/robot`);
4. starts the body (`$DUCK_SIM_RL/.venv/bin/python -m mjlab_microduck.sim.body_server`), then
   `tofd --sim`, `configd --simulated`, `updaterd`, `robotd --sim`, optional `mediad`;
5. sends `robot.enable {on: true}` over the socket (there is no `robotctl` subcommand for it) and
   checks `trunk_z > 0.095` to decide whether the duck stood.

Walking is `scripts/duck-sim drive`: `robot.move {vx, vy, vyaw}` notifications at 10 Hz, because
robotd expires an intent after 500 ms (`[safety] deadman_ms`).

## 3. The control interface (robotd JSON-RPC, Unix socket)

Method names from `duck-ipc-proto/src/lib.rs` (`pub mod method`), types from the same file,
behaviour observed against a running `robotd --sim`:

| Method | Kind | Verified behaviour |
|---|---|---|
| `robot.subscribe {hz}` | request → stream | Ack names `walk`, `stand`, `sitstand`, `ground_pick`, `skills[]`, `unavailable`; then `robot.state` notifications |
| `robot.state` | notification | `t, move{requested,applied,limited_by}, policy, safety{fallen,limp,gravity,gain}, loop{hz,missed}, joints[15], targets[15], velocities[15], currents_ma, odom{position,yaw}, imu{gyro,quat}, frames, skeleton[15]` |
| `robot.move {vx, vy, vyaw}` | notification, resend ≥ 2 Hz | Reflected in `move.requested` within one tick. Zeroed by the deadman after 500 ms. |
| `robot.stop` | request | `{accepted: true}` |
| `robot.enable {on}` | request | Policy on: torque on, 2 s ramp to home, "the policy has the robot" |
| `robot.relax` | request | Torque off ("the robot will collapse") |
| `robot.do {skill}` | request | Skills from the ack (`roulade, kick_left, kick_right`) plus `ground_pick`, `sit_toggle`; refusals carry a reason |
| `robot.policies`, `robot.health`, `robot.model` | request | Slot table with per-slot load errors; health gate; static geometry |

Velocity envelope: robotd does not clamp `robot.move`; the pad defaults for the v8 velstand gait
are vx ±0.4 m/s, vy ±0.3 m/s, vyaw ±1.0 rad/s (`robotd-params/src/lib.rs`, `PadDriveParams`).
Microduck X enforces exactly these.

## 4. Policies

`robotd` loads ONNX graphs `obs[1,61] → actions[1,14]` (`docs/policy-manifest.md`,
`duck-control/src/policy.rs`). The walk-mode defaults (`robotd-params`, `resolved_with`) are
`velstand.onnx` for walking *and* standing (no stand network), `alpha_sitstand.onnx`,
`alpha_ground_pick.onnx`, kicks and `roulade.onnx`. The policies are **not** in either repository;
they come from the Hub.

## 5. Findings — broken, missing, or easy to get wrong

| # | Finding | Evidence | Consequence / what Microduck X does |
|---|---|---|---|
| F1 | **No trained policies are reachable from this cloud sandbox**: `huggingface.co` is refused by its egress proxy. | `seed-policies.sh` → `curl: (22) 403` | Walking with the official gait could **not** be demonstrated here. Everything up to the policy is verified; the gait itself must be verified on your machine (`x-setup.sh` fetches the set). |
| F2 | `robot.enable` answers `accepted: true, "enabled — driving"` even when the walk policy failed to load, and `robot.move` is reflected in `requested` while `policy` stays `held`. | Observed; integration test `test_robotd_reflects_intents_but_no_policy_means_no_driving` | A client that trusts `accepted` reports walking that never happens. Microduck X waits for `robot.state.policy != "held"` and refuses movement otherwise. |
| F3 | After the body restarts, robotd reconnects but does **not** replay torque; the new body holds the duck until a torque op arrives. A plain `robot.enable` on an enabled robotd does not send one; `robot.relax` then `robot.enable` does. | Integration test `test_body_restart_needs_relax_then_enable` (fixture policy) | Scene switches in Microduck X run relax → enable after checking the new body is holding the duck. |
| F4 | `scripts/duck-sim` writes `walk = alpha_walking.onnx`, `stand = alpha_stand.onnx` and requires `alpha_stand.onnx` to exist, while robotd's default walk is `velstand.onnx` with no stand network. | `scripts/duck-sim` `up()`/`policies()`; `robotd-params` comment "alpha_walking.onnx + alpha_stand.onnx stay in the set for a board that loads them back by hand" | **Unverified** whether set v8 still carries the alpha files (Hub unreachable). If not, `duck-sim` dies with "no policy set". `x-start.sh` prefers `velstand.onnx` and names absent slots `"none"`. |
| F5 | The full `microduck_rl` `uv sync` installs torch 2.9.1 (CUDA wheels on x86-64), mjlab and warp — several GB — but the body server imports only `mujoco` and `numpy` (plus `onnxruntime` for robotd's `ORT_DYLIB_PATH`). | `body_server.py`, `camera.py`, `tof.py` imports; `uv.lock` | `x-setup.sh` builds a 218 MB venv with the *locked* versions (mujoco 3.10.0, numpy 2.4.1, onnxruntime 1.24.4). `--full-rl` keeps the official path. |
| F6 | The body server's default scene `scene.xml` uses the *groundcontact* robot (11 collision geoms, a curated floor set); `scene_apartment.xml` uses *allcollisions* (70). | Compiled model counts | New scenes use allcollisions so furniture and walls collide with the whole robot, like the original apartment. `bare` stays the original default. |
| F8 | The body server steps the MJCF position actuators (kp 0.55, ±0.96 N·m), while training uses the BAM voltage model; `docs/robot/simulation.md` describes duck-body as having "the BAM actuator models the policies were trained against". | `body_server.py` (no BAM import), `actuator/friction_dr_bam.py`, `scripts/infer_policy.py --no-bam` | Read from code, not tested. A sim-to-sim gap of the original stack; documented, not changed. |
| F7 | `pocketsphinx` (the only speech engine runnable without a model download here) is weak: against synthetic espeak speech it got 5 of 11 command phrases exact, and once heard "stop" as "start". | `/api/voice` runs, recorded in the roadmap | Uncalibrated engines always ask for confirmation (except stop commands); cut-off phrases are refused; whisper is the intended engine and must be downloaded once. |

There was **no existing React dashboard, voice backend or scenic launcher** in the upload. The
two web pieces it does contain are `mediad/webclient` (the robot's WebRTC console) and
`spaces/policy-playground` (a Hugging Face Space that runs tricks on a signed-in duck). Neither is
modified.

## 6. What was built, and where it plugs in

Nothing in the original tree was modified (`git status` shows only `microduck-x/` added).
Microduck X sits *beside* the original stack:

```
Browser (Windows) ──HTTP/WS 127.0.0.1:8765──► control service (FastAPI, microduck-x/backend)
                                                 │  JSON-RPC over ~/.cache/duck-sim/duck-a.sock
                                                 ├──────────────► robotd --sim  (original, unmodified)
                                                 │                    │ TCP 7801, original body protocol
                                                 │  TCP 7811         ▼
                                                 └──────────────► xbody.py = body_server's World/Body/Handler
                                                                  + scenes + read-only state port
```

`ARCHITECTURE.md` describes the pieces; `CONTROL_PROTOCOL.md` the wire formats;
`ROBOT_MODEL.md` the model; `SIMULATION_SETUP.md` the Windows/WSL2 install;
`IMPLEMENTATION_ROADMAP.md` what is verified, what is not, and what is next.
