"""Turning validated commands into robotd calls, sim operations and Microduck X tasks.

One *activity* at a time moves the robot (walk, turn, skill, goto, waypoints, experiment). A new
activity supersedes the running one — it is cancelled, the twist is zeroed, and its record says
`superseded` — so two movement commands can never interleave their intents.

Statuses mean exactly this:

* ``completed`` — the action ran and its *observed* result is in `outcome` (for movement:
  measured displacement from simulator ground truth and robotd's own odometry).
* ``failed``    — it ran, or tried to, and the result was not what was asked (reason given).
* ``rejected``  — refused before anything was sent (safety gate, unknown skill, bad target).
* ``cancelled`` / ``superseded`` — stopped by a stop, an e-stop or a newer activity.
* ``delivered`` — UI-only commands (camera, panels), handed to the dashboard.

A command is never reported `completed` because robotd accepted it; for continuous intents the
controller has to *reflect* the intent in `robot.state.move.requested`, and a policy has to be
driving.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

from . import commands as C
from .commands import Envelope, interface_of
from .config import Settings
from .events import EventBus
from .experiments import ExperimentRunner, ExperimentStore
from .navigation import Grid, PlanError, heading_command, obstacles_from_model, plan, wrap
from .robotd import RobotdClient, RobotdUnavailable, RpcError
from .safety import SafetyMonitor, SafetyRefusal
from .supervisor import SimSupervisor
from .telemetry import TelemetryHub
from .xbody import SimUnavailable, XbodyClient

TERMINAL = {"completed", "failed", "rejected", "cancelled", "superseded", "delivered"}


def gait_floor(vx: float, vy: float, vyaw: float, min_fwd: float, min_back: float, min_yaw: float):
    """A twist the gait will actually step for (see `Settings.gait_min_vx_fwd`).

    If no component reaches its floor the policy would stand still, so the whole twist is scaled
    until its dominant component does — the direction and the arc's curvature are kept. A twist
    that already steps is returned unchanged, so small heading corrections on a walk stay small.
    Pure sideways steps (vy alone) are left as they are: their floor has not been measured."""
    floor_x = min_fwd if vx >= 0 else min_back
    rx = abs(vx) / floor_x if floor_x > 0 else 0.0
    ryaw = abs(vyaw) / min_yaw if min_yaw > 0 else 0.0
    r = max(rx, ryaw)
    if r == 0.0 or r >= 1.0:
        return vx, vy, vyaw
    return vx / r, vy / r, vyaw / r


@dataclass
class Execution:
    id: str
    action: str
    command: dict
    source: str
    interface: str
    transcript: str | None = None
    confidence: float | None = None
    status: str = "queued"
    message: str = ""
    outcome: dict = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    started: float | None = None
    ended: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class Rejected(Exception):
    pass


class Failed(Exception):
    def __init__(self, message: str, outcome: dict | None = None):
        super().__init__(message)
        self.outcome = outcome or {}


class Executor:
    def __init__(self, settings: Settings, robot: RobotdClient, xbody: XbodyClient, hub: TelemetryHub,
                 safety: SafetyMonitor, events: EventBus, supervisor: SimSupervisor | None,
                 runner: ExperimentRunner, store: ExperimentStore):
        self.s = settings
        self.robot = robot
        self.xbody = xbody
        self.hub = hub
        self.safety = safety
        self.events = events
        self.supervisor = supervisor
        self.runner = runner
        self.store = store
        self.history: deque[Execution] = deque(maxlen=300)
        self._activity: tuple[Execution, asyncio.Task] | None = None
        self._teleop_at = 0.0
        self._teleop_active = False
        self.obstacle_contacts = 0
        self._touching: set[tuple[int, int]] = set()
        self._geom_names: dict[int, str] = {}
        self._geom_body_duck: dict[int, int | None] = {}
        self.last_plan: dict | None = None
        self._grid_cache: tuple[str, Grid] | None = None
        self._sequence: asyncio.Task | None = None
        self._cancel_why: dict[str, tuple[str, str]] = {}
        self._slot_lock = asyncio.Lock()
        self._gen = 0
        # The talking head (talk_loop): until when the page says the duck is audibly talking, and
        # where the person watching is (world frame, the 3D view's camera), to look towards.
        self._talk_until = 0.0
        self._talk_viewer: tuple[float, float, float] | None = None
        self._talk_head_on = False

    # ── bookkeeping ───────────────────────────────────────────────────────

    def _new(self, env: Envelope) -> Execution:
        cmd = env.command
        ex = Execution(id=env.id, action=cmd.action, command=cmd.model_dump(), source=env.source,
                       interface=interface_of(cmd), transcript=env.transcript, confidence=env.confidence)
        self.history.append(ex)
        return ex

    def _finish(self, ex: Execution, status: str, message: str, outcome: dict | None = None) -> Execution:
        ex.status, ex.message, ex.ended = status, message, time.time()
        if outcome:
            ex.outcome.update(outcome)
        level = "info" if status in ("completed", "delivered") else "warn"
        self.events.emit(f"cmd.{status}", f"{ex.action}: {message}", level, id=ex.id, action=ex.action,
                         source=ex.source, outcome=ex.outcome, command=ex.command)
        return ex

    def current(self) -> dict | None:
        return self._activity[0].as_dict() if self._activity else None

    def on_frame(self, frame: dict) -> None:
        """Count new robot↔scenery contacts (not the floor) — 'did it touch an obstacle?'."""
        if not self._geom_names:
            return
        now = set()
        for c in frame.get("contacts", []):
            g1, g2 = c[3], c[4]
            d1, d2 = self._geom_body_duck.get(g1), self._geom_body_duck.get(g2)
            if (d1 is None) == (d2 is None):
                continue
            other = g2 if d1 is not None else g1
            name = self._geom_names.get(other, "")
            if name == "floor" or name.startswith("decal_") or name.startswith("floor"):
                continue
            now.add((min(g1, g2), max(g1, g2)))
        self.obstacle_contacts += len(now - self._touching)
        self._touching = now

    async def load_model_index(self) -> dict | None:
        try:
            header, mesh, _ = await self.xbody.model()
        except Exception:  # noqa: BLE001
            return None
        bodies = {b["id"]: b for b in header["bodies"]}
        self._geom_names = {g["id"]: g["name"] for g in header["geoms"]}
        self._geom_body_duck = {g["id"]: bodies[g["body"]]["duck"] for g in header["geoms"]}
        return header

    # ── entry points ──────────────────────────────────────────────────────

    # Activities hold the one slot that moves the robot. Anything that can take seconds runs as
    # one too, so an emergency stop can cancel it.
    ACTIVITIES = ("walk", "turn", "hold", "emote", "neutral", "dance", "skill", "goto", "waypoints", "run_experiment",
                  "enable", "reset", "set_environment", "push")
    GATED = ("walk", "turn", "hold", "emote", "neutral", "dance", "skill", "goto", "waypoints")
    # Activities that own the head while they run; the talking head (talk_loop) waits for them.
    HEAD_OWNERS = ("emote", "neutral", "dance")

    async def submit(self, env: Envelope, ex: Execution | None = None) -> Execution:
        """Run a command. Activities are started and returned while running; the rest complete first.

        Starting an activity is atomic with respect to stops: `_slot_lock` covers cancel-old →
        start-new, and `_gen` (bumped by every stop, e-stop and cancel) is checked again after
        the old activity is gone, so a stop that lands in between wins.
        """
        ex = ex or self._new(env)
        cmd = env.command
        self.events.emit("cmd.accepted", f"{cmd.action} from {env.source}", id=ex.id, command=ex.command,
                         source=env.source, transcript=env.transcript)
        if cmd.action not in self.ACTIVITIES:
            await self._run(ex, cmd)
            return ex
        gen = self._gen
        # Refuse before superseding anything: a refused command must not stop a running one.
        refusal = self._refusal(cmd)
        if refusal:
            return self._finish(ex, "rejected", refusal)
        async with self._slot_lock:
            if gen != self._gen:
                return self._finish(ex, "cancelled", "a stop arrived before it started")
            self._teleop_active = False
            await self._cancel_activity("superseded", f"superseded by {cmd.action} ({ex.id})")
            refusal = self._refusal(cmd)
            if gen != self._gen or refusal:
                return self._finish(ex, "cancelled" if gen != self._gen else "rejected",
                                    "a stop arrived before it started" if gen != self._gen else refusal)
            task = asyncio.create_task(self._run(ex, cmd))
            self._activity = (ex, task)
            task.add_done_callback(lambda _t, ex=ex: self._clear_activity(ex))
        await asyncio.sleep(0)  # let it start
        return ex

    def _refusal(self, cmd) -> str | None:
        if cmd.action in self.GATED:
            blockers = self.safety.motion_blockers()
            return "; ".join(blockers) if blockers else None
        if self.safety.estop and cmd.action in ("enable", "run_experiment", "reset", "set_environment", "push"):
            return "emergency stop is latched — clear it first"
        return None

    async def wait(self, ex: Execution, timeout: float = 600) -> Execution:
        deadline = time.monotonic() + timeout
        while ex.status not in TERMINAL and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return ex

    def submit_sequence(self, envelopes: list[Envelope]) -> list[Execution]:
        """Run several commands one after another ("turn left, then walk forward").

        Every record exists from the start (status `queued`). Each step waits for the previous to
        finish; the first that does not complete, or a stop, ends the sequence, and every step
        that never ran is recorded as cancelled.
        """
        if len(envelopes) == 1:
            raise ValueError("use submit for a single command")
        if self._sequence and not self._sequence.done():
            self._sequence.cancel()
        records = [self._new(e) for e in envelopes]

        async def runner():
            reason = "sequence cancelled"
            try:
                for env, ex in zip(envelopes, records):
                    await self.submit(env, ex)
                    await self.wait(ex)
                    if ex.status not in ("completed", "delivered"):
                        reason = f"not run: '{ex.action}' ended {ex.status}"
                        return
            finally:
                for ex in records:
                    if ex.status == "queued":
                        self._finish(ex, "cancelled", reason)

        self._sequence = asyncio.create_task(runner())
        return records

    async def cancel_all(self, why: str) -> None:
        self._gen += 1
        self._teleop_active = False
        if self._sequence and not self._sequence.done() and self._sequence is not asyncio.current_task():
            self._sequence.cancel()
        await self._cancel_activity("cancelled", why)

    async def execute_inline(self, env: Envelope) -> Execution:
        """Run a command to completion inside the current activity (used by experiments)."""
        ex = self._new(env)
        await self._run(ex, env.command)
        return ex

    def _clear_activity(self, ex: Execution) -> None:
        if self._activity and self._activity[0] is ex:
            self._activity = None
            self.safety.current_command = None

    async def _cancel_activity(self, status: str, why: str) -> None:
        if not self._activity:
            return
        ex, task = self._activity
        if task is asyncio.current_task():
            return  # a step inside the activity (an experiment's reset) must not cancel the activity
        self._activity = None
        self._cancel_why[ex.id] = (status, why)
        if not task.done():
            task.cancel()
            # asyncio.wait, not `await task`: it does not swallow a cancel aimed at *this* task.
            await asyncio.wait({task})
        if ex.status not in TERMINAL:
            self._finish(ex, status, why)

    async def _run(self, ex: Execution, cmd) -> None:
        ex.status, ex.started = "running", time.time()
        self.events.emit("cmd.started", f"{cmd.action}", id=ex.id, action=cmd.action)
        handler = getattr(self, f"_do_{cmd.action}")
        try:
            message, outcome = await handler(cmd, ex)
            status = "delivered" if interface_of(cmd) == "ui" else "completed"
            self._finish(ex, status, message, outcome)
        except asyncio.CancelledError:
            if ex.status not in TERMINAL:
                status, why = self._cancel_why.pop(ex.id, ("cancelled", "cancelled"))
                self._finish(ex, status, why)
            await self._halt_quietly()
            raise
        except SafetyRefusal as refusal:
            await self._halt_quietly()
            self._finish(ex, "rejected" if ex.outcome.get("sent") is None else "failed", "; ".join(refusal.reasons))
        except Rejected as error:
            self._finish(ex, "rejected", str(error))
        except Failed as error:
            await self._halt_quietly()
            self._finish(ex, "failed", str(error), error.outcome)
        except (RobotdUnavailable, SimUnavailable) as error:
            self._finish(ex, "failed", str(error))
        except RpcError as error:
            self._finish(ex, "failed", f"robotd refused: {error.message}")
        except Exception as error:  # noqa: BLE001 — never leave a command hanging in 'running'
            await self._halt_quietly()
            self._finish(ex, "failed", f"internal error: {error!r}")

    # ── teleop (keyboard / on-screen pad) ─────────────────────────────────

    def _gait(self, vx: float, vy: float, vyaw: float) -> tuple[float, float, float]:
        """Every twist robotd receives passes here: raised to what the gait steps for, then clamped."""
        s = self.s
        vx, vy, vyaw = gait_floor(vx, vy, vyaw, s.gait_min_vx_fwd, s.gait_min_vx_back, s.gait_min_vyaw)
        return (max(-s.vx_max, min(s.vx_max, vx)), max(-s.vy_max, min(s.vy_max, vy)),
                max(-s.vyaw_max, min(s.vyaw_max, vyaw)))

    async def teleop(self, vx: float, vy: float, vyaw: float) -> dict:
        if not all(math.isfinite(v) for v in (vx, vy, vyaw)):
            return {"ok": False, "blockers": ["non-finite teleop values"]}
        vx, vy, vyaw = self._gait(vx, vy, vyaw)
        if vx == vy == vyaw == 0:
            if self._teleop_active:
                self._teleop_active = False
                await self._halt_quietly()
            return {"ok": True, "stopped": True}
        blockers = self.safety.motion_blockers()
        if blockers:
            return {"ok": False, "blockers": blockers}
        gen = self._gen
        async with self._slot_lock:
            if self._activity:
                await self._cancel_activity("superseded", "superseded by keyboard teleop")
            # Re-check after the await: an e-stop may have landed while the activity was stopping.
            blockers = self.safety.motion_blockers()
            if blockers or gen != self._gen:
                return {"ok": False, "blockers": blockers or ["a stop arrived"]}
            self._teleop_active, self._teleop_at = True, time.monotonic()
            await self.robot.move(vx, vy, vyaw)
        return {"ok": True}

    async def teleop_watchdog(self) -> None:
        """Client-side deadman on top of robotd's own: no teleop message for 350 ms → stop."""
        while True:
            await asyncio.sleep(0.1)
            if self._teleop_active and time.monotonic() - self._teleop_at > 0.35:
                self._teleop_active = False
                await self._halt_quietly()
                self.events.emit("teleop.timeout", "keyboard teleop went quiet; stopped", "warn")

    # ── the talking head ───────────────────────────────────────────────────
    #
    # While the page plays the duck's voice it says so (`talk`, a keepalive every second); the head
    # then looks towards the person watching and moves a little with the words — a small re-aim,
    # a curious tilt, a short nod — every second or so, never in lockstep. It is body language on
    # robotd's head intent only: it does not take the activity slot, so a walk goes on, and it
    # stands aside whenever an emote, dance or reset holds the head. Every move is stated from the
    # gaze robotd's IK answers plus a small bounded offset (nothing accumulates), and when the
    # talking stops — or the keepalives do — the head goes back to NEUTRAL_HEAD.

    TALK_KEEPALIVE_S = 2.5

    def talk(self, on: bool, viewer: list | None = None) -> None:
        self._talk_until = time.monotonic() + self.TALK_KEEPALIVE_S if on else 0.0
        if viewer and len(viewer) == 3 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in viewer):
            self._talk_viewer = (float(viewer[0]), float(viewer[1]), float(viewer[2]))

    def _head_owned(self) -> bool:
        return bool(self._activity) and self._activity[0].action in self.HEAD_OWNERS

    def _gaze_point(self) -> tuple[float, float, float]:
        """The viewer, in the trunk frame (what robot.look takes), kept to a natural cone in front."""
        frame = self.xbody.last_frame or {}
        duck = (frame.get("ducks") or [None])[0]
        if not duck or not self._talk_viewer:
            return self.GAZE["ahead"]
        (tx, ty, tz), yaw = duck["trunk"], duck["yaw"]
        dx, dy = self._talk_viewer[0] - tx, self._talk_viewer[1] - ty
        x, y = math.cos(yaw) * dx + math.sin(yaw) * dy, -math.sin(yaw) * dx + math.cos(yaw) * dy
        bearing = max(-1.0, min(1.0, math.atan2(y, x)))  # ±57°: further round, it turns its head as far as is natural
        elev = max(-0.25, min(0.45, math.atan2(self._talk_viewer[2] - tz - 0.13, math.hypot(x, y))))
        r = 0.6
        return (r * math.cos(bearing), r * math.sin(bearing), 0.13 + r * math.tan(elev))

    async def talk_loop(self) -> None:
        rng = random.Random()
        next_beat = 0.0
        while True:
            await asyncio.sleep(0.05)
            now = time.monotonic()
            talking = now < self._talk_until and not self.safety.motion_blockers()
            try:
                if not talking:
                    if self._talk_head_on:
                        self._talk_head_on = False
                        if not self._head_owned():
                            await self._neutral_head()
                    continue
                if self._head_owned() or now < next_beat:
                    continue
                legs_busy = self._teleop_active or (bool(self._activity) and self._activity[0].action in
                                                    ("walk", "turn", "goto", "waypoints"))
                x, y, z = self._gaze_point()
                y += rng.uniform(-0.05, 0.05)
                z += rng.uniform(-0.03, 0.04)
                answer = await self.robot.call("robot.look", {"x": x, "y": y, "z": z}, timeout=1.0)
                h = {k: float((answer.get("head") or {}).get(k, 0.0)) for k in self.NEUTRAL_HEAD}
                self._talk_head_on = True
                move = "gaze" if legs_busy else rng.choices(("gaze", "tilt", "nod"), (5, 3, 2))[0]
                if move == "tilt":
                    await self.robot.notify("robot.head", {**h, "head_roll": h["head_roll"] + rng.choice((-1, 1)) * rng.uniform(0.07, 0.13)})
                elif move == "nod":
                    await self.robot.notify("robot.head", {**h, "head_pitch": h["head_pitch"] + 0.12})
                    await asyncio.sleep(0.22)
                    await self.robot.notify("robot.head", h)
                next_beat = time.monotonic() + rng.uniform(0.7, 1.4)
            except Exception:  # noqa: BLE001 — robotd away: the next beat tries again
                next_beat = time.monotonic() + 1.0

    # ── emergency stop (also reachable without any parsing: POST /api/estop) ──

    async def estop(self, reason: str) -> dict:
        self.safety.latch(reason)
        self._teleop_active = False
        await self.cancel_all(f"emergency stop: {reason}")
        result: dict[str, Any] = {"latched": True}
        try:
            await asyncio.wait_for(self.robot.move(0, 0, 0), 0.5)
            answer = await self.robot.call("robot.stop", timeout=1.0)
            result["robot_stop"] = answer
        except Exception as error:  # noqa: BLE001
            result["robot_stop"] = None
            result["note"] = (f"robotd unreachable ({error}); no intents are being sent, so robotd's own "
                              "500 ms deadman zeroes the twist regardless")
        return result

    async def _halt_quietly(self) -> None:
        try:
            await asyncio.wait_for(self.robot.move(0, 0, 0), 0.5)
            await self.robot.call("robot.stop", timeout=1.0)
        except Exception:  # noqa: BLE001 — the deadman covers a dead connection
            pass

    # ── helpers ───────────────────────────────────────────────────────────

    def pose(self) -> tuple[tuple[float, float, float] | None, str]:
        p = self.hub.pose()
        if p is not None and self.hub.sim_age() < self.s.stale_sim_s:
            return p, "simulator ground truth"
        r = self.hub.robot
        if r and r.get("odom"):
            return (r["odom"]["position"][0], r["odom"]["position"][1], r["odom"]["yaw"]), "robotd odometry"
        return None, "unavailable"

    def markers(self) -> dict[str, tuple[float, float]]:
        header = self.xbody._model[0] if self.xbody._model else None
        if not header:
            return {}
        return {m["name"]: (m["x"], m["y"]) for m in header["env"]["markers"]}

    def _measure_start(self) -> dict:
        pose, src = self.pose()
        r = self.hub.robot or {}
        return dict(pose=pose, source=src, odom=r.get("odom"), t=time.time())

    def _measure_end(self, start: dict) -> dict:
        pose, src = self.pose()
        out: dict[str, Any] = dict(pose_source=src, seconds=round(time.time() - start["t"], 2))
        if pose and start["pose"]:
            x0, y0, yaw0 = start["pose"]
            dx, dy = pose[0] - x0, pose[1] - y0
            c, s = math.cos(yaw0), math.sin(yaw0)
            out.update(forward_m=round(c * dx + s * dy, 3), lateral_m=round(-s * dx + c * dy, 3),
                       yaw_change_deg=round(math.degrees(wrap(pose[2] - yaw0)), 1),
                       end=dict(x=round(pose[0], 3), y=round(pose[1], 3), yaw=round(pose[2], 3)))
        r = self.hub.robot or {}
        if start.get("odom") and r.get("odom"):
            a, b = start["odom"]["position"], r["odom"]["position"]
            out["robot_odom_distance_m"] = round(math.hypot(b[0] - a[0], b[1] - a[1]), 3)
        up = self.hub.upright()
        fresh = self.hub.sim_age() < self.s.stale_sim_s or self.hub.robot_age() < self.s.stale_robot_s
        out["upright_at_end"] = (not self.safety.fallen()[0]) if fresh else None  # None = unknown
        out["tilt_deg"] = None if up["ground_truth_tilt_deg"] is None else round(up["ground_truth_tilt_deg"], 1)
        return out

    async def _drive_tick(self, vx: float, vy: float, vyaw: float, started: float, ex: Execution) -> None:
        self.safety.check_motion()
        vx, vy, vyaw = self._gait(vx, vy, vyaw)
        await self.robot.move(vx, vy, vyaw)
        ex.outcome["sent"] = [vx, vy, vyaw]
        recent = ex.outcome.setdefault("_recent", [])
        recent.append((vx, vy, vyaw))
        del recent[:-15]
        if not ex.outcome.get("acknowledged"):
            if self._acknowledged(ex):
                return
            if time.monotonic() - started > 1.2:
                req = ((self.hub.robot or {}).get("move") or {}).get("requested")
                raise Failed(f"robotd did not reflect the intent in robot.state (requested={req})")

    def _acknowledged(self, ex: Execution) -> bool:
        """Has robot.state.move.requested shown one of the last few twists we sent?"""
        r = self.hub.robot or {}
        req = (r.get("move") or {}).get("requested")
        if req and any(all(abs(a - b) < 1e-3 for a, b in zip(req, sent)) for sent in ex.outcome.get("_recent", [])):
            ex.outcome["acknowledged"] = True
            ex.outcome["policy"] = r.get("policy")
            return True
        return False

    async def _require_ack(self, ex: Execution) -> None:
        """Before reporting a motion done: the controller must have reflected it at least once."""
        deadline = time.monotonic() + 0.6
        while not ex.outcome.get("acknowledged") and time.monotonic() < deadline:
            if self._acknowledged(ex):
                break
            await asyncio.sleep(0.04)
        ex.outcome.pop("_recent", None)
        if not ex.outcome.get("acknowledged"):
            raise Failed("robotd never reflected the intent in robot.state; not reporting it as done")

    async def _stop_and_settle(self) -> None:
        await self.robot.move(0, 0, 0)
        answer = await self.robot.call("robot.stop", timeout=2.0)
        if not answer or not answer.get("accepted", False):
            raise Failed(f"robot.stop was not accepted: {answer}")

    # ── robotd actions ────────────────────────────────────────────────────

    async def _do_walk(self, cmd: C.Walk, ex: Execution):
        self.safety.current_command = ex.command
        start = self._measure_start()
        t0 = time.monotonic()
        period = 1.0 / self.s.intent_hz
        while time.monotonic() - t0 < cmd.duration_s:
            await self._drive_tick(cmd.vx, cmd.vy, cmd.vyaw, t0, ex)
            await asyncio.sleep(period)
        await self._require_ack(ex)
        await self._stop_and_settle()
        out = self._measure_end(start)
        if out["upright_at_end"] is False:
            raise Failed("the robot fell during the walk", out)
        vx, vy, vyaw = ex.outcome.get("sent", (cmd.vx, cmd.vy, cmd.vyaw))
        msg = f"walked {cmd.duration_s:g} s at vx={vx:.2g} vy={vy:.2g} vyaw={vyaw:.2g}"
        if "forward_m" in out:
            msg += f"; moved {out['forward_m']:+.2f} m forward, {out['lateral_m']:+.2f} m lateral, yaw {out['yaw_change_deg']:+.0f}°"
        return msg, out

    async def _do_turn(self, cmd: C.Turn, ex: Execution):
        self.safety.current_command = ex.command
        start = self._measure_start()
        t0 = time.monotonic()
        period = 1.0 / self.s.intent_hz
        if cmd.angle_deg is None:
            while time.monotonic() - t0 < cmd.duration_s:
                await self._drive_tick(0, 0, cmd.vyaw, t0, ex)
                await asyncio.sleep(period)
            await self._require_ack(ex)
            await self._stop_and_settle()
            out = self._measure_end(start)
            return f"turned for {cmd.duration_s:g} s at {cmd.vyaw:+.2f} rad/s; yaw {out.get('yaw_change_deg', '?')}°", out
        # Closed loop on measured yaw — a Microduck X extension; robotd only knows rates.
        target = math.radians(cmd.angle_deg)
        if abs(cmd.angle_deg) < 5:
            raise Rejected("turns smaller than 5° are below what the yaw loop resolves")
        pose, src = self.pose()
        if pose is None:
            raise Rejected("turn-by-angle needs a yaw measurement and none is available")
        last_yaw, turned = pose[2], 0.0
        timeout = abs(target) / abs(cmd.vyaw) * 2.5 + 3.0
        while True:
            pose, _ = self.pose()
            turned += wrap(pose[2] - last_yaw)
            last_yaw = pose[2]
            remaining = target - turned
            if abs(remaining) < math.radians(4):
                break
            if time.monotonic() - t0 > timeout:
                await self._stop_and_settle()
                raise Failed(f"turned {math.degrees(turned):.0f}° of {cmd.angle_deg:g}° before the {timeout:.0f} s timeout",
                             self._measure_end(start))
            speed = min(abs(cmd.vyaw), max(0.3, abs(remaining) * 1.5))
            await self._drive_tick(0, 0, math.copysign(speed, remaining), t0, ex)
            await asyncio.sleep(period)
        await self._require_ack(ex)
        await self._stop_and_settle()
        out = self._measure_end(start)
        out.update(target_deg=cmd.angle_deg, turned_deg=round(math.degrees(turned), 1), yaw_source=src)
        return f"turned {out['turned_deg']:+.0f}° (asked {cmd.angle_deg:+.0f}°, measured from {src})", out

    async def _do_hold(self, cmd: C.Hold, ex):
        start = self._measure_start()
        t0 = time.monotonic()
        await self.robot.move(0, 0, 0)
        while time.monotonic() - t0 < cmd.duration_s:
            fallen, why = self.safety.fallen()
            if fallen:
                raise Failed(f"fell while holding: {why}", self._measure_end(start))
            await asyncio.sleep(0.1)
        out = self._measure_end(start)
        return f"held still for {cmd.duration_s:g} s" + (
            f"; drift {out['forward_m']:+.3f} m / {out['lateral_m']:+.3f} m" if "forward_m" in out else ""), out

    # ── body language (robot.look / robot.head / robot.pose) ───────────────
    #
    # Directions come from robotd's own head IK: `robot.look` takes a point in the trunk frame and
    # answers the joint targets it set, so "look left" is left by the robot's kinematics, not by
    # a sign convention guessed here.
    #
    # The head intent does not expire in robotd, so every gesture ends by sending the head back to
    # NEUTRAL_HEAD — the policy's nominal posture, all four offsets zero — and never to a value
    # read back from robot.state. That reading is not the intent: robotd reports
    # `head_ema + chorale sway + idle offset` (robotd main.rs, the PolicyCommand it builds), i.e.
    # a slewed, lagging copy of the intent plus whatever glance, neck lean or yawn its own idle
    # behaviour was adding at that instant. Writing it back froze those transient offsets into the
    # permanent head command, and the next gesture captured them again with a fresh idle offset on
    # top: the head drifted, and stayed bent after the duck had finished talking. robotd's idle
    # look-around still rides on top of neutral, so the duck stays alive without accumulating.

    NEUTRAL_HEAD = {"neck_pitch": 0.0, "head_pitch": 0.0, "head_yaw": 0.0, "head_roll": 0.0}

    async def _neutral_head(self) -> None:
        await self.robot.notify("robot.head", dict(self.NEUTRAL_HEAD))

    GAZE = {  # trunk frame, metres; the camera sits ~0.13 m above the trunk origin
        "ahead": (0.6, 0.0, 0.13), "up": (0.6, 0.0, 0.42), "down": (0.5, 0.0, -0.06),
        "left": (0.35, 0.42, 0.13), "right": (0.35, -0.42, 0.13),
        "small_left": (0.6, 0.22, 0.13), "small_right": (0.6, -0.22, 0.13),
    }

    async def _look(self, where: str) -> dict:
        x, y, z = self.GAZE[where]
        return await self.robot.call("robot.look", {"x": x, "y": y, "z": z}, timeout=1.0)

    async def _pose(self, z=0.0, roll=0.0, pitch=0.0, active=True) -> None:
        await self.robot.notify("robot.pose", {"z": z, "roll": roll, "pitch": pitch, "active": active})

    async def _gesture(self, gesture: str, duration: float, ex: Execution) -> dict:
        r0 = self.hub.robot or {}
        head0 = list(r0.get("head") or [0.0, 0.0, 0.0, 0.0])  # a measurement reference only, never sent back
        seen_head: list[list[float]] = []
        t0 = time.monotonic()
        step = 0

        def beat(period):
            return int((time.monotonic() - t0) / period)

        try:
            while time.monotonic() - t0 < duration:
                self.safety.check_motion()
                t = time.monotonic() - t0
                if gesture in ("nod", "celebrate", "bow"):
                    b = beat(0.35) + 1
                    if b != step:
                        step = b
                        await self._look("up" if step % 2 else "down")
                elif gesture == "shake":
                    b = beat(0.3) + 1
                    if b != step:
                        step = b
                        await self._look("small_left" if step % 2 else "small_right")
                elif gesture in ("look_left", "look_right", "look_up", "look_down"):
                    if step == 0:
                        step = 1
                        await self._look(gesture.split("_")[1])
                elif gesture == "curious":
                    if step == 0:
                        step = 1
                        answer = await self._look("small_left")
                        h = answer.get("head", {})
                        await self.robot.notify("robot.head", {**{k: h.get(k, 0.0) for k in
                                                                 ("neck_pitch", "head_pitch", "head_yaw")},
                                                              "head_roll": 0.3})
                elif gesture == "sleepy":
                    if step == 0:
                        step = 1
                        await self._look("down")
                if gesture in ("wiggle", "celebrate"):
                    await self._pose(roll=0.15 * math.sin(2 * math.pi * 2.0 * t))
                elif gesture == "crouch":
                    await self._pose(z=-0.02)
                elif gesture == "bow":
                    await self._pose(pitch=0.2 * min(1.0, t / 0.5))
                elif gesture == "sleepy":
                    await self._pose(z=-0.015 * min(1.0, t / 1.0))
                h = (self.hub.robot or {}).get("head")
                if h:
                    seen_head.append(list(h))
                await asyncio.sleep(1.0 / self.s.intent_hz)
        finally:
            # Release: body pose back to nominal, head back to neutral (see NEUTRAL_HEAD) — also when
            # the gesture was cancelled or superseded halfway, so an interruption never leaves a pose.
            try:
                await self._pose(active=False)
                await self._neutral_head()
            except Exception:  # noqa: BLE001
                pass
        moved = max((max(abs(a - b) for a, b in zip(h, head0)) for h in seen_head), default=0.0)
        pose_only = gesture in ("wiggle", "crouch")
        out = dict(gesture=gesture, head_moved_rad=round(moved, 3), samples=len(seen_head))
        if not pose_only and moved < 0.03:
            raise Failed(f"robotd did not reflect the {gesture} in robot.state.head (moved {moved:.3f} rad)", out)
        out["acknowledged"] = True
        return out

    async def _do_emote(self, cmd: C.Emote, ex: Execution):
        self.safety.current_command = ex.command
        out = await self._gesture(cmd.gesture, cmd.duration_s, ex)
        up = self._measure_end(self._measure_start())
        out["upright_at_end"] = up["upright_at_end"]
        if out["upright_at_end"] is False:
            raise Failed(f"{cmd.gesture}: the robot was down afterwards", out)
        return f"{cmd.gesture.replace('_', ' ')} done (head moved {out['head_moved_rad']:.2f} rad in robot.state)", out

    async def _do_neutral(self, cmd: C.Neutral, ex: Execution):
        """Upright and neutral: body pose released, head command to the trained nominal. robotd
        slews there (no snap); the result is read back once it has had time to settle."""
        self.safety.current_command = ex.command
        before = list((self.hub.robot or {}).get("head") or [0.0] * 4)
        await self._pose(active=False)
        await self._neutral_head()
        # robotd fades its idle offset out over [idle] fade_s (1.5 s) and starts it again after_s
        # (2 s) after the last head command: read between the two, when the head shows the intent
        await asyncio.sleep(1.7)
        after = list((self.hub.robot or {}).get("head") or [0.0] * 4)
        off = max(abs(v) for v in after) if after else 0.0
        out = dict(head_before=[round(v, 3) for v in before], head_after=[round(v, 3) for v in after],
                   head_offset_rad=round(off, 3))
        # robotd's idle glances fade back in on top of neutral after a few still seconds; right after
        # the reset they are faded out, so a large residual here means the reset did not take.
        if off > 0.12:
            raise Failed(f"the head did not settle at neutral (largest offset {off:.2f} rad)", out)
        return f"upright and neutral (head offsets within {off:.2f} rad)", out

    async def _do_dance(self, cmd: C.Dance, ex: Execution):
        self.safety.current_command = ex.command
        start = self._measure_start()
        steps = []
        if cmd.style == "spin":
            plan = [("turn", 360.0)]
        elif cmd.style == "bob":
            plan = [("gesture", "celebrate"), ("gesture", "nod")]
        else:
            plan = [("gesture", "wiggle"), ("turn", 45.0), ("turn", -90.0), ("turn", 45.0), ("gesture", "celebrate")]
        for kind, arg in plan:
            if kind == "gesture":
                res = await self._gesture(arg, 1.6, ex)
                steps.append(dict(step=arg, head_moved_rad=res["head_moved_rad"]))
            else:
                sub = C.Turn(vyaw=0.8 if arg > 0 else -0.8, angle_deg=arg)
                msg, res = await self._do_turn(sub, ex)
                steps.append(dict(step=f"turn {arg:+.0f}", turned_deg=res.get("turned_deg")))
        out = self._measure_end(start)
        out["steps"] = steps
        if out["upright_at_end"] is False:
            raise Failed("the dance ended on the floor", out)
        return f"{cmd.style} dance: " + ", ".join(st["step"] for st in steps), out

    async def _do_stop(self, cmd, ex):
        self._teleop_active = False
        await self.cancel_all("stopped")
        await self.robot.move(0, 0, 0)
        answer = await self.robot.call("robot.stop")
        if not answer.get("accepted"):
            raise Failed(f"robot.stop refused: {answer.get('reason')}")
        return "stopped (twist zeroed)", {"robotd": answer}

    async def _do_estop(self, cmd, ex):
        result = await self.estop(f"requested by {ex.source}")
        return "emergency stop latched — say 'clear emergency stop' to move again", result

    async def _do_clear_estop(self, cmd, ex):
        self.safety.clear()
        return "emergency stop cleared", {}

    async def _enable(self) -> tuple[str, dict]:
        if self.safety.estop:
            raise Rejected("emergency stop is latched — clear it before enabling")
        policies = await self.robot.call("robot.policies")
        if policies.get("enabled") and policies.get("sitting"):
            answer = await self.robot.call("robot.do", {"skill": "sit_toggle"})
            if not answer.get("accepted"):
                raise Failed(f"robot is sitting and would not stand: {answer.get('reason')}")
        else:
            answer = await self.robot.call("robot.enable", {"on": True})
            if not answer.get("accepted"):
                raise Failed(f"robot.enable refused: {answer.get('reason')}")
        # Accepted is not driving: wait for a policy to actually take the robot.
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            r = self.hub.robot
            if r and r.get("policy") not in (None, "held"):
                return f"enabled — policy '{r['policy']}' is driving", {"robotd": answer, "policy": r["policy"]}
            await asyncio.sleep(0.2)
        unavailable = (self.robot.ack or {}).get("unavailable")
        raise Failed("robotd accepted enable but no policy is driving"
                     + (f": {unavailable}" if unavailable else " (robot.state.policy stayed 'held')"),
                     {"robotd": answer})

    async def _do_enable(self, cmd, ex):
        msg, out = await self._enable()
        await self._neutral_head()  # stand up looking ahead, whatever an earlier session left behind
        return msg, out

    async def _do_disable(self, cmd, ex):
        await self._cancel_activity("cancelled", "policy disabled")
        answer = await self.robot.call("robot.enable", {"on": False})
        if not answer.get("accepted"):
            raise Failed(f"robot.enable off refused: {answer.get('reason')}")
        return f"policy disabled ({answer.get('reason', 'accepted')})", {"robotd": answer}

    def available_skills(self) -> list[str]:
        ack = self.robot.ack or {}
        names = list(ack.get("skills") or [])
        if ack.get("ground_pick"):
            names.append("ground_pick")
        if ack.get("sitstand"):
            names.append("sit_toggle")
        return names

    async def _do_skill(self, cmd: C.Skill, ex):
        skills = self.available_skills()
        if cmd.skill not in skills:
            raise Rejected(f"this robotd has no skill '{cmd.skill}' (it reported: {', '.join(skills) or 'none'})")
        self.safety.current_command = ex.command
        if cmd.skill != "sit_toggle":
            self.safety.check_motion()
        start = self._measure_start()
        answer = await self.robot.call("robot.do", {"skill": cmd.skill})
        if not answer.get("accepted"):
            raise Failed(f"robot.do refused: {answer.get('reason')}", {"robotd": answer})
        seen = set()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 4.0:
            r = self.hub.robot
            if r:
                seen.add(r.get("policy"))
            await asyncio.sleep(0.1)
        out = self._measure_end(start)
        out.update(robotd=answer, policies_seen=sorted(p for p in seen if p))
        # robot.state.policy names what drove each tick (robotd control.rs's frame label, not its
        # `Driving` display): a skill by its config name, `ground_pick`, and for the sit toggle
        # `sit` going down or `rise` coming up. Accepted is not done: the skill must be seen.
        expected = {"sit_toggle": {"sit", "rise"}, "ground_pick": {"ground_pick"}}.get(cmd.skill, {cmd.skill})
        if not expected & seen:
            raise Failed(f"robot.do {cmd.skill} was accepted, but robot.state never showed it driving "
                         f"(saw {out['policies_seen']})", out)
        if out["upright_at_end"] is False and cmd.skill != "roulade":
            raise Failed(f"{cmd.skill} accepted, but the robot was down afterwards", out)
        return f"{cmd.skill}: robotd accepted ({answer.get('reason') or 'ok'}); policies seen {out['policies_seen']}", out

    # ── simulator actions ─────────────────────────────────────────────────

    async def _do_pause(self, cmd, ex):
        await self.cancel_all("simulation paused")
        await self._halt_quietly()
        await self.xbody.op({"op": "pause"})
        return "physics paused (robotd keeps running; motion is refused until resumed)", {}

    async def _do_resume(self, cmd, ex):
        await self.xbody.op({"op": "resume"})
        return "physics resumed", {}

    async def reset_world(self, keyframe: str = "SIT", enable_after: bool = True) -> dict:
        await self._cancel_activity("cancelled", "world reset")
        out: dict[str, Any] = {}
        try:
            out["disable"] = await self.robot.call("robot.enable", {"on": False})
        except Exception as error:  # noqa: BLE001
            out["disable"] = f"skipped: {error}"
        out["sim"] = await self.xbody.op({"op": "reset", "keyframe": keyframe})
        self.obstacle_contacts, self._touching = 0, set()
        await asyncio.sleep(0.5)
        if enable_after:
            msg, enable_out = await self._enable()
            out["enable"] = msg
        return out

    async def _do_reset(self, cmd: C.Reset, ex):
        out = await self.reset_world(cmd.keyframe, cmd.enable_after)
        return f"world reset to {cmd.keyframe}" + (f"; {out['enable']}" if "enable" in out else ""), out

    async def prepare_scene(self, environment: str, layout: str | None) -> None:
        hello = self.xbody.hello or await self.xbody.op({"op": "hello"})
        current_layout = (hello.get("opts") or {}).get("layout") or ("open" if hello.get("env") == "arena" else None)
        if hello.get("env") == environment and (layout is None or layout == current_layout):
            return
        await self.set_environment(environment, layout)

    async def set_environment(self, environment: str, layout: str | None) -> dict:
        if self.supervisor is None:
            raise Rejected("switching scenes restarts the MuJoCo body, and this backend does not manage it "
                           "(start with microduck-x/scripts/x-start.sh, or set MDX_MANAGE_SIM=1)")
        await self._cancel_activity("cancelled", f"switching to {environment}")
        try:
            await self.robot.call("robot.enable", {"on": False})
        except Exception:  # noqa: BLE001
            pass
        await self.supervisor.restart(environment, layout)
        self.xbody.invalidate_model()
        self._grid_cache = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                hello = await self.xbody.op({"op": "hello"})
                if hello.get("env") == environment:
                    break
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.2)
        else:
            raise Failed("the new simulator did not come up")
        header = await self.load_model_index()
        self.events.emit("sim.model_changed", f"environment is now {environment}", env=environment, layout=layout)
        # A fresh body holds the duck until torque is switched on again, and robotd does not replay
        # torque on reconnect. relax → enable re-sends it. Relax is only safe because the new body
        # is holding the duck (`released` false), which is checked first.
        out: dict[str, Any] = {"env": header["env"] if header else environment}
        for _ in range(30):
            if self.hub.duck() is not None and self.xbody.last_frame and self.xbody.frame_age() < 0.5:
                break
            await asyncio.sleep(0.1)
        duck = self.hub.duck()
        if duck is not None and not duck.get("released"):
            try:
                out["relax"] = await self.robot.call("robot.relax")
                msg, _ = await self._enable()
                out["enable"] = msg
            except Failed as error:
                out["enable"] = f"not driving: {error}"
            except Exception as error:  # noqa: BLE001
                out["enable"] = f"skipped: {error}"
        return out

    async def _do_set_environment(self, cmd: C.SetEnvironment, ex):
        layout = cmd.layout if cmd.environment == "arena" else None
        out = await self.set_environment(cmd.environment, layout)
        return f"environment switched to {cmd.environment}" + (f" ({layout})" if layout else ""), out

    async def _do_push(self, cmd: C.Push, ex):
        pose, _ = self.pose()
        yaw = pose[2] if pose else 0.0
        # "from the front" pushes backwards along the heading, etc.
        angle = {"front": yaw + math.pi, "back": yaw, "left": yaw - math.pi / 2, "right": yaw + math.pi / 2}[cmd.direction]
        force = [cmd.newtons * math.cos(angle), cmd.newtons * math.sin(angle), 0.0]
        start = self._measure_start()
        answer = await self.xbody.op({"op": "push", "duck": 0, "force": force, "seconds": cmd.seconds})
        self.events.emit("sim.push", f"{cmd.newtons:g} N push from the {cmd.direction}", newtons=cmd.newtons,
                         direction=cmd.direction)
        await asyncio.sleep(cmd.seconds + 1.5)
        out = self._measure_end(start)
        out["sim"] = answer
        verdict = "stayed upright" if out["upright_at_end"] else "fell"
        return f"pushed {cmd.newtons:g} N from the {cmd.direction} for {cmd.seconds:g} s; robot {verdict}", out

    # ── UI actions ────────────────────────────────────────────────────────

    async def _do_camera(self, cmd: C.Camera, ex):
        self.events.emit("ui.camera", cmd.mode, mode=cmd.mode)
        return f"camera → {cmd.mode}", {}

    async def _do_show_panel(self, cmd: C.ShowPanel, ex):
        self.events.emit("ui.panel", cmd.panel, panel=cmd.panel, visible=cmd.visible)
        return f"{'showing' if cmd.visible else 'hiding'} {cmd.panel}", {}

    # ── navigation (Microduck X extension) ────────────────────────────────

    def resolve_target(self, target: str) -> tuple[str, tuple[float, float]]:
        markers = self.markers()
        if not markers:
            raise Rejected("no scene model loaded yet, so no markers to go to")
        t = target.lower().strip().replace(" ", "_")
        if t in markers:
            return t, markers[t]
        if t in ("goal", "target", "marker"):
            goals = {k: v for k, v in markers.items() if k.startswith("goal") or k not in ("start",)}
            pose, _ = self.pose()
            if not goals:
                raise Rejected("this scene has no goal markers")
            name = min(goals, key=lambda k: math.hypot(goals[k][0] - pose[0], goals[k][1] - pose[1]) if pose else 0)
            return name, goals[name]
        close = [k for k in markers if k.startswith(t) or t in k]
        if len(close) == 1:
            return close[0], markers[close[0]]
        raise Rejected(f"no marker '{target}' here. This scene has: {', '.join(markers)}")

    async def _grid(self) -> Grid:
        header, mesh, _ = await self.xbody.model()
        bounds = header["env"]["bounds"]
        obstacles = obstacles_from_model(header, self.xbody.last_frame, mesh)
        return Grid(bounds, obstacles)

    async def _walk_path(self, ex: Execution, goal_name: str, goal: tuple[float, float], speed: float) -> dict:
        pose, src = self.pose()
        if pose is None:
            raise Rejected("navigation needs a pose and none is available")
        grid = await self._grid()
        try:
            path = plan(grid, (pose[0], pose[1]), goal)
        except PlanError as error:
            raise Failed(f"no plan to {goal_name}: {error}")
        length = sum(math.dist(a, b) for a, b in zip([(pose[0], pose[1])] + path, path))
        self.last_plan = dict(goal=goal_name, path=[(pose[0], pose[1])] + path, length_m=round(length, 3),
                              grid=grid.to_json())
        self.events.emit("nav.plan", f"path to {goal_name}: {len(path)} legs, {length:.2f} m", goal=goal_name,
                         path=self.last_plan["path"], length_m=round(length, 3))
        t0 = time.monotonic()
        timeout = length / speed * 3.0 + 12.0
        contacts0 = self.obstacle_contacts
        period = 1.0 / self.s.intent_hz
        best, best_at = math.inf, time.monotonic()
        for i, wp in enumerate(path):
            final = i == len(path) - 1
            tol = 0.15 if final else 0.12
            while True:
                pose, _ = self.pose()
                vx, vyaw, dist = heading_command(pose, wp, speed, final)
                if dist < tol:
                    break
                if dist < best - 0.04:
                    best, best_at = dist, time.monotonic()
                if time.monotonic() - best_at > 8.0:
                    raise Failed(f"no progress towards {goal_name} for 8 s (blocked, or the gait is not tracking)",
                                 dict(distance_m=round(dist, 3)))
                if time.monotonic() - t0 > timeout:
                    raise Failed(f"navigation to {goal_name} timed out after {timeout:.0f} s",
                                 dict(distance_m=round(dist, 3)))
                await self._drive_tick(vx, 0.0, vyaw, t0, ex)
                await asyncio.sleep(period)
            best = math.inf
            best_at = time.monotonic()
        await self._require_ack(ex)
        await self._stop_and_settle()
        pose, _ = self.pose()
        err = math.hypot(pose[0] - goal[0], pose[1] - goal[1])
        return dict(goal=goal_name, goal_xy=list(goal), error_m=round(err, 3), path_length_m=round(length, 3),
                    seconds=round(time.monotonic() - t0, 1), obstacle_contacts=self.obstacle_contacts - contacts0,
                    pose_source=src, legs=len(path))

    async def _do_goto(self, cmd: C.Goto, ex):
        self.safety.current_command = ex.command
        if cmd.target:
            name, goal = self.resolve_target(cmd.target)
        else:
            name, goal = f"({cmd.x:g}, {cmd.y:g})", (cmd.x, cmd.y)
        out = await self._walk_path(ex, name, goal, cmd.speed)
        msg = (f"reached {name}: {out['error_m']:.2f} m from the marker after {out['seconds']:.0f} s, "
               f"{out['obstacle_contacts']} obstacle contacts ({out['pose_source']})")
        return msg, out

    async def _do_waypoints(self, cmd: C.Waypoints, ex):
        self.safety.current_command = ex.command
        markers = self.markers()
        names = cmd.targets or [k for k in markers if k.startswith("goal")] or [k for k in markers if k != "start"]
        if not names:
            raise Rejected("this scene has no waypoints")
        legs = []
        for n in names:
            name, goal = self.resolve_target(n)
            legs.append(await self._walk_path(ex, name, goal, cmd.speed))
        return f"visited {len(legs)} waypoints: {', '.join(l['goal'] for l in legs)}", {"legs": legs}

    # ── experiments and reports ───────────────────────────────────────────

    async def policies_snapshot(self) -> dict | None:
        try:
            return await self.robot.call("robot.policies")
        except Exception:  # noqa: BLE001
            return None

    async def _do_run_experiment(self, cmd: C.RunExperiment, ex):
        try:
            run = await self.runner.run(self, cmd.experiment, cmd.trials if cmd.trials > 1 else None)
        except ValueError as error:
            raise Rejected(str(error))
        passed = run.get("passed_trials", 0)
        summary = dict(run_id=run["id"], passed=passed, trials=run["trials"], status=run["status"],
                       failures=[r["criteria_failed"] for r in run["results"] if not r["passed"]])
        msg = f"{run['title']}: {passed}/{run['trials']} trials passed (run {run['id']})"
        if run["status"] != "passed":
            raise Failed(msg, summary)
        return msg, summary

    async def _do_save_experiment(self, cmd, ex):
        run_id = self.runner.last_run_id or next((r["id"] for r in self.store.list()), None)
        if not run_id:
            raise Rejected("no experiment run to save yet")
        self.store.mark_saved(run_id)
        return f"run {run_id} marked as saved", {"run_id": run_id}

    async def _do_compare_runs(self, cmd, ex):
        runs = self.store.list()
        if len(runs) < 2:
            raise Rejected("need at least two experiment runs to compare")
        a, b = self.store.get(runs[1]["id"]), self.store.get(runs[0]["id"])
        cmp = self.runner.compare(a, b)
        parts = [f"{k}: {v:+g}" for k, v in cmp["delta_b_minus_a"].items()]
        return f"{b['id']} vs {a['id']}: " + (", ".join(parts) or "no comparable metrics"), cmp

    async def _do_status(self, cmd: C.Status, ex):
        if cmd.topic == "fall":
            return self.safety.explain_last_fall(), {}
        r = self.hub.robot
        up = self.hub.upright()
        ack = self.robot.ack or {}
        parts = []
        if r is None:
            parts.append("I have no telemetry from robotd.")
        else:
            parts.append(f"robotd loop at {r.get('loop', {}).get('hz', 0):.0f} Hz; policy driving: {r.get('policy')}.")
            mv = r.get("move", {})
            parts.append(f"Requested twist {mv.get('requested')}, applied {mv.get('applied')}.")
        if ack.get("unavailable"):
            parts.append(f"Policy problem reported by robotd: {ack['unavailable']}.")
        if cmd.topic == "joints" and r:
            names = ["L hip yaw", "L hip roll", "L hip pitch", "L knee", "L ankle", "neck", "head pitch", "head yaw",
                     "head roll", "mouth", "R hip yaw", "R hip roll", "R hip pitch", "R knee", "R ankle"]
            parts.append("Joints (deg): " + ", ".join(f"{n} {math.degrees(q):.0f}" for n, q in zip(names, r["joints"])))
        if cmd.topic == "policy":
            parts.append(f"Loaded: walk={ack.get('walk')}, stand={ack.get('stand')}, skills={ack.get('skills')}.")
        pose, src = self.pose()
        if pose:
            parts.append(f"Position ({src}): x {pose[0]:.2f} m, y {pose[1]:.2f} m, heading {math.degrees(pose[2]):.0f}°.")
        if up["ground_truth_tilt_deg"] is not None:
            parts.append(f"Tilt {up['ground_truth_tilt_deg']:.0f}°, trunk height {up['trunk_z']:.3f} m.")
        if self._activity:
            parts.append(f"Current task: {self._activity[0].action}.")
        blockers = self.safety.motion_blockers()
        parts.append("Motion allowed." if not blockers else "Motion blocked: " + "; ".join(blockers) + ".")
        return " ".join(parts), {}

    async def _do_help(self, cmd, ex):
        return ("Try: walk forward for two seconds; turn left ninety degrees; stop; sit down; stand up; "
                "switch to the garden; go to goal two; return to the start; push it gently; start the walking "
                "test; camera follow; show the joint angles; explain the robot's status; emergency stop."), {}
