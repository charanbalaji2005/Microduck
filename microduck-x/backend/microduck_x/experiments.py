"""Repeatable experiments: a configuration, a command sequence, success criteria, and the
telemetry that decides them.

A trial passes only when its criteria evaluate true on *recorded telemetry*. A command being
accepted by robotd is never, by itself, a pass. Every run records the configuration, the
policies robotd reported loading, the simulator build and the scene, so it can be repeated.

Sources of nondeterminism, stated rather than hidden: robotd's loop is wall-clock while MuJoCo
is stepped in 20 ms batches paced to real time, so the phase between a policy tick and a physics
batch varies run to run; OS scheduling jitter moves it further. Two runs with the same seed and
scene are therefore similar, not identical.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .commands import Envelope
from .config import REPO, Settings
from .events import EventBus
from .telemetry import TelemetryHub


class Criteria(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_forward_m: float | None = None
    max_forward_m: float | None = None
    max_lateral_m: float | None = None
    min_abs_yaw_deg: float | None = None
    max_abs_yaw_deg: float | None = None
    upright: bool = True
    max_tilt_deg: float | None = 45.0
    goal: str | None = None
    goal_tolerance_m: float = 0.25
    max_obstacle_contacts: int | None = None
    max_duration_s: float | None = None


class ExperimentDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    title: str
    description: str = ""
    environment: str = "arena"
    layout: str | None = None
    keyframe: str = "SIT"
    settle_s: float = Field(10.0, gt=0, le=60)
    steps: list[dict]
    success: Criteria = Field(default_factory=Criteria)
    trials: int = Field(1, ge=1, le=20)
    record_hz: float = Field(10.0, gt=0, le=10.0)


def load_definitions(folder: Path) -> dict[str, ExperimentDef]:
    out = {}
    for path in sorted(folder.glob("*.json")):
        d = ExperimentDef.model_validate_json(path.read_text())
        for step in d.steps:  # every step must be a valid command
            Envelope.model_validate({"command": step, "source": "experiment"})
        out[d.name] = d
    return out


def git_rev() -> str | None:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=2).stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _unwrap(yaws: list[float]) -> float:
    total = 0.0
    for a, b in zip(yaws, yaws[1:]):
        total += (b - a + math.pi) % (2 * math.pi) - math.pi
    return total


def metrics(samples: list[dict], start: dict | None, markers: dict[str, tuple[float, float]], goal: str | None,
            obstacle_contacts: int, duration: float) -> dict:
    gt = [s for s in samples if "x" in s]
    m: dict[str, Any] = dict(samples=len(samples), duration_s=round(duration, 2), obstacle_contacts=obstacle_contacts,
                             source="simulator ground truth" if gt else "unavailable")
    if not gt or start is None:
        return m
    x0, y0, yaw0 = start["x"], start["y"], start["yaw"]
    end = gt[-1]
    dx, dy = end["x"] - x0, end["y"] - y0
    c, s = math.cos(yaw0), math.sin(yaw0)
    tilts = [t["tilt"] for t in gt if t.get("tilt") is not None]
    m.update(
        forward_m=round(c * dx + s * dy, 4), lateral_m=round(-s * dx + c * dy, 4),
        distance_m=round(math.hypot(dx, dy), 4), yaw_change_deg=round(math.degrees(_unwrap([t["yaw"] for t in gt])), 2),
        max_tilt_deg=round(max(tilts), 2) if tilts else None, min_trunk_z=round(min(t["z"] for t in gt), 4),
        mean_v_fwd=round(sum(t["v_fwd"] for t in gt) / len(gt), 4),
        fell=any((t.get("tilt") or 0) > 60 for t in gt) or any(t.get("fallen") for t in samples),
        end=dict(x=round(end["x"], 4), y=round(end["y"], 4), yaw=round(end["yaw"], 4)),
    )
    odom = [t["odom"] for t in samples if t.get("odom")]
    if len(odom) >= 2:
        a, b = odom[0]["position"], odom[-1]["position"]
        m["robot_odom_distance_m"] = round(math.hypot(b[0] - a[0], b[1] - a[1]), 4)
    if goal and goal in markers:
        gx, gy = markers[goal]
        m["goal_error_m"] = round(math.hypot(end["x"] - gx, end["y"] - gy), 4)
    return m


def evaluate(c: Criteria, m: dict, step_failures: list[str]) -> tuple[bool, list[str], list[str]]:
    passed, failed = [], []

    def check(ok: bool | None, text: str):
        if ok is None:
            failed.append(f"{text}: not measurable (no ground truth)")
        elif ok:
            passed.append(text)
        else:
            failed.append(text)

    has = "forward_m" in m
    if c.min_forward_m is not None:
        check(m["forward_m"] >= c.min_forward_m if has else None, f"forward ≥ {c.min_forward_m} m (got {m.get('forward_m')})")
    if c.max_forward_m is not None:
        check(m["forward_m"] <= c.max_forward_m if has else None, f"forward ≤ {c.max_forward_m} m (got {m.get('forward_m')})")
    if c.max_lateral_m is not None:
        check(abs(m["lateral_m"]) <= c.max_lateral_m if has else None, f"|lateral| ≤ {c.max_lateral_m} m (got {m.get('lateral_m')})")
    if c.min_abs_yaw_deg is not None:
        check(abs(m["yaw_change_deg"]) >= c.min_abs_yaw_deg if has else None,
              f"|yaw change| ≥ {c.min_abs_yaw_deg}° (got {m.get('yaw_change_deg')})")
    if c.max_abs_yaw_deg is not None:
        check(abs(m["yaw_change_deg"]) <= c.max_abs_yaw_deg if has else None,
              f"|yaw change| ≤ {c.max_abs_yaw_deg}° (got {m.get('yaw_change_deg')})")
    if c.upright:
        check((not m["fell"]) if has else None, "stayed upright")
    if c.max_tilt_deg is not None:
        check(m["max_tilt_deg"] <= c.max_tilt_deg if has and m.get("max_tilt_deg") is not None else None,
              f"max tilt ≤ {c.max_tilt_deg}° (got {m.get('max_tilt_deg')})")
    if c.goal:
        check(m["goal_error_m"] <= c.goal_tolerance_m if "goal_error_m" in m else None,
              f"within {c.goal_tolerance_m} m of {c.goal} (got {m.get('goal_error_m')})")
    if c.max_obstacle_contacts is not None:
        check(m["obstacle_contacts"] <= c.max_obstacle_contacts,
              f"obstacle contacts ≤ {c.max_obstacle_contacts} (got {m['obstacle_contacts']})")
    if c.max_duration_s is not None:
        check(m["duration_s"] <= c.max_duration_s, f"duration ≤ {c.max_duration_s} s (got {m['duration_s']})")
    for f in step_failures:
        failed.append(f"step failed: {f}")
    return (not failed), passed, failed


class ExperimentStore:
    def __init__(self, settings: Settings):
        self.dir = settings.runs_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, run: dict) -> Path:
        path = self.dir / f"{run['id']}.json"
        path.write_text(json.dumps(run, indent=1, default=str))
        return path

    def list(self) -> list[dict]:
        out = []
        for p in sorted(self.dir.glob("*.json"), reverse=True):
            try:
                r = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            out.append({k: r.get(k) for k in ("id", "experiment", "title", "started", "status", "passed_trials",
                                              "trials", "saved", "environment")})
        return out

    def get(self, run_id: str) -> dict:
        if not run_id.replace("-", "").replace("_", "").isalnum():
            raise KeyError(run_id)
        return json.loads((self.dir / f"{run_id}.json").read_text())

    def mark_saved(self, run_id: str) -> dict:
        r = self.get(run_id)
        r["saved"] = True
        self.save(r)
        return r

    def csv(self, run_id: str) -> str:
        r = self.get(run_id)
        buf = io.StringIO()
        cols = ["trial", "t", "sim_t", "x", "y", "z", "yaw", "roll", "pitch", "tilt", "v_fwd", "v_lat", "wz",
                "foot_l", "foot_r", "contacts", "policy", "req_vx", "req_vyaw", "app_vx", "app_vyaw", "loop_hz",
                "fallen", "rtf"]
        q_cols = [f"q{i}" for i in range(15)]
        w = csv.writer(buf)
        w.writerow(cols + q_cols)
        for trial in r.get("results", []):
            for s in trial.get("samples", []):
                req, app = s.get("req") or [None] * 3, s.get("app") or [None] * 3
                row = [trial["trial"], s.get("t"), s.get("sim_t"), s.get("x"), s.get("y"), s.get("z"), s.get("yaw"),
                       s.get("roll"), s.get("pitch"), s.get("tilt"), s.get("v_fwd"), s.get("v_lat"), s.get("wz"),
                       s.get("foot_l"), s.get("foot_r"), s.get("contacts"), s.get("policy"), req[0], req[2],
                       app[0], app[2], s.get("loop_hz"), s.get("fallen"), s.get("rtf")]
                w.writerow(row + list(s.get("q") or [None] * 15))
        return buf.getvalue()


class ExperimentRunner:
    def __init__(self, settings: Settings, hub: TelemetryHub, events: EventBus, store: ExperimentStore):
        self.s = settings
        self.hub = hub
        self.events = events
        self.store = store
        self.last_experiment: str | None = None
        self.last_run_id: str | None = None
        self.current: dict | None = None

    def definitions(self) -> dict[str, ExperimentDef]:
        return load_definitions(self.s.experiments_dir)

    async def run(self, executor, name: str | None, trials: int | None) -> dict:
        defs = self.definitions()
        name = name or self.last_experiment
        if not name:
            raise ValueError("no experiment named, and none has run yet")
        if name not in defs:
            raise ValueError(f"no experiment '{name}'. Known: {', '.join(defs)}")
        d = defs[name]
        trials = trials or d.trials
        self.last_experiment = name
        run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{name}"
        run: dict[str, Any] = dict(
            id=run_id, experiment=name, title=d.title, definition=d.model_dump(), trials=trials,
            started=time.time(), environment=d.environment, layout=d.layout, saved=False,
            software=dict(microduck_rev=git_rev(), robotd_ack=executor.robot.ack,
                          sim=executor.xbody.hello, policies=await executor.policies_snapshot()),
            results=[],
        )
        self.current = dict(id=run_id, name=name, trial=0, trials=trials)
        self.events.emit("experiment.start", f"experiment '{d.title}' × {trials}", run=run_id)
        try:
            for trial in range(1, trials + 1):
                self.current["trial"] = trial
                run["results"].append(await self._trial(executor, d, trial))
                self.store.save(run)
        finally:
            passed = sum(1 for r in run["results"] if r["passed"])
            run.update(finished=time.time(), passed_trials=passed,
                       status="passed" if run["results"] and passed == len(run["results"]) and len(run["results"]) == trials
                       else "incomplete" if len(run["results"]) < trials else "failed")
            self.store.save(run)
            self.last_run_id = run_id
            self.current = None
            self.events.emit("experiment.done", f"experiment '{d.title}': {passed}/{trials} trials passed",
                             "info" if passed == trials else "warn", run=run_id, passed=passed, trials=trials)
        return run

    async def _trial(self, executor, d: ExperimentDef, trial: int) -> dict:
        t0 = time.time()
        try:
            await executor.prepare_scene(d.environment, d.layout)
            await executor.reset_world(d.keyframe, enable_after=True)
            settled, why = await self._settle(d.settle_s)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 — a setup failure is a failed trial, with its reason
            settled, why = False, f"setup failed: {error}"
        samples: list[dict] = []
        unsubscribe = self.hub.subscribe(samples.append)
        step_failures: list[str] = []
        steps_log = []
        start = self.hub.sample()
        contacts0 = executor.obstacle_contacts
        started = time.time()
        try:
            if not settled:
                step_failures.append(why)
            else:
                for i, step in enumerate(d.steps):
                    env = Envelope.model_validate({"command": step, "source": "experiment"})
                    record = await executor.execute_inline(env)
                    steps_log.append(dict(step=i, action=record.action, status=record.status, message=record.message,
                                          outcome=record.outcome))
                    if record.status != "completed":
                        step_failures.append(f"{record.action}: {record.message}")
                        break
                await asyncio.sleep(1.0)
        finally:
            unsubscribe()
        duration = time.time() - started
        markers = executor.markers()
        m = metrics(samples, start if "x" in start else None, markers, d.success.goal,
                    executor.obstacle_contacts - contacts0, duration)
        if settled:
            ok, passed, failed = evaluate(d.success, m, step_failures)
        else:  # the trial never started; its criteria were not tested, so none is reported either way
            ok, passed, failed = False, [], [f"trial did not start — {why}"]
        self.events.emit("experiment.trial", f"trial {trial}: {'PASS' if ok else 'FAIL'}"
                         + ("" if ok else f" — {failed[0]}"), "info" if ok else "warn", trial=trial, metrics=m)
        return dict(trial=trial, passed=ok, criteria_passed=passed, criteria_failed=failed, metrics=m,
                    steps=steps_log, settle=dict(ok=settled, detail=why), wall_s=round(time.time() - t0, 2),
                    samples=samples)

    async def _settle(self, limit: float) -> tuple[bool, str]:
        """Standing means: a policy is driving, the trunk is up (≥ 0.095 m), tilt < 20°, for 1 s."""
        deadline = time.time() + limit
        good_since = None
        last = "no telemetry"
        while time.time() < deadline:
            r, up = self.hub.robot, self.hub.upright()
            policy = r.get("policy") if r else None
            tilt, z = up["ground_truth_tilt_deg"], up["trunk_z"]
            ok = policy not in (None, "held") and tilt is not None and tilt < 20 and z is not None and z >= 0.095
            last = f"policy={policy}, trunk z={z}, tilt={tilt if tilt is None else round(tilt, 1)}°"
            if ok:
                good_since = good_since or time.time()
                if time.time() - good_since >= 1.0:
                    return True, f"standing ({last})"
            else:
                good_since = None
            await asyncio.sleep(0.1)
        return False, f"robot did not stand up within {limit:g} s ({last})"

    @staticmethod
    def compare(a: dict, b: dict) -> dict:
        def summary(run):
            ms = [t["metrics"] for t in run.get("results", [])]
            keys = ["forward_m", "lateral_m", "yaw_change_deg", "max_tilt_deg", "goal_error_m", "duration_s",
                    "obstacle_contacts"]
            out = {}
            for k in keys:
                vals = [m[k] for m in ms if m.get(k) is not None]
                if vals:
                    out[k] = round(sum(vals) / len(vals), 4)
            out["pass_rate"] = round(sum(t["passed"] for t in run.get("results", [])) / max(1, len(ms)), 3)
            return out

        sa, sb = summary(a), summary(b)
        delta = {k: round(sb[k] - sa[k], 4) for k in sa if k in sb}
        return dict(a=dict(id=a["id"], experiment=a["experiment"], **sa),
                    b=dict(id=b["id"], experiment=b["experiment"], **sb), delta_b_minus_a=delta)
