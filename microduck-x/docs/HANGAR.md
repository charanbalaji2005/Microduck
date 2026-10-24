# The hangar: chat, persona, paint shop, short commands

The hangar is the default page (`http://localhost:8765`) and the default scene (`MDX_ENV=hangar`).
It is a friendlier front door to the same machinery as the lab dashboard (`▣ Lab`, top right):
every chip, typed line and spoken sentence goes through the same parser, allowlisted command
schema, safety gate and executor ([`CONTROL_PROTOCOL.md`](CONTROL_PROTOCOL.md)). Nothing in the
hangar can make the robot do something the lab could not.

The look — a duck on a lit pedestal in an orbital hangar, a thought bubble, a chat panel, a
colour customiser — is an original implementation *inspired by* screenshots of Onur Solmaz's
Microquack. No code, art or branding from that project is used (its repository was not available).

## What is physical and what is not

| Thing | Physical (MuJoCo collision geom)? |
|---|---|
| Deck, pedestal (5 cm step, 0.76 m radius top), railing, bulkheads, pipes, consoles, gantry, crates | **Yes** — built in `sim/environments.py` (`hangar`), compiled with the robot |
| Planet, moon, stars | **No** — `frontend/src/sim/backdrop.ts`, drawn by the viewer only; the footer says so |
| Glow on the pedestal rim, console screens, deck lights | Visual material on real geoms |
| Duck colours | Viewer materials only; the MuJoCo model's masses and geometry are unchanged |

The pedestal edge is a real drop: `"where are we?"` says so, and walking off it can tip the duck.

## Rendering

The duck drawn is the **compiled Microduck model** from the body server (as in the lab, see
[`ROBOT_MODEL.md`](ROBOT_MODEL.md)); the hangar only renders it better: PMREM room environment
lighting, creased normals on the robot meshes, a `MeshPhysicalMaterial` per part (clear-coat shell,
satin trim, metal bearings, dark frame), a soft-shadowed key spot, rim and fill lights, ACES tone
mapping and a light bloom pass whose threshold sits above lit white plastic, so lamps and screens
glow and the shell does not. A contact shadow (`frontend/src/sim/contactShadow.ts`) darkens the floor
where the feet meet it: an orthographic camera at floor level renders only the robot's layer as
"distance from the floor", blurred onto a decal under the duck, so a lifted foot fades out by
itself. When the chat panel is open the image is shifted with `camera.setViewOffset` so the duck
sits beside it; the camera itself is unchanged.

The camera is the user's (orbit, zoom, pan; touch too). It opens with an establishing move onto the
duck, and a double-click or double-tap (or "camera reset") swings it back to the duck's front;
either move travels around the duck, and touching the controls ends it at once.
`prefers-reduced-motion` skips both. Quality adapts to the device and to measured frame times
(pixel ratio, then bloom, then shadow resolution); models, materials and lights stay the same.

## Paint shop ("Customize")

Six parts — Shell, Face, Trim, Beak, Eye, Feet — are mapped by **mesh name** in
`frontend/src/sim/duckLook.ts` (e.g. Shell = `top_head_shell`, `left_shell`, `right_shell`,
`upper_leg_*`). Colours are saved in the browser, and with the duck's name on `POST /api/duck`.
`Share` copies a link with the colours in its `#duck=` fragment; nothing is uploaded anywhere.

## Chat and persona (`backend/microduck_x/persona.py`)

`POST /api/chat {"text": "...", "source": "chat", "session": "..."}` and `POST /api/chat/voice`
(multipart `audio`, `session`). Each turn returns the duck's reply as `{babble, text, mood, confirm?}`
and is broadcast on `/ws` as `chat.user` / `chat.reply` events. `babble` is generated robot noise;
`text` is the translation shown under it. `session` (1–64 of `A-Za-z0-9_-`, one per browser tab)
only matters to the brain below. Messages are at most `MDX_CHAT_MAX_CHARS` (2000) characters.

The order of a turn:

1. **Persona phrases** first — greetings, "good duck", jokes, "where are we", "good night",
   "who are you", "why did you fall" (answered from recorded telemetry), teaching and memory.
   Some of these also trigger a harmless action (e.g. a nod) through the normal executor.
2. Otherwise the line is **parsed as a command**. If it parses, the duck acknowledges *and* the
   commands run. If every command is rejected (e.g. no policy loaded), the acknowledgement is
   dropped and the refusal is what you read — the duck never says "on it!" and then doesn't.
3. If neither placed the line — nothing parsed, or most of it was not a command — and the Groq
   brain is on, it is an **open question** for the brain (below). A line with a stop in it never is.
4. Outcomes are narrated from the execution record (`completed` only with a measured result).

"Status report" says "No faults detected" only when the safety gate has no blockers; otherwise
it lists them.

### Open questions: the Groq brain (`backend/microduck_x/brain.py`)

Off unless `GROQ_API_KEY` is set (copy `microduck-x/.env.example` to `microduck-x/.env`; the real
environment wins over the file). Model `GROQ_MODEL`, default `openai/gpt-oss-120b`, through the
official `groq` SDK, streaming. **This is the one part of the hangar that leaves the machine**: the
question, this session's recent conversation and a few live facts (scene, whether the legs can
move, current activity, pose, names) go to Groq. Commands, stops and the e-stop never do. The key
is read by the service only; it is never logged, returned by an API or sent to the page.

* **A turn.** `POST /api/chat` answers at once with `pending: {id}`; the words stream to the page
  as transient `/ws` events (`chat.thinking`, then `chat.delta` with the text so far — not kept,
  not logged), then the whole reply arrives as an ordinary `chat.reply` with `session`,
  `brain.model`, `sources` and `actions`. The model's reasoning is never shown or spoken.
* **One open question per session at a time** (a second gets `409`; a stop, a chip or a command
  still goes through). `POST /api/chat/cancel {session}` stops the answer and keeps the words so
  far. `GROQ_RATE_PER_MIN` (20) limits questions across sessions (`429`).
* **Memory** is this session's last `GROQ_HISTORY_MESSAGES` messages (16) and at most
  `GROQ_HISTORY_CHARS` (16000), in the service's memory only — gone on restart, oldest exchange
  dropped first. `POST /api/chat/clear {session}` (the chat's *Clear*) forgets it. The duck is
  told it does not remember earlier conversations. Names come from the persona's memory file above.
* **Actions.** The model has one tool, `duck_actions`, with an allowlist: `walk_forward`,
  `walk_backward` (`seconds` 0.5–10), `turn_left`, `turn_right` (`degrees` 5–360), `look_around`,
  `stop_movement`, `start_exploring`, `rest`, `speak`, at most three per call. Each is validated
  (unknown actions, extra fields and out-of-range numbers are refused and the model is told so),
  turned into the same commands a chip or phrase makes, and submitted as one sequence to the
  executor with source `llm`, where the safety gate applies as for anything else. The model is told
  the actions *started*; how they went is narrated afterwards from the execution record, like any
  chat command. Nothing it returns is run as code.
* **Lookups.** A `look_up` tool is offered only when a retriever is configured — `GROQ_WEB_SEARCH=1`
  uses Groq's built-in browser search (an external web search run by Groq). The `Retriever` protocol
  in `brain.py` is where approved docs or a local RAG index would plug in. Sources are shown only
  when a lookup actually ran and returned them; with no retriever the duck says its knowledge may
  be out of date.
* **Errors** become a sentence in the chat — key refused, rate-limited, timed out (`GROQ_TIMEOUT_S`,
  30 s per request), no connection, model not accepted — with *Try again* when retrying can help.
  The SDK's own error text is never shown. A failed turn is not remembered.
* **Voice.** The reply is spoken by the duck's own synthesised voice (`frontend/src/voice/duckVoice.ts`,
  robot tags chosen by mood and length; Groq produces words, never audio) and the beak follows that
  playback's loudness envelope. A long answer is said as up to six short utterances, one per ~24
  words of its text, with a breath between them (`voice/events.ts` `speechChunks`), so it has the
  answer's shape without lasting a minute. A new reply cuts off the previous one; sending a message
  cuts it off at once. The bar under the chat header (`voice/VoiceControls.tsx`) shows *Speaking*,
  *Paused* or *held by browser*, with pause / resume, stop, replay, mute, volume and *Auto speak*;
  each answer's toolbar can play it in the duck's voice again. Pausing stops the audio clock, so the
  beak shuts and holds. None of it touches the legs; the duck talks while walking. If the browser
  holds audio until the page is clicked (autoplay rules), the reply is dropped rather than played
  late, and the chat says so with *Retry voice*, which replays it.
* **The page** draws a reply by its content (below).

`GET /api/status` → `brain` says whether it is on, the model, and why not when it is off.

#### How an answer is drawn (`frontend/src/components/chat/`)

The model writes Markdown — it streams well, and half of it is still readable — with code in
fences that name the language and a file (```` ```python title="app.py" ````), maths in LaTeX
(`$…$`, `\(…\)` inline; `$$…$$`, `\[…\]` displayed) and, optionally, a last line
`>> follow-ups: q1 | q2`. Nothing is asked of it as JSON. When the answer is complete, `brain.structure()`
derives the metadata from the text itself and sends it with the `chat.reply` as `structured`:
`kind` (`conversation`, `math`, `code`, `long`, `list`, `error`, `action`), a one-line `summary`,
the code blocks (`language`, `filename`, `lines`) and how much maths there is; the follow-ups line
is removed from the text and sent as `followups`. The page checks that shape
(`lib/chatContent.ts` `structuredOf`) and, if it is missing or malformed — or while the reply is
still streaming — classifies the text itself.

| Piece | Does |
|---|---|
| `AIMessage` | one brain reply: the duck-speak strip (the babble, optional — *Duck-speak on/off*; never in place of the answer), the answer, actions started, sources, follow-up chips, toolbar |
| `ResponseRenderer` | picks the drawing by kind; an error while rendering falls back to the plain text |
| `MarkdownRenderer` | headings, lists, tables, quotes, links (`http(s)` only), as React elements |
| `MathResponse` | KaTeX, `trust: false` (no `\href`/`\url` links), errors shown inline; copy an equation, the equations, or the solution |
| `CodeBlock` | highlight.js (escapes the source), language badge, file name, line numbers on/off, copy, download, *Show all N lines* past 24, large view; the code is shown and copied whole and never run |
| `ExpandableResponse` | answers over ~1400 characters, 28 lines or 40 lines of code start collapsed — and only if they really overflow |
| `ResponseToolbar`, `ResponseActionsMenu` | on hover/focus (always on touch): copy, share, read aloud / stop, regenerate, 👍/👎, and a menu of what fits the kind (copy code, copy code with formatting, download code or Markdown, copy equations, expand/collapse) |
| `ShareDialog` | shares the answer's or one code block's **text** — the system share sheet where there is one, else copy. There is no conversation-link service, so no link is made |
| `FeedbackControls` | `POST /api/chat/feedback {id, rating: up/down/null}` → a `chat.feedback` event in the log; stored nowhere else |

KaTeX and highlight.js are the only places markup is generated from model text; both escape it.
*Read aloud* is the browser's speech synthesis reading the words (code and equations are named,
not spelled out); the beak follows the duck's own voice only. *Regenerate* re-sends the question
with `regenerate: true`, and the brain replaces that exchange in the session's memory instead of
stacking a second one. Copy reports success only after the browser does, and says when it fell
back to plain text or failed.

### "Learning" — what it is, honestly

The duck **remembers**; it does not train. Memory is a JSON file,
`~/.cache/duck-sim/x-duck-memory.json`:

| Say | Effect |
|---|---|
| "when I say zoom, walk forward fast for 3 seconds" / "teach zoom means spin" | stores an alias — only if the meaning already parses into valid commands |
| "zoom" | runs the stored commands (validated again at run time) |
| "forget zoom" · "what have you learned?" | remove / list aliases |
| "my name is Charan" · "your name is Pip" | remembered, used in replies and the title |
| "faster" · "slower" | scales the speed of short commands, 0.6–1.6×, still clamped to the trained envelope |
| "again" | repeats the last command |

Mood (shown under each reply: happy, curious, sorry, ramble…) labels the tone of that reply and
picks the babble's sounds. It does not affect control. No neural network is trained or fine-tuned by anything in the hangar; training
policies is out of scope ([`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md)).

## One- and two-word commands (`parser.py`, `SHORT`)

Matched before the full grammar, so a single word does what you expect. Synonyms in brackets.

| Words | Command |
|---|---|
| forward (go, walk, march) · back (reverse, back up) | walk 2 s at 0.2 / −0.15 m/s |
| left · right · around (u turn) · spin (twirl) | turn 90° / −90° / 180° / 360°, closed loop on measured yaw |
| step left · step right (strafe, slide) | sidestep 2 s |
| stand (up, wake up, start) · sit (rest, sleep) | `enable` · `robot.do sit_toggle` |
| dance (boogie, party) · spin dance · bob (groove) | `dance` — wiggle + turns + celebrate / 360° / bounce + nod |
| nod (yes) · shake (no) · look left/right/up/down · look around · wiggle · crouch · bow · celebrate (yay) · curious (hmm) · sleepy (yawn) | `emote` |
| kick · kick left · roll · pick | `robot.do` skills, if robotd reports them loaded |
| home (come here) · patrol | go to the start marker · the scene's waypoint tour |
| push · status · help · test | gentle 2 N push · status · help · the 1 m walk experiment |
| pause · resume · reset | simulator |
| follow · orbit · top · fpv | camera |
| hangar · lab · garden · arena · apartment · bare | switch scene |
| stop · STOP (any "stop…") · Esc | stop / e-stop latch, independent of chat and AI |

### How emotes and dances move the robot

`emote` and `dance` are executor sequences over robotd's own allowlisted calls — `robot.look`
(robotd's IK to head joints), `robot.head`, `robot.pose {z, roll, pitch}` within the trained
range (z −0.025…0.010 m, roll/pitch ±0.26 rad), and `turn`. They are gated like walking (policy
present, not fallen, not e-stopped, fresh state). An emote is `completed` only if robot.state shows
the head actually moved (≥ 0.03 rad) and the duck is upright afterwards; the head is restored at
the end. **Not verified here**: how good they look with the official policies (see the roadmap).
