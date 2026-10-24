"""Microduck X control service: localhost HTTP + WebSocket in front of robotd and the MuJoCo body.

    python -m microduck_x            # 127.0.0.1:8765

Separate process from both the simulator and the browser. The browser never talks to robotd or
MuJoCo directly; it talks to this, and this talks only through allowlisted calls.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import json
import time
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__, llm, speech_out
from .brain import Brain, BrainError, mood_of, structure
from .commands import Envelope, action_names
from .config import Settings
from .events import EventBus
from .executor import Executor
from .experiments import ExperimentRunner, ExperimentStore
from .grammar import write_grammar
from .parser import parse
from .persona import Memory, Persona, babble
from .robotd import RobotdClient
from .safety import SafetyMonitor
from .stt import Speech, SttUnavailable
from .supervisor import SimSupervisor
from .telemetry import TelemetryHub
from .xbody import XbodyClient


class Service:
    def __init__(self, settings: Settings):
        if settings.mock:
            from .mock import mock_paths

            settings.robot_socket, settings.xbody_state_port = mock_paths(settings)
            settings.manage_sim = False
        self.s = settings
        self.events = EventBus()
        self.hub = TelemetryHub()
        self.robot = RobotdClient(settings.robot_socket)
        self.xbody = XbodyClient(settings.xbody_host, settings.xbody_state_port)
        self.safety = SafetyMonitor(settings, self.hub, self.events)
        self.supervisor = SimSupervisor(settings, self.events) if settings.manage_sim else None
        self.store = ExperimentStore(settings)
        self.runner = ExperimentRunner(settings, self.hub, self.events, self.store)
        self.executor = Executor(settings, self.robot, self.xbody, self.hub, self.safety, self.events,
                                 self.supervisor, self.runner, self.store)
        grammar = write_grammar(settings.state_dir / "x-voice")
        self.speech = Speech(settings.stt_engine, settings.whisper_model, settings.models_dir,
                             settings.whisper_device, grammar)
        self.hub.subscribe(self.safety.on_sample)
        self.memory = Memory.load(settings.state_dir / "x-duck-memory.json")
        self.brain = Brain(settings)
        self.persona = Persona(self.memory, self.persona_context, lambda: self.brain.enabled)
        self.chats: dict[str, asyncio.Task] = {}  # session → the brain turn running for it
        self.tasks: list[asyncio.Task] = []
        self.mock = None
        self.started = time.time()

    async def start(self) -> None:
        if self.s.mock:
            from .mock import MockWorld

            self.mock = MockWorld(self.s)
            await self.mock.start()
            self.events.emit("service.mock", "MOCK mode: a fake robotd and a fake simulator — not the Microduck", "warn")
        if self.supervisor is not None:
            try:
                if await self.supervisor.ensure_started():
                    self.events.emit("sim.started", f"started the MuJoCo body in '{self.supervisor.env}'")
            except Exception as error:  # noqa: BLE001
                self.events.emit("sim.error", f"could not start the simulator: {error}", "error")

        def robot_status(up: bool, why: str | None):
            self.events.emit("robotd.connected" if up else "robotd.disconnected",
                             "robotd connected" if up else f"robotd lost: {why}", "info" if up else "warn",
                             ack=self.robot.ack)

        def sim_status(up: bool, why: str | None):
            self.events.emit("sim.connected" if up else "sim.disconnected",
                             f"simulator connected ({(self.xbody.hello or {}).get('env')})" if up else f"simulator lost: {why}",
                             "info" if up else "warn")
            if up:
                asyncio.ensure_future(self.executor.load_model_index())

        def on_frame(frame):
            self.hub.on_sim(frame)
            self.executor.on_frame(frame)

        self.tasks += [
            asyncio.create_task(self.robot.stream_states(25, self.hub.on_robot, robot_status)),
            asyncio.create_task(self.xbody.stream(30, on_frame, sim_status)),
            asyncio.create_task(self.executor.teleop_watchdog()),
            asyncio.create_task(self.executor.talk_loop()),
        ]
        self.events.emit("service.start", f"microduck-x {__version__} on {self.s.host}:{self.s.port}",
                         robot_socket=str(self.s.robot_socket), sim=f"{self.s.xbody_host}:{self.s.xbody_state_port}")
        b = self.brain
        self.events.emit("brain.config", f"Groq brain on: {self.s.groq_model}, reasoning {self.s.groq_reasoning_effort}"
                         + (f", lookups via {b.retriever.name}" if b.retriever else ", no lookups") if b.enabled
                         else f"Groq brain off: {b.problem}", "info" if b.enabled or not self.s.groq_api_key else "warn")

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.executor.cancel_all("service shutting down"), 2)
            await asyncio.wait_for(self.robot.move(0, 0, 0), 0.5)
        for t in [*self.tasks, *self.chats.values()]:
            t.cancel()
        if self.supervisor is not None and os.environ.get("MDX_KEEP_SIM") != "1":
            await asyncio.to_thread(self.supervisor.stop_sync)
        if self.mock:
            await self.mock.stop()

    def persona_context(self) -> dict:
        header = self.xbody._model[0] if self.xbody._model else None
        pose, _src = self.executor.pose()
        return dict(status=self.status(), env=header["env"] if header else None, pose=pose,
                    fall_text=self.safety.explain_last_fall() if self.safety.falls else None,
                    teleop=self.executor._teleop_active)

    def brain_facts(self) -> str:
        """What the brain may know about the robot right now — read from live telemetry, like the
        persona's own facts, so "what are you doing?" is answered from the robot, not imagined."""
        ctx = self.persona_context()
        st = ctx["status"]
        r, safety, act = st["robotd"], st["safety"], st.get("activity") or {}
        env = (ctx.get("env") or {}).get("title")
        lines = [f"Your name: {self.memory.duck_name}."]
        if self.memory.user_name:
            lines.append(f"The user's name (saved by the app): {self.memory.user_name}.")
        lines.append(f"Scene: {env}." if env else "Scene: unknown (the simulator is not connected).")
        if not r.get("connected"):
            lines.append("Your legs: robotd is not connected, so you cannot move.")
        elif safety.get("blockers"):
            lines.append("You cannot move right now: " + "; ".join(safety["blockers"]) + ".")
        else:
            lines.append(f"Your legs: policy '{r.get('policy')}' is driving; you can move.")
        if act:
            lines.append(f"Doing now: {act.get('action')} ({act.get('status')}).")
        elif ctx.get("teleop"):
            lines.append("Doing now: the user is driving you with the keyboard.")
        if ctx.get("pose"):
            x, y, yaw = ctx["pose"]
            lines.append(f"Position: x {x:.2f} m, y {y:.2f} m, heading {round(yaw * 57.3) % 360}°.")
        return "\n".join(lines)

    def status(self) -> dict:
        r, ack = self.robot.last_state, self.robot.ack or {}
        frame = self.xbody.last_frame or {}
        return dict(
            version=__version__, uptime_s=round(time.time() - self.started, 1), mock=bool(self.s.mock),
            robotd=dict(connected=self.robot.connected, socket=str(self.s.robot_socket),
                        socket_present=self.robot.socket_present, error=self.robot.last_error,
                        age_s=_fin(self.hub.robot_age()), rate_hz=round(self.hub.robot_rate.hz, 1),
                        policy=r.get("policy") if r else None, loop_hz=r.get("loop", {}).get("hz") if r else None,
                        walk=ack.get("walk"), stand=ack.get("stand"), skills=self.executor.available_skills(),
                        policy_unavailable=ack.get("unavailable")),
            sim=dict(connected=self.xbody.connected, address=f"{self.s.xbody_host}:{self.s.xbody_state_port}",
                     error=self.xbody.last_error, env=(self.xbody.hello or {}).get("env"),
                     opts=(self.xbody.hello or {}).get("opts"), age_s=_fin(self.hub.sim_age()),
                     rate_hz=round(self.hub.sim_rate.hz, 1), rtf=frame.get("rtf"), paused=frame.get("paused"),
                     sim_time=frame.get("t"), managed=self.supervisor is not None),
            safety=self.safety.status(), activity=self.executor.current(),
            speech=self.speech.status(), tts=dict(espeak=speech_out.available()),
            llm=dict(enabled=bool(self.s.ollama_model), model=self.s.ollama_model or None),
            brain=self.brain.status(),
            config=dict(local_only=self.s.local_only, auth=bool(self.s.token), max_command_s=self.s.max_command_s,
                        limits=dict(vx=self.s.vx_max, vy=self.s.vy_max, vyaw=self.s.vyaw_max),
                        stt_min_confidence=self.s.stt_min_confidence, require_wake_word=self.s.require_wake_word),
        )


def _fin(v: float):
    return None if v == float("inf") else round(v, 3)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    svc = Service(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        await svc.start()
        yield
        await svc.stop()

    app = FastAPI(title="Microduck X", version=__version__, lifespan=lifespan)
    app.state.svc = svc
    app.add_middleware(GZipMiddleware, minimum_size=2048)

    def auth(request: Request):
        if settings.token and request.headers.get("x-mdx-token") != settings.token \
                and request.query_params.get("token") != settings.token:
            raise HTTPException(401, "missing or wrong MDX token")

    guard = [Depends(auth)]

    # ── status ────────────────────────────────────────────────────────────

    @app.get("/api/health")
    async def health():
        st = svc.status()
        return dict(ok=st["robotd"]["connected"] and (st["sim"]["connected"] or settings.mock),
                    robotd=st["robotd"]["connected"], sim=st["sim"]["connected"], mock=settings.mock,
                    motion_allowed=st["safety"]["motion_allowed"])

    @app.get("/api/status", dependencies=guard)
    async def status():
        return svc.status()

    @app.get("/api/actions", dependencies=guard)
    async def actions():
        return dict(actions=action_names(), skills=svc.executor.available_skills())

    @app.get("/api/policies", dependencies=guard)
    async def policies():
        try:
            slots = await svc.robot.call("robot.policies")
        except Exception as error:  # noqa: BLE001
            slots = {"error": str(error)}
        return dict(ack=svc.robot.ack, policies=slots)

    # ── the model, for the viewer ─────────────────────────────────────────

    @app.get("/api/model", dependencies=guard)
    async def model():
        try:
            header, _, _ = await svc.xbody.model()
        except Exception as error:  # noqa: BLE001
            raise HTTPException(503, f"simulator not reachable: {error}")
        return JSONResponse(header, headers={"Cache-Control": "no-store"})

    @app.get("/api/model/meshes.bin", dependencies=guard)
    async def meshes():
        _, mesh, _ = await svc.xbody.model()
        return Response(mesh, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/model/textures.bin", dependencies=guard)
    async def textures():
        _, _, tex = await svc.xbody.model()
        return Response(tex, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/environments", dependencies=guard)
    async def environments():
        try:
            return await svc.xbody.op({"op": "environments"})
        except Exception as error:  # noqa: BLE001
            raise HTTPException(503, f"simulator not reachable: {error}")

    # ── commands ──────────────────────────────────────────────────────────

    async def run_envelopes(envs: list[Envelope]) -> list[dict]:
        if len(envs) == 1:
            return [(await svc.executor.submit(envs[0])).as_dict()]
        svc.executor.submit_sequence(envs)
        await asyncio.sleep(0.05)
        return [dict(id=e.id, action=e.command.action, status="queued") for e in envs]

    @app.post("/api/command", dependencies=guard)
    async def command(body: dict = Body(...)):
        try:
            fields = {k: v for k, v in body.items() if k not in ("wait", "timeout")}
            env = Envelope.model_validate({"source": "api", **fields} if "command" in fields else
                                          {"command": fields, "source": "api"})
        except ValidationError as error:
            raise HTTPException(422, error.errors(include_url=False))
        ex = await svc.executor.submit(env)
        if body.get("wait"):
            await svc.executor.wait(ex, timeout=min(float(body.get("timeout", 60)), 600))
        return ex.as_dict()

    @app.get("/api/commands", dependencies=guard)
    async def commands(limit: int = 100):
        return [e.as_dict() for e in list(svc.executor.history)[-limit:]][::-1]

    @app.get("/api/commands/{cid}", dependencies=guard)
    async def command_get(cid: str):
        for e in svc.executor.history:
            if e.id == cid:
                return e.as_dict()
        raise HTTPException(404, "no such command")

    @app.post("/api/cancel", dependencies=guard)
    async def cancel():
        await svc.executor.cancel_all("cancelled from the dashboard")
        await svc.executor._halt_quietly()
        return {"ok": True}

    @app.post("/api/estop", dependencies=guard)
    async def estop():
        return await svc.executor.estop("dashboard emergency stop")

    @app.post("/api/estop/clear", dependencies=guard)
    async def estop_clear():
        svc.safety.clear()
        return {"ok": True}

    async def interpret(text: str, source: str, confidence: float | None, require_wake: bool) -> dict:
        result = parse(text)
        out = dict(parse=result.as_dict(), executions=[], llm=None)
        if require_wake and not result.wake_word:
            out["ignored"] = "no wake word ('Microduck …')"
            return out
        cmds = list(result.commands)
        if (result.unmatched or not cmds) and not result.errors and settings.ollama_model:
            try:
                proposed, note = await llm.interpret(settings.ollama_url, settings.ollama_model,
                                                     " ".join(result.unmatched) or result.normalized)
                out["llm"] = dict(commands=[c.model_dump() for c in proposed], note=note)
                if proposed:
                    cmds += proposed
                    source = "llm" if not result.commands else source
            except Exception as error:  # noqa: BLE001
                out["llm"] = dict(error=str(error))
        out["commands"] = [c.model_dump() for c in cmds]
        out["envelopes"] = [Envelope(command=c, source=source, confidence=confidence, transcript=text) for c in cmds]
        return out

    def reply_for(out: dict) -> str:
        p = out["parse"]
        if out.get("ignored"):
            return ""
        if p["errors"]:
            return p["errors"][0]
        if not out["commands"]:
            return f"I did not understand '{p['normalized']}'." if p["normalized"] else ""
        return ""

    @app.post("/api/text", dependencies=guard)
    async def text(body: dict = Body(...)):
        t = str(body.get("text", ""))[:500]
        execute = bool(body.get("execute", True))
        out = await interpret(t, body.get("source", "text"), None, False)
        envs = out.pop("envelopes")
        if execute and envs and not out["parse"]["errors"]:
            out["executions"] = await run_envelopes(envs)
        out["reply"] = reply_for(out)
        return out

    @app.post("/api/voice", dependencies=guard)
    async def voice(audio: UploadFile = File(...), execute: bool = Form(True), hands_free: bool = Form(False)):
        data = await audio.read()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413, "clip too large (8 MB max)")
        try:
            tr = await asyncio.to_thread(svc.speech.transcribe, data)
        except SttUnavailable as error:
            raise HTTPException(503, str(error))
        except ValueError as error:
            raise HTTPException(400, str(error))
        svc.events.emit("voice.transcript", tr.text or "(nothing heard)", transcript=tr.as_dict())
        need_wake = settings.require_wake_word or hands_free
        out = await interpret(tr.text, "voice", tr.confidence, need_wake)
        envs = out.pop("envelopes")
        out["transcript"] = tr.as_dict()
        # An engine with no calibrated confidence (pocketsphinx) always asks, unless told not to.
        low = (tr.confidence is None and settings.confirm_uncalibrated) or \
            (tr.confidence is not None and tr.confidence < settings.stt_min_confidence)
        is_stop = any(e.command.action in ("estop", "stop") for e in envs)
        out["needs_confirmation"] = bool(low and envs and not is_stop)
        if out["needs_confirmation"]:
            sure = f"only {tr.confidence:.0%} sure" if tr.confidence is not None else "using a recogniser with no confidence score"
            out["reply"] = f"I heard '{tr.text}', but I'm {sure}. Confirm to run it."
        elif execute and envs and not out["parse"]["errors"]:
            # A stop is never held back for confidence: stopping by mistake is the safe error.
            out["executions"] = await run_envelopes(envs)
            out["reply"] = reply_for(out)
        else:
            out["reply"] = reply_for(out)
        return out

    @app.get("/api/voice/status", dependencies=guard)
    async def voice_status():
        return svc.speech.status()

    @app.post("/api/voice/download", dependencies=guard)
    async def voice_download(body: dict = Body(default={})):
        st = svc.speech.status()
        if not body.get("confirm"):
            return dict(confirm_required=True, model=st["whisper"]["model"], size_mb=st["whisper"]["size_mb"],
                        destination=str(settings.models_dir / "whisper"))
        if svc.speech.download_state.get("state") == "downloading":
            return svc.speech.download_state

        async def job():
            try:
                await asyncio.to_thread(svc.speech.download_whisper)
                svc.events.emit("voice.model", f"whisper '{settings.whisper_model}' downloaded")
            except Exception as error:  # noqa: BLE001
                svc.events.emit("voice.model", f"whisper download failed: {error}", "error")

        asyncio.ensure_future(job())
        return {"state": "downloading"}

    @app.get("/api/tts", dependencies=guard)
    async def tts(text: str):
        try:
            wav = await asyncio.to_thread(speech_out.synthesize, text)
        except RuntimeError as error:
            raise HTTPException(503, str(error))
        return Response(wav, media_type="audio/wav")

    # ── the duck's personality ────────────────────────────────────────────

    SESSION_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

    def session_of(value) -> str:
        sid = str(value or "default")
        if not SESSION_RE.fullmatch(sid):
            raise HTTPException(422, "session must be 1-64 letters, digits, '-' or '_'")
        return sid

    def checked_text(value) -> str:
        text = str(value or "").strip()
        if not text:
            raise HTTPException(422, "empty message")
        if len(text) > settings.chat_max_chars:
            raise HTTPException(413, f"message longer than {settings.chat_max_chars} characters")
        return text

    async def narrate(ids):
        """What the duck says when commands it started have finished — from the real result."""
        for cid in ids:
            ex = next((e for e in svc.executor.history if e.id == cid), None)
            if ex is None:
                continue
            await svc.executor.wait(ex, timeout=300)
            reply = svc.persona.outcome_reply(ex.action, ex.status, ex.message)
            if reply:
                svc.events.emit("chat.reply", reply.text, **reply.as_dict(), command=cid)
            if ex.status not in ("completed", "delivered"):
                return  # the rest of a sequence will not run; one explanation is enough

    async def think(sid: str, rid: str, text: str, ask: str) -> None:
        """One brain turn: stream the words to the page, then the whole reply (which the page
        voices), then — later — what the actions it started actually did."""
        partial = {"text": "", "at": 0.0}

        def on_delta(t: str) -> None:
            partial["text"] = t
            now = time.monotonic()
            if now - partial["at"] >= 0.08:  # at most ~12 updates a second
                partial["at"] = now
                svc.events.broadcast("chat.delta", id=rid, session=sid, text=t)

        async def act(cmds: list) -> list[dict]:
            return await run_envelopes([Envelope(command=c, source="llm", transcript=text) for c in cmds])

        def reply(body: str, mood: str, level: str = "info", **extra) -> None:
            # the babble is from the prose only: no duck noise for a code listing or an equation
            spoken = structure_prose(body)
            svc.events.emit("chat.reply", body, level, id=rid, t=time.time(), text=body, mood=mood,
                            babble=babble(spoken[:240], mood) if mood != "sleepy" and spoken else "",
                            session=sid, question=ask, **extra)

        try:
            ans = await svc.brain.answer(sid, ask, svc.brain_facts(), on_delta, act)
            body = ans.text or ("On it!" if ans.actions else "Hmm… I've got nothing to say to that.")
            reply(body, mood_of(body), brain=dict(model=settings.groq_model, looked_up=ans.looked_up),
                  sources=ans.sources, actions=ans.actions, followups=ans.followups,
                  structured=structure(body, ans.actions))
            ids = [a["id"] for a in ans.actions if a.get("id")]
            if ids:
                asyncio.ensure_future(narrate(ids))
        except asyncio.CancelledError:
            reply((partial["text"] + " …").strip() if partial["text"] else "(stopped)", "sleepy", cancelled=True)
            raise
        except BrainError as error:
            reply(error.message, "sorry", "warn", error=error.kind, retry=text if error.retry else None,
                  structured=structure(error.message, error=error.kind))
        finally:
            if svc.chats.get(sid) is asyncio.current_task():
                del svc.chats[sid]

    def structure_prose(body: str) -> str:
        """A reply's words without code blocks or LaTeX — what the duck's babble is made from."""
        return re.sub(r"\$\$.*?\$\$|\$[^$\n]+\$|```.*?(```|$)", " ", body, flags=re.S).strip()

    async def chat_turn(text: str, source: str, sid: str = "default", regenerate: bool = False) -> dict:
        turn = svc.persona.handle(text)
        # Only a second open question waits; "stop", chips and commands always go through.
        if turn.ask_brain and sid in svc.chats:
            raise HTTPException(409, "still answering your last message — stop it or wait")
        if turn.ask_brain and not svc.brain.limiter.allow():
            raise HTTPException(429, "too many questions in a minute — give me a few seconds")
        if regenerate and turn.ask_brain:
            svc.brain.sessions.drop_last(sid, turn.brain_text or text)
        svc.events.emit("chat.user", text, text=text, source=source, session=sid)
        execs = []
        if turn.commands:
            envs = [Envelope(command=c, source="chat" if source != "voice" else "voice", transcript=text)
                    for c in turn.commands]
            execs = await run_envelopes(envs)
        refused_now = bool(execs) and all(e["status"] == "rejected" for e in execs)
        replies = [] if (turn.ack and refused_now) else turn.replies
        for r in replies:
            svc.events.emit("chat.reply", r.text, **r.as_dict())
        if execs and not turn.side:
            asyncio.ensure_future(narrate([e["id"] for e in execs]))
        pending = None
        if turn.ask_brain:
            rid = f"b{int(time.time() * 1000)}-{len(svc.chats)}"
            svc.events.broadcast("chat.thinking", id=rid, session=sid)
            svc.chats[sid] = asyncio.create_task(think(sid, rid, text, turn.brain_text or text))
            pending = dict(id=rid)
        return dict(replies=[r.as_dict() for r in replies], executions=execs, learned=turn.learned,
                    parse=turn.parse, memory=svc.memory.as_dict(), pending=pending)

    @app.post("/api/chat", dependencies=guard)
    async def chat(body: dict = Body(...)):
        return await chat_turn(checked_text(body.get("text")), str(body.get("source", "chat"))[:16],
                               session_of(body.get("session")), regenerate=body.get("regenerate") is True)

    @app.post("/api/chat/feedback", dependencies=guard)
    async def chat_feedback(body: dict = Body(...)):
        """Thumbs up/down on a reply: recorded in the event log (and so in the service log), nowhere else."""
        rid, rating = str(body.get("id", ""))[:64], body.get("rating")
        if not rid or rating not in ("up", "down", None):
            raise HTTPException(422, "id and rating ('up', 'down' or null) are required")
        svc.events.emit("chat.feedback", f"reply {rid}: {rating or 'cleared'}", id=rid, rating=rating,
                        session=session_of(body.get("session")))
        return dict(recorded=True)

    @app.post("/api/chat/cancel", dependencies=guard)
    async def chat_cancel(body: dict = Body(default={})):
        """Stop the answer being written for this session (the words so far are kept)."""
        task = svc.chats.get(session_of(body.get("session")))
        if task:
            task.cancel()
        return dict(cancelled=bool(task))

    @app.post("/api/chat/clear", dependencies=guard)
    async def chat_clear(body: dict = Body(default={})):
        """Forget this session's conversation: the brain starts the next question fresh."""
        sid = session_of(body.get("session"))
        task = svc.chats.get(sid)
        if task:
            task.cancel()
        svc.brain.sessions.clear(sid)
        return dict(cleared=True)

    @app.post("/api/chat/idle", dependencies=guard)
    async def chat_idle(body: dict = Body(default={})):
        """The duck speaks without being asked (the page calls this after a quiet spell). Talk
        only: nothing here reaches the executor."""
        reply = svc.persona.idle(body if isinstance(body, dict) else {})
        svc.events.emit("chat.reply", reply.text, **reply.as_dict())
        return dict(replies=[reply.as_dict()])

    @app.post("/api/chat/voice", dependencies=guard)
    async def chat_voice(audio: UploadFile = File(...), session: str = Form("default")):
        data = await audio.read()
        try:
            tr = await asyncio.to_thread(svc.speech.transcribe, data)
        except SttUnavailable as error:
            raise HTTPException(503, str(error))
        except ValueError as error:
            raise HTTPException(400, str(error))
        svc.events.emit("voice.transcript", tr.text or "(nothing heard)", transcript=tr.as_dict())
        if not tr.text.strip():
            return dict(transcript=tr.as_dict(), replies=[], executions=[])
        low = (tr.confidence is None and settings.confirm_uncalibrated) or \
            (tr.confidence is not None and tr.confidence < settings.stt_min_confidence)
        p = parse(tr.text)
        stopping = any(c.action in ("stop", "estop") for c in p.commands)
        if low and not stopping:
            return dict(transcript=tr.as_dict(), needs_confirmation=True, replies=[], executions=[])
        out = await chat_turn(tr.text.strip()[:settings.chat_max_chars], "voice", session_of(session))
        out["transcript"] = tr.as_dict()
        return out

    @app.get("/api/duck", dependencies=guard)
    async def duck_get():
        return svc.memory.as_dict()

    @app.post("/api/duck", dependencies=guard)
    async def duck_set(body: dict = Body(...)):
        if "duck_name" in body:
            svc.memory.duck_name = str(body["duck_name"])[:30] or "Microduck"
        if isinstance(body.get("look"), dict):
            svc.memory.look = {str(k)[:12]: str(v)[:9] for k, v in list(body["look"].items())[:12]
                               if re.fullmatch(r"#[0-9a-fA-F]{6}", str(v))}
        if "forget" in body:
            svc.memory.aliases.pop(str(body["forget"]), None)
        svc.memory.save()
        return svc.memory.as_dict()

    # ── experiments ───────────────────────────────────────────────────────

    @app.get("/api/experiments", dependencies=guard)
    async def experiments():
        defs = svc.runner.definitions()
        return dict(definitions=[d.model_dump() for d in defs.values()], current=svc.runner.current,
                    last=svc.runner.last_experiment)

    @app.post("/api/experiments/{name}/run", dependencies=guard)
    async def experiment_run(name: str, body: dict = Body(default={})):
        env = Envelope.model_validate({"command": {"action": "run_experiment", "experiment": name,
                                                   "trials": int(body.get("trials", 1))}, "source": "ui"})
        return (await svc.executor.submit(env)).as_dict()

    @app.get("/api/runs", dependencies=guard)
    async def runs():
        return svc.store.list()

    @app.get("/api/runs/{run_id}", dependencies=guard)
    async def run_get(run_id: str, samples: bool = False):
        try:
            r = svc.store.get(run_id)
        except (KeyError, OSError):
            raise HTTPException(404, "no such run")
        if not samples:
            for t in r.get("results", []):
                t["samples"] = len(t.get("samples", []))
        return r

    @app.get("/api/runs/{run_id}/export.json", dependencies=guard)
    async def run_json(run_id: str):
        try:
            path = svc.store.dir / f"{svc.store.get(run_id)['id']}.json"
        except (KeyError, OSError):
            raise HTTPException(404, "no such run")
        return FileResponse(path, media_type="application/json", filename=path.name)

    @app.get("/api/runs/{run_id}/export.csv", dependencies=guard)
    async def run_csv(run_id: str):
        try:
            text = svc.store.csv(run_id)
        except (KeyError, OSError):
            raise HTTPException(404, "no such run")
        return PlainTextResponse(text, media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'})

    @app.get("/api/compare", dependencies=guard)
    async def compare(a: str, b: str):
        try:
            return svc.runner.compare(svc.store.get(a), svc.store.get(b))
        except (KeyError, OSError):
            raise HTTPException(404, "no such run")

    # ── navigation, telemetry, logs ───────────────────────────────────────

    @app.get("/api/nav/plan", dependencies=guard)
    async def nav_plan():
        return svc.executor.last_plan or {}

    @app.post("/api/nav/preview", dependencies=guard)
    async def nav_preview(body: dict = Body(...)):
        from .navigation import PlanError, plan

        try:
            name, goal = svc.executor.resolve_target(str(body.get("target", "")))
        except Exception as error:  # noqa: BLE001
            raise HTTPException(400, str(error))
        pose, src = svc.executor.pose()
        if pose is None:
            raise HTTPException(503, "no pose available")
        grid = await svc.executor._grid()
        try:
            path = plan(grid, (pose[0], pose[1]), goal)
        except PlanError as error:
            raise HTTPException(409, str(error))
        return dict(goal=name, path=[(pose[0], pose[1])] + path, grid=grid.to_json(), pose_source=src)

    @app.get("/api/telemetry/history", dependencies=guard)
    async def history(seconds: float = 30):
        return svc.hub.recent(min(seconds, 120))

    @app.get("/api/logs", dependencies=guard)
    async def logs(limit: int = 300, level: str | None = None, kind: str | None = None):
        return svc.events.recent(min(limit, 2000), kind=kind, level=level)

    # ── live stream ───────────────────────────────────────────────────────

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        if settings.token and socket.query_params.get("token") != settings.token:
            await socket.close(code=4401)
            return
        await socket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        def on_event(e):
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(e)

        unsubscribe = svc.events.subscribe(on_event)
        await socket.send_text(json.dumps(dict(type="hello", status=svc.status(),
                                               events=svc.events.recent(80), commands=[e.as_dict() for e in
                                                                                       list(svc.executor.history)[-30:]]),
                                          default=str))

        async def sender():
            last_sim = last_robot = -1
            last_status = 0.0
            while True:
                await asyncio.sleep(1 / 30)
                msgs = []
                while not queue.empty():
                    msgs.append(dict(type="event", event=queue.get_nowait()))
                frame = svc.xbody.last_frame
                if frame and frame.get("seq") != last_sim:
                    last_sim = frame.get("seq")
                    msgs.append(dict(type="sim", frame=frame))
                if svc.robot.states_received != last_robot and svc.robot.last_state:
                    last_robot = svc.robot.states_received
                    msgs.append(dict(type="robot", state=svc.robot.last_state))
                if time.monotonic() - last_status > 0.5:
                    last_status = time.monotonic()
                    msgs.append(dict(type="status", status=svc.status()))
                for m in msgs:
                    await socket.send_text(json.dumps(m, default=str))

        async def receiver():
            while True:
                msg = json.loads(await socket.receive_text())
                kind = msg.get("type")
                if kind == "ping":
                    await socket.send_text(json.dumps(dict(type="pong", t=msg.get("t"), server=time.time())))
                elif kind == "teleop":
                    res = await svc.executor.teleop(float(msg.get("vx", 0)), float(msg.get("vy", 0)),
                                                    float(msg.get("vyaw", 0)))
                    if not res.get("ok"):
                        await socket.send_text(json.dumps(dict(type="teleop_refused", blockers=res["blockers"])))
                elif kind == "talk":
                    # the page is playing the duck's voice (keepalive ~1 s): body language only
                    svc.executor.talk(bool(msg.get("on")), msg.get("viewer"))
                elif kind == "estop":
                    await svc.executor.estop("dashboard emergency stop (websocket)")
                elif kind == "command":
                    try:
                        env = Envelope.model_validate({"command": msg.get("command"),
                                                       "source": msg.get("source", "ui")})
                        await svc.executor.submit(env)
                    except ValidationError as error:
                        await socket.send_text(json.dumps(dict(type="error", message=str(error.errors()[0]["msg"]))))

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for t in tasks:
                t.cancel()
            unsubscribe()
            # A dashboard that disappears mid-teleop must not leave the robot walking.
            if svc.executor._teleop_active:
                await svc.executor.teleop(0, 0, 0)

    # ── the dashboard itself ──────────────────────────────────────────────

    dist: Path = settings.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            target = dist / path
            if path and target.is_file() and dist in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    else:
        @app.get("/")
        async def no_frontend():
            return PlainTextResponse("Microduck X backend is running. The dashboard is not built: "
                                     "cd microduck-x/frontend && npm install && npm run build")

    return app
