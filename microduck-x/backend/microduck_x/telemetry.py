"""Two telemetry sources, kept apart and labelled.

* **robotd** (`robot.state`) — what the robot's own software believes: joints it measured,
  targets it sent, the policy that drove the tick, safety flags, loop rate, contact odometry.
  This is the stream a real Microduck would produce.
* **simulator ground truth** (xbody frames) — what MuJoCo knows and no robot can: world pose,
  world velocity, every contact and its force, foot contacts.

The hub keeps the latest of each, a 10 Hz history for charts and experiments, and derived
quantities with their source stated.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable

HISTORY_HZ = 10.0
HISTORY_S = 120


def tilt_deg(gravity) -> float | None:
    """Angle between the trunk's z axis and vertical, from projected gravity ([0,0,-1] upright)."""
    if not gravity:
        return None
    gz = max(-1.0, min(1.0, -float(gravity[2])))
    return math.degrees(math.acos(gz))


def roll_pitch(gravity) -> tuple[float, float] | tuple[None, None]:
    if not gravity:
        return None, None
    gx, gy, gz = (float(v) for v in gravity)
    return math.degrees(math.atan2(-gy, -gz)), math.degrees(math.atan2(gx, math.hypot(gy, gz)))


class RateMeter:
    def __init__(self, window: float = 2.0):
        self.window = window
        self.stamps: deque[float] = deque()

    def tick(self, now: float | None = None) -> None:
        now = now or time.monotonic()
        self.stamps.append(now)
        while self.stamps and now - self.stamps[0] > self.window:
            self.stamps.popleft()

    @property
    def hz(self) -> float:
        if len(self.stamps) < 2:
            return 0.0
        span = self.stamps[-1] - self.stamps[0]
        return (len(self.stamps) - 1) / span if span > 0 else 0.0


class TelemetryHub:
    def __init__(self):
        self.robot: dict | None = None
        self.robot_at = 0.0
        self.sim: dict | None = None
        self.sim_at = 0.0
        self.robot_rate = RateMeter()
        self.sim_rate = RateMeter()
        self.history: deque[dict] = deque(maxlen=int(HISTORY_HZ * HISTORY_S))
        self._last_sample = 0.0
        self._listeners: list[Callable[[dict], None]] = []
        self.policy_seen: deque[tuple[float, str]] = deque(maxlen=500)

    # ── ingest ────────────────────────────────────────────────────────────

    def on_robot(self, state: dict) -> None:
        now = time.monotonic()
        prev = self.robot.get("policy") if self.robot else None
        self.robot, self.robot_at = state, now
        self.robot_rate.tick(now)
        if state.get("policy") != prev:
            self.policy_seen.append((time.time(), state.get("policy", "")))
        self._maybe_sample(now)

    def on_sim(self, frame: dict) -> None:
        now = time.monotonic()
        self.sim, self.sim_at = frame, now
        self.sim_rate.tick(now)
        self._maybe_sample(now)

    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    # ── ages ──────────────────────────────────────────────────────────────

    def robot_age(self) -> float:
        return time.monotonic() - self.robot_at if self.robot_at else math.inf

    def sim_age(self) -> float:
        return time.monotonic() - self.sim_at if self.sim_at else math.inf

    # ── ground truth helpers ──────────────────────────────────────────────

    def duck(self, index: int = 0) -> dict | None:
        if not self.sim or not self.sim.get("ducks"):
            return None
        ducks = self.sim["ducks"]
        return ducks[index] if index < len(ducks) else None

    def pose(self) -> tuple[float, float, float] | None:
        """(x, y, yaw) from the simulator. Ground truth — not something a robot can know."""
        d = self.duck()
        if d is None:
            return None
        return d["trunk"][0], d["trunk"][1], d["yaw"]

    def upright(self) -> dict:
        d = self.duck()
        gt = tilt_deg(d["gravity"]) if d else None
        rb = tilt_deg(self.robot["safety"]["gravity"]) if self.robot and self.robot.get("safety") else None
        return dict(ground_truth_tilt_deg=gt, robot_tilt_deg=rb,
                    trunk_z=d["trunk"][2] if d else None,
                    robot_fallen=bool(self.robot and self.robot.get("safety", {}).get("fallen")))

    # ── history ───────────────────────────────────────────────────────────

    def sample(self) -> dict:
        s: dict = dict(t=time.time())
        d = self.duck()
        if d is not None and self.sim is not None:
            x, y, z = d["trunk"]
            yaw = d["yaw"]
            vx_w, vy_w, vz_w = d["linvel"]
            c, sn = math.cos(yaw), math.sin(yaw)
            r, p = roll_pitch(d["gravity"])
            s.update(
                sim_t=self.sim["t"], x=x, y=y, z=z, yaw=yaw, roll=r, pitch=p, tilt=tilt_deg(d["gravity"]),
                v_fwd=c * vx_w + sn * vy_w, v_lat=-sn * vx_w + c * vy_w, v_up=vz_w, wz=d["angvel"][2],
                foot_l=int(d["feet"]["left"]), foot_r=int(d["feet"]["right"]), contacts=d["external_contacts"],
                q=d["q"], qd=d["qd"], rtf=self.sim.get("rtf"), paused=self.sim.get("paused"),
            )
        if self.robot is not None:
            mv = self.robot.get("move", {})
            s.update(
                r_t=self.robot.get("t"), policy=self.robot.get("policy"),
                req=mv.get("requested"), app=mv.get("applied"), limited=mv.get("limited_by"),
                loop_hz=self.robot.get("loop", {}).get("hz"), fallen=self.robot.get("safety", {}).get("fallen"),
                r_q=self.robot.get("joints"), r_target=self.robot.get("targets"),
                odom=self.robot.get("odom"),
            )
        return s

    def _maybe_sample(self, now: float) -> None:
        if now - self._last_sample < 1.0 / HISTORY_HZ:
            return
        self._last_sample = now
        s = self.sample()
        self.history.append(s)
        for listener in list(self._listeners):
            try:
                listener(s)
            except Exception:  # noqa: BLE001
                pass

    def recent(self, seconds: float) -> list[dict]:
        cutoff = time.time() - seconds
        return [s for s in self.history if s["t"] >= cutoff]
