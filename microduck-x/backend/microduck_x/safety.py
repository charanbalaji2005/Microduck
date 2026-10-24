"""The gate in front of every movement, and the record of every fall.

Movement is refused — not delayed, refused with reasons — when any of these holds:

* the emergency stop is latched;
* robotd is not connected, or its `robot.state` stream is older than `stale_robot_s`;
* the simulator is configured and its ground-truth stream is older than `stale_sim_s`;
* the physics is paused;
* the robot has fallen (robotd's own `safety.fallen`, or simulator tilt above `fall_tilt_deg`);
* no policy is driving (`robot.state.policy == "held"`) — robotd accepts `robot.move` either way,
  so without this check a walk would be "sent" to a robot that cannot walk.

None of this depends on speech recognition or a language model.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Settings
from .events import EventBus
from .telemetry import TelemetryHub, tilt_deg


class SafetyRefusal(RuntimeError):
    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass
class FallRecord:
    t: float
    sim_t: float | None
    source: str
    tilt_deg: float | None
    command: dict | None
    window: list[dict] = field(default_factory=list)


class SafetyMonitor:
    def __init__(self, settings: Settings, hub: TelemetryHub, events: EventBus, sim_expected: bool = True):
        self.s = settings
        self.hub = hub
        self.events = events
        self.sim_expected = sim_expected
        self.estop = False
        self.estop_reason: str | None = None
        self.estop_at: float | None = None
        self.falls: list[FallRecord] = []
        self._was_fallen = False
        self.current_command: dict | None = None

    # ── status ────────────────────────────────────────────────────────────

    def fallen(self) -> tuple[bool, str | None]:
        robot = self.hub.robot
        if robot and robot.get("safety", {}).get("fallen") and self.hub.robot_age() < self.s.stale_robot_s:
            return True, "robotd reports the robot has fallen"
        duck = self.hub.duck()
        if duck is not None and self.hub.sim_age() < self.s.stale_sim_s:
            tilt = tilt_deg(duck["gravity"])
            if tilt is not None and tilt > self.s.fall_tilt_deg:
                return True, f"simulator: trunk tilted {tilt:.0f}° (limit {self.s.fall_tilt_deg:.0f}°)"
        return False, None

    def motion_blockers(self) -> list[str]:
        reasons = []
        if self.estop:
            reasons.append("emergency stop is latched — clear it first")
        robot_age = self.hub.robot_age()
        if self.hub.robot is None:
            reasons.append("no telemetry from robotd yet")
        elif robot_age > self.s.stale_robot_s:
            reasons.append(f"robotd telemetry is stale ({robot_age:.1f} s old)")
        if self.sim_expected:
            sim_age = self.hub.sim_age()
            if self.hub.sim is None:
                reasons.append("no ground truth from the simulator yet")
            elif sim_age > self.s.stale_sim_s:
                reasons.append(f"simulator telemetry is stale ({sim_age:.1f} s old)")
            elif self.hub.sim.get("paused"):
                reasons.append("the simulation is paused")
        fallen, why = self.fallen()
        if fallen:
            reasons.append(f"robot is down: {why}")
        robot = self.hub.robot
        if robot is not None and robot.get("policy") in (None, "", "held"):
            reasons.append("no policy is driving the robot (robot.state.policy = held) — enable it ('stand up') "
                           "or install the policy set")
        return reasons

    def check_motion(self) -> None:
        reasons = self.motion_blockers()
        if reasons:
            raise SafetyRefusal(reasons)

    def status(self) -> dict:
        blockers = self.motion_blockers()
        fallen, why = self.fallen()
        return dict(motion_allowed=not blockers, blockers=blockers, estop=self.estop,
                    estop_reason=self.estop_reason, fallen=fallen, fallen_reason=why,
                    robot_age_s=_finite(self.hub.robot_age()), sim_age_s=_finite(self.hub.sim_age()),
                    falls=len(self.falls))

    # ── latching stop ─────────────────────────────────────────────────────

    def latch(self, reason: str) -> None:
        self.estop, self.estop_reason, self.estop_at = True, reason, time.time()
        self.events.emit("safety.estop", f"emergency stop: {reason}", "warn")

    def clear(self) -> None:
        if self.estop:
            self.events.emit("safety.estop_cleared", "emergency stop cleared")
        self.estop, self.estop_reason, self.estop_at = False, None, None

    # ── fall watch (called on every telemetry sample) ─────────────────────

    def on_sample(self, sample: dict) -> None:
        fallen, why = self.fallen()
        if fallen and not self._was_fallen:
            record = FallRecord(t=time.time(), sim_t=sample.get("sim_t"), source=why or "",
                                tilt_deg=sample.get("tilt"), command=self.current_command,
                                window=self.hub.recent(4.0))
            self.falls.append(record)
            self.falls = self.falls[-20:]
            self.events.emit("safety.fall", f"robot fell: {why}", "warn",
                             tilt_deg=sample.get("tilt"), command=self.current_command)
        if not fallen and self._was_fallen:
            self.events.emit("safety.upright", "robot is upright again")
        self._was_fallen = fallen

    def explain_last_fall(self) -> str:
        if not self.falls:
            return "I have not recorded a fall in this session."
        f = self.falls[-1]
        w = [s for s in f.window if s.get("tilt") is not None]
        parts = [f"The last fall was {time.time() - f.t:.0f} seconds ago ({f.source})."]
        if f.command:
            parts.append(f"The active command was {f.command.get('action')} "
                         f"{ {k: v for k, v in f.command.items() if k != 'action'} }.")
        else:
            parts.append("No command from this dashboard was running at the time.")
        if w:
            first, peak = w[0], max(w, key=lambda s: s["tilt"])
            parts.append(f"Over the preceding {len(w) / 10:.1f} s the trunk tilt went from {first['tilt']:.0f}° "
                         f"to {peak['tilt']:.0f}°.")
            vmax = max(abs(s.get("v_fwd") or 0) for s in w)
            parts.append(f"Peak forward speed was {vmax:.2f} m/s (ground truth).")
            limited = {lim for s in w for lim in (s.get("limited") or [])}
            if limited:
                parts.append(f"robotd was limiting the twist by: {', '.join(sorted(limited))}.")
            policies = {s.get("policy") for s in w if s.get("policy")}
            if policies:
                parts.append(f"Policies driving in that window: {', '.join(sorted(policies))}.")
            lost = [s for s in w if s.get("foot_l") == 0 and s.get("foot_r") == 0]
            if lost:
                parts.append(f"Both feet were off the ground in {len(lost)} of {len(w)} samples.")
        pushes = [e for e in self.events.recent(50, kind="sim.push") if f.t - 5 < e["t"] <= f.t]
        if pushes:
            parts.append(f"A {pushes[-1]['data'].get('newtons')} N push was applied "
                         f"{f.t - pushes[-1]['t']:.1f} s before the fall.")
        parts.append("This is a description of the recorded telemetry, not a diagnosis of the policy.")
        return " ".join(parts)


def _finite(v: float) -> float | None:
    return None if v == float("inf") else round(v, 3)
