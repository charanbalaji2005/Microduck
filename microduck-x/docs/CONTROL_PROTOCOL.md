# Wire formats

Three protocols, two of them original.

## 1. robotd JSON-RPC 2.0 (original, Unix socket)

Socket: `~/.cache/duck-sim/duck-a.sock` (the same place `scripts/duck-sim` uses). One JSON object
per line. Requests carry `id` and get one answer; notifications have no `id` and get none.
The subset Microduck X calls — and the allowlist in `backend/microduck_x/robotd.py`:

| Call | Kind | Used for |
|---|---|---|
| `robot.subscribe {"hz": 25}` | request → stream of `robot.state` | all robot-side telemetry |
| `robot.move {"vx","vy","vyaw"}` | notification at 20 Hz | walk, turn, navigation, keyboard |
| `robot.stop` | request | stop, end of every motion |
| `robot.enable {"on": bool}` | request | stand up / disable |
| `robot.relax` | request | only during a scene switch, while the new body holds the duck |
| `robot.do {"skill"}` | request | roulade, kicks, ground pick, sit toggle |
| `robot.policies`, `robot.health`, `robot.model` | request | status pages |

Anything else is refused before it is sent. Examples (verbatim from a session):

```json
→ {"jsonrpc":"2.0","id":3,"method":"robot.enable","params":{"on":true}}
← {"jsonrpc":"2.0","id":3,"result":{"accepted":true,"reason":"enabled — driving"}}
→ {"jsonrpc":"2.0","method":"robot.move","params":{"vx":0.2,"vy":0.0,"vyaw":0.0}}
```

**Accepted is not driving.** `robot.enable` answers `accepted` even when the walk policy failed to
load (audit F2). The control service treats a command as executing only when `robot.state.policy`
names a policy and `robot.state.move.requested` equals what was sent.

## 2. The body protocol (original, TCP 7801)

`duck_control::sim::RemoteIo` ⇄ `body_server.Handler`, protocol 1: `hello`, `read`, `write`,
`gain`, `torque`, `slow`, `tof`. Microduck X does not touch it; xbody serves it with the
original handler class.

## 3. The xbody state port (Microduck X, TCP 7811, localhost only)

Newline JSON. Read-only except four physics-admin ops.

| Request | Answer |
|---|---|
| `{"op":"hello"}` | `{"protocol":"xbody/1","env":"lab","opts":{},"ducks":1,"robot_protocol":1}` |
| `{"op":"model"}` | one JSON header line, then `mesh_bytes` of mesh data (all vertices as `<f4`, then all faces as `<u4`) and `texture_bytes` of RGBA textures |
| `{"op":"subscribe","hz":30}` | a frame per line until disconnect |
| `{"op":"pause"}` / `{"op":"resume"}` | physics stepping off/on |
| `{"op":"reset","keyframe":"SIT"}` | scenery restored, every duck back at its spawn |
| `{"op":"push","duck":0,"force":[fx,fy,fz],"seconds":0.2}` | world-frame force on the trunk; ≤ 20 N, ≤ 1 s |
| `{"op":"stats"}`, `{"op":"environments"}` | real-time factor, steps; scene catalogue |

A frame:

```json
{"seq":812,"t":16.24,"wall":1792680000.1,"paused":false,"rtf":1.0,
 "xpos":[...3 per moving body...],"xquat":[...4 per moving body, w x y z...],
 "ducks":[{"trunk":[x,y,z],"quat":[w,x,y,z],"gravity":[gx,gy,gz],"yaw":0.01,
           "linvel":[world],"angvel":[local],"q":[15, null for mouth],"qd":[15],"ctrl":[15],"force":[15],
           "torque_on":true,"released":true,"kp":200,"feet":{"left":true,"right":true},"external_contacts":4}],
 "contacts":[[x,y,z,geom1,geom2,normal_force_N], ...]}
```

`model.dynamic_bodies` lists which bodies the `xpos`/`xquat` arrays describe, in order.

## 4. The control service (Microduck X, HTTP + WebSocket, 127.0.0.1:8765)

Binding beyond localhost refuses to start without `MDX_TOKEN` (then send `x-mdx-token`).

| Endpoint | |
|---|---|
| `GET /api/health`, `GET /api/status` | liveness; everything the status bar shows |
| `POST /api/command {"command":{...},"source":"ui"}` | one validated command → execution record |
| `POST /api/text {"text":"walk forward for 2 seconds"}` | parse (+ optional LLM) and run |
| `POST /api/voice` (multipart `audio`, `execute`, `hands_free`) | transcribe offline, parse, run or ask to confirm |
| `POST /api/estop`, `POST /api/estop/clear`, `POST /api/cancel` | latching stop; clear; cancel the running task |
| `GET /api/model`, `/api/model/meshes.bin`, `/api/model/textures.bin` | the compiled scene for the viewer |
| `GET /api/environments`, `GET /api/policies` | scene catalogue; robotd's slot table |
| `GET /api/experiments`, `POST /api/experiments/{name}/run`, `GET /api/runs[/{id}][/export.json|.csv]`, `GET /api/compare?a=&b=` | experiments |
| `POST /api/nav/preview {"target":"goal_2"}`, `GET /api/nav/plan` | plan without moving; last plan |
| `GET /api/logs`, `GET /api/telemetry/history`, `GET /api/voice/status`, `POST /api/voice/download`, `GET /api/tts?text=` | diagnostics, speech |
| `POST /api/chat {"text"}`, `POST /api/chat/voice`, `GET/POST /api/duck` | the hangar's chat and the duck's memory — see [`HANGAR.md`](HANGAR.md) |
| `WS /ws` | in: `sim`, `robot`, `status`, `event`, `pong`; out: `teleop {vx,vy,vyaw}`, `ping`, `estop`, `command` |

### The command schema (`backend/microduck_x/commands.py`)

Discriminated on `action`; extra fields are refused; bounds are enforced at construction.

| action | fields | goes to |
|---|---|---|
| `walk` | vx ±0.4, vy ±0.3, vyaw ±1.0, duration_s ≤ 30 | robotd |
| `turn` | vyaw ±1.0, angle_deg ±720 *or* duration_s | robotd (angle: closed loop on measured yaw, extension) |
| `hold` | duration_s ≤ 120 | robotd (zero twist, fall watch) |
| `emote` | gesture ∈ nod, shake, look_left/right/up/down, wiggle, crouch, bow, celebrate, curious, sleepy; duration_s ≤ 6 | robotd `robot.look` / `robot.head` / `robot.pose` ([`HANGAR.md`](HANGAR.md)) |
| `dance` | style ∈ shuffle, spin, bob | sequence of emotes and turns |
| `stop`, `estop`, `clear_estop` | — | robotd / gate |
| `enable`, `disable` | — | robotd |
| `skill` | skill ∈ what robotd reported | robotd `robot.do` |
| `pause`, `resume`, `reset {keyframe}`, `push {direction, newtons ≤ 10, seconds ≤ 1}` | | simulator |
| `set_environment {environment, layout}` | hangar, bare, apartment, lab, garden, arena | simulator (body restart) |
| `camera {mode}`, `show_panel {panel, visible}` | | dashboard |
| `goto {target | x,y, speed}`, `waypoints {targets}` | | extension over robot.move |
| `run_experiment {experiment, trials}`, `save_experiment`, `compare_runs`, `status {topic}`, `help` | | extension |

Every execution record: `id, action, command, source, interface, transcript, confidence, status
(queued|running|completed|failed|rejected|cancelled|superseded|delivered), message, outcome`.
`outcome` of a movement holds what was *measured*: forward/lateral displacement and yaw change
from simulator ground truth, robotd's odometry distance, whether the controller acknowledged the
intent, and the policy that was driving.
