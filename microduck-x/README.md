# Microduck X — a voice-controlled virtual robotics lab for the simulated Microduck

Speak to the real Microduck software stack and watch the original robot model answer in MuJoCo,
in a web dashboard that draws exactly what the physics engine holds.

* **The original duck, unchanged.** MuJoCo body from `microduck_rl` (its `body_server` classes,
  imported, not copied), the real `robotd --sim` with its 50 Hz loop, ONNX policies, safety and
  fall reflex. No file of the original tree is modified.
* **A digital twin, not an animation.** The viewer is built from the *compiled* MuJoCo model
  (meshes, materials, textures) and moved by MuJoCo's body poses; a badge says LIVE, STALE or NOT LIVE.
* **The hangar (default page).** The real duck on a pedestal in an orbital hangar, rendered with
  studio lighting; a thought bubble and chat with robot babble and moods, a paint shop for six
  parts, one- and two-word commands ("dance", "spin", "nod", "home"), and a duck that *remembers*
  what you teach it ("when I say zoom, spin") — memory, not machine learning.
  [`docs/HANGAR.md`](docs/HANGAR.md).
* **Six worlds.** The hangar, the original bare floor and apartment, plus a robotics lab, a garden
  and a training arena (4 obstacle layouts) — every object with height is a collision geom.
* **Offline voice.** Push-to-talk or hands-free (VAD + "Microduck …"), faster-whisper or
  pocketsphinx, a deterministic parser onto an allowlisted command schema, confirmation for
  uncertain transcripts, spoken replies. An optional local LLM can only propose allowlisted commands.
* **Ask it anything (optional, online).** With a Groq API key in `microduck-x/.env`, chat questions
  the persona and parser cannot place are answered by `openai/gpt-oss-120b`, streamed, in the
  duck's voice; it can start only allowlisted moves. [`docs/HANGAR.md`](docs/HANGAR.md#open-questions-the-groq-brain-backendmicroduck_xbrainpy).
* **Honest outcomes.** A command is `completed` only with a measured result; "robotd accepted it"
  is never reported as "the robot did it". Emergency stop is a latch independent of speech and AI.
* **Lab tools.** Telemetry graphs (MuJoCo ground truth beside robotd's own belief), navigation on
  the scene's real geometry, repeatable experiments with pass/fail criteria, JSON/CSV export,
  run comparison, fall explanations from recorded data.

## Quick start (Ubuntu or WSL2 on Windows 11)

```sh
microduck-x/scripts/x-setup.sh                 # once: microduck_rl, venvs, robotd build, policies, dashboard
microduck-x/scripts/x-setup.sh --whisper base.en   # optional, ~145 MB, asks first
cp microduck-x/.env.example microduck-x/.env   # optional: put your GROQ_API_KEY in it for open questions
microduck-x/scripts/x-start.sh                 # MDX_ENV=hangar (default)|lab|garden|apartment|bare|arena
# open http://localhost:8765
microduck-x/scripts/x-stop.sh
```

Full guide: [`docs/SIMULATION_SETUP.md`](docs/SIMULATION_SETUP.md).

**Hosted demo.** `microduck-x/Dockerfile` and the repository's `render.yaml` run the dashboard,
chat, Groq brain and the duck's voice in front of the **MOCK** robot (not MuJoCo, not the
Microduck; the page says so) on Render's free plan. Set `GROQ_API_KEY` in Render's dashboard, then
open `/?token=<MDX_TOKEN>`. The real duck needs the setup above.

## Things to say

Short: "stand" · "dance" · "spin" · "left" · "nod" · "look around" · "home" · "status" · "stop"
([full list](docs/HANGAR.md#one--and-two-word-commands-parserpy-short)). Longer:
"Microduck, stand up" · "walk forward slowly for two seconds" · "turn right by ninety degrees" ·
"stop moving immediately" · "switch to the laboratory" · "camera follow mode" · "show the joint
angles" · "start the walking test" · "go to goal two" · "return to the starting position" ·
"push it gently from the left" · "why did it fall" · "compare the last two runs"

Keyboard: `W/S` forward/back, `A/D` turn, `Q/E` strafe, `Shift` faster, `Space` hold-to-talk,
`Esc` emergency stop, `1–4` camera, `P` pause physics. In the hangar, `Space` talks to the duck's chat.

## Layout

```
microduck-x/
├── sim/            xbody.py (body server + state port), environments.py (scenes via MjSpec)
├── backend/        microduck_x: FastAPI service — robotd client, parser, schema, safety, executor,
│                   telemetry, navigation, experiments, speech, persona (chat + memory), mock
├── frontend/       React + TypeScript + Three.js: hangar page + lab dashboard
├── configs/        experiments/*.json
├── scripts/        x-setup.sh, x-start.sh, x-stop.sh, x-status.sh
├── tests/          unit, mock-backed, and real-stack integration tests
└── docs/           ARCHITECTURE_AUDIT, ARCHITECTURE, ROBOT_MODEL, CONTROL_PROTOCOL,
                    SIMULATION_SETUP, IMPLEMENTATION_ROADMAP, HANGAR
```

## Status, briefly

Verified against real MuJoCo + real robotd: model fidelity, scenes, live twin, the control path,
e-stop, telemetry, experiments, start/stop. **Not verified where this was built:** walking with
the official trained gait and whisper transcription, because that machine could not reach
huggingface.co. Both are one `x-setup.sh` away on yours. See
[`docs/IMPLEMENTATION_ROADMAP.md`](docs/IMPLEMENTATION_ROADMAP.md) for the itemised list.

## Tests

```sh
microduck-x/.venv/bin/python -m pytest -q microduck-x/tests
MDX_IT=1 DUCK_SIM_RL=~/Pollen/microduck_rl microduck-x/.venv/bin/python -m pytest -q -m integration microduck-x/tests
cd microduck-x/frontend && npm run typecheck
```
