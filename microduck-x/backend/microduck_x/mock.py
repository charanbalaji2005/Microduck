"""MOCK robotd and MOCK simulator, for UI development and tests without MuJoCo or Rust.

**This is not the Microduck.** The mock robot is three boxes integrating the commanded twist;
there is no physics, no policy and no balance. Everything it serves is tagged `"mock": true`,
the environment is named `mock`, and the dashboard shows a banner while it is in use. It exists
so the parser, the executor and the UI can be exercised on a machine with nothing installed.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
from pathlib import Path

from .config import Settings

JOINTS = 15


def mock_paths(settings: Settings) -> tuple[Path, int]:
    return settings.state_dir / "mock" / "duck-a.sock", 7899


class MockWorld:
    def __init__(self, settings: Settings):
        self.sock, self.port = mock_paths(settings)
        self.x = self.y = self.yaw = 0.0
        self.cmd = (0.0, 0.0, 0.0)
        self.cmd_at = 0.0
        self.enabled = False
        self.paused = False
        self.skill: tuple[str, float] | None = None
        self.head = [0.0, 0.0, 0.0, 0.0]  # the head *intent* (robot.head / robot.look)
        # What real robotd adds to the intent in what it reports as robot.state.head: its idle
        # glances, neck leans and yawns (robotd main.rs). Zero unless a test sets it.
        self.idle_offset = [0.0, 0.0, 0.0, 0.0]
        self.t0 = time.monotonic()
        self.sim_t = 0.0
        self._servers = []
        self._task = None

    async def start(self):
        self.sock.parent.mkdir(parents=True, exist_ok=True)
        if self.sock.exists():
            self.sock.unlink()
        self._servers.append(await asyncio.start_unix_server(self._robotd, path=str(self.sock)))
        self._servers.append(await asyncio.start_server(self._xbody, "127.0.0.1", self.port))
        self._task = asyncio.create_task(self._physics())

    async def stop(self):
        if self._task:
            self._task.cancel()
        for s in self._servers:
            s.close()
        if self.sock.exists():
            os.unlink(self.sock)

    async def _physics(self):
        dt = 0.02
        while True:
            await asyncio.sleep(dt)
            if self.paused:
                continue
            self.sim_t += dt
            vx, vy, vyaw = self.cmd if time.monotonic() - self.cmd_at < 0.5 and self.enabled else (0, 0, 0)
            c, s = math.cos(self.yaw), math.sin(self.yaw)
            self.x += (c * vx - s * vy) * dt
            self.y += (s * vx + c * vy) * dt
            self.yaw = (self.yaw + vyaw * dt + math.pi) % (2 * math.pi) - math.pi

    # ── fake robotd ───────────────────────────────────────────────────────

    def _state(self) -> dict:
        live = time.monotonic() - self.cmd_at < 0.5
        req = list(self.cmd) if live else [0.0, 0.0, 0.0]
        return dict(
            t=time.monotonic() - self.t0, move=dict(requested=req, applied=req if self.enabled else [0, 0, 0],
                                                    limited_by=[] if live else ["deadman"]),
            head=[h + o for h, o in zip(self.head, self.idle_offset)], policy=(self.skill[0] if self.skill and time.monotonic() < self.skill[1] else
                                       "MOCK" if self.enabled else "held"),
            safety=dict(fallen=False, limp=False, gravity=[0, 0, -1], gain=200, picked_up=False),
            loop=dict(hz=50.0, missed=0), joints=[0.0] * JOINTS, targets=[0.0] * JOINTS,
            odom=dict(position=[self.x, self.y, 0.12], yaw=self.yaw), imu=dict(gyro=[0, 0, 0], quat=[1, 0, 0, 0]),
            mock=True,
        )

    async def _robotd(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                msg = json.loads(line)
                method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
                if method == "robot.move":
                    self.cmd, self.cmd_at = (params.get("vx", 0), params.get("vy", 0), params.get("vyaw", 0)), time.monotonic()
                    continue
                if method == "robot.head":
                    self.head = [params.get(k, 0.0) for k in ("neck_pitch", "head_pitch", "head_yaw", "head_roll")]
                    continue
                if mid is None:
                    continue
                if method == "robot.subscribe":
                    ack = dict(accepted=True, walk="MOCK (no policy)", sitstand="MOCK (no policy)", skills=["roulade"], mock=True)
                    writer.write((json.dumps({"jsonrpc": "2.0", "id": mid, "result": ack}) + "\n").encode())
                    period = 1 / (params.get("hz") or 25)
                    while True:
                        writer.write((json.dumps({"jsonrpc": "2.0", "method": "robot.state", "params": self._state()})
                                      + "\n").encode())
                        await writer.drain()
                        await asyncio.sleep(period)
                result = {"accepted": True}
                if method == "robot.enable":
                    self.enabled = bool(params.get("on"))
                    result["reason"] = "MOCK: enabled" if self.enabled else "MOCK: disabled"
                elif method == "robot.look":
                    x, y, z = params.get("x", 1), params.get("y", 0), params.get("z", 0.13)
                    self.head = [0.0, -math.atan2(z - 0.13, x), math.atan2(y, x), 0.0]
                    result = dict(head=dict(zip(("neck_pitch", "head_pitch", "head_yaw", "head_roll"), self.head)),
                                  clamped=False)
                elif method == "robot.do":
                    name = {"sit_toggle": "sit"}.get(params.get("skill"), params.get("skill"))  # robotd's frame label
                    self.skill = (name, time.monotonic() + 1.5)
                elif method == "robot.stop":
                    self.cmd = (0, 0, 0)
                elif method == "robot.policies":
                    result = dict(enabled=self.enabled, sitting=False, mode="walk", slots=[], skills=["roulade"], mock=True)
                elif method == "robot.health":
                    result = dict(healthy=True, reason="MOCK robot")
                writer.write((json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    # ── fake simulator ────────────────────────────────────────────────────

    def _model(self) -> dict:
        box = lambda i, name, body, size, pos, rgba: dict(id=i, name=name, body=body, type=6, size=size, pos=pos,  # noqa: E731
                                                           quat=[1, 0, 0, 0], rgba=rgba, group=2, mesh=-1,
                                                           material=-1, collides=True)
        return dict(
            protocol="xbody/1", mock=True, timestep=0.005, joint_names=[f"j{i}" for i in range(JOINTS)],
            env=dict(name="mock", title="MOCK — not the Microduck", description="Three boxes and a plane.",
                     original=False, bounds=[-3, 3, -3, 3], look=dict(sky="#3a2a2a", ground="#333", sun=[2, -2, 6]),
                     layouts=["default"], layout="default", markers=[dict(name="start", x=0, y=0, kind="start"),
                                                                     dict(name="goal_1", x=1.5, y=0, kind="goal")]),
            dynamic_bodies=[1],
            bodies=[dict(id=0, name="world", parent=0, duck=None, dynamic=False, pos=[0, 0, 0], quat=[1, 0, 0, 0]),
                    dict(id=1, name="mock_trunk", parent=0, duck=0, dynamic=True, pos=[0, 0, 0.12], quat=[1, 0, 0, 0])],
            geoms=[dict(id=0, name="floor", body=0, type=0, size=[0, 0, 0.05], pos=[0, 0, 0], quat=[1, 0, 0, 0],
                        rgba=[0.3, 0.3, 0.3, 1], group=0, mesh=-1, material=-1, collides=True),
                   box(1, "mock_body", 1, [0.06, 0.05, 0.05], [0, 0, 0], [0.9, 0.3, 0.3, 1]),
                   box(2, "mock_leg_l", 1, [0.015, 0.015, 0.05], [0, 0.03, -0.08], [0.6, 0.6, 0.6, 1]),
                   box(3, "mock_leg_r", 1, [0.015, 0.015, 0.05], [0, -0.03, -0.08], [0.6, 0.6, 0.6, 1])],
            meshes=[], materials=[], textures=[], joints=[], cameras=[],
            ducks=[dict(index=0, name="duck-a", prefix="", trunk_body=1, feet={})],
            mesh_bytes=0, texture_bytes=0, source=dict(mock=True),
        )

    def _frame(self, seq: int) -> dict:
        q = [math.cos(self.yaw / 2), 0, 0, math.sin(self.yaw / 2)]
        live = time.monotonic() - self.cmd_at < 0.5 and self.enabled
        vx, vy, vyaw = self.cmd if live else (0, 0, 0)
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return dict(seq=seq, t=round(self.sim_t, 4), wall=time.time(), paused=self.paused, rtf=1.0, mock=True,
                    xpos=[self.x, self.y, 0.12], xquat=q,
                    ducks=[dict(trunk=[self.x, self.y, 0.12], quat=q, gravity=[0, 0, -1], yaw=self.yaw,
                                linvel=[c * vx - s * vy, s * vx + c * vy, 0], angvel=[0, 0, vyaw],
                                q=[0.0] * JOINTS, qd=[0.0] * JOINTS, ctrl=[0.0] * JOINTS, force=[0.0] * JOINTS,
                                torque_on=True, released=self.enabled, kp=200, feet=dict(left=True, right=True),
                                external_contacts=2)],
                    contacts=[])

    async def _xbody(self, reader, writer):
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                req = json.loads(line)
                op = req.get("op")
                if op == "subscribe":
                    seq = 0
                    period = 1 / min(60, req.get("hz", 30))
                    while True:
                        seq += 1
                        writer.write((json.dumps(self._frame(seq)) + "\n").encode())
                        await writer.drain()
                        await asyncio.sleep(period)
                if op == "model":
                    writer.write((json.dumps(self._model()) + "\n").encode())
                    await writer.drain()
                    continue
                answer = {"ok": True}
                if op == "hello":
                    answer = dict(protocol="xbody/1", env="mock", opts={}, ducks=1, mock=True)
                elif op == "pause":
                    self.paused = True
                elif op == "resume":
                    self.paused = False
                elif op == "reset":
                    self.x = self.y = self.yaw = 0.0
                elif op == "environments":
                    answer = dict(current="mock", opts={}, environments=[self._model()["env"]])
                elif op == "push":
                    pass
                writer.write((json.dumps(answer) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
