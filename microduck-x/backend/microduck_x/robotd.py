"""The one door to the robot: robotd's JSON-RPC 2.0 over its Unix socket.

Verified against `duck-ipc-proto` (API_VERSION 44) and a running `robotd --sim`:

* **Discrete intents are requests** and answer `{"accepted": bool, "reason"?: str}` —
  `robot.enable {on}`, `robot.stop`, `robot.do {skill}`, `robot.relax`, `robot.init`.
* **Continuous intents are notifications** (no `id`, no reply), last-writer-wins, and expire after
  `[safety] deadman_ms` (500 ms by default) — `robot.move {vx, vy, vyaw}`, `robot.head`, `robot.pose`.
  So a move must be *resent* (here at 20 Hz) for as long as the robot should keep moving, and a
  client that dies stops the robot by construction.
* **`robot.subscribe {hz}`** answers an acknowledgement naming the loaded policies and skills,
  then turns the connection into a stream of `robot.state` notifications.

Nothing here invents a method. Anything not listed in `ALLOWED` is refused before it is sent.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

ALLOWED_REQUESTS = {
    "robot.health", "robot.enable", "robot.stop", "robot.do", "robot.relax", "robot.init",
    "robot.policies", "robot.model", "robot.mode", "robot.look", "robot.sound", "robot.subscribe",
    "system.info",
}
ALLOWED_NOTIFICATIONS = {"robot.move", "robot.head", "robot.pose"}


class RobotdUnavailable(ConnectionError):
    pass


class RpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"robotd error {code}: {message}")
        self.code, self.message, self.data = code, message, data


class RobotdClient:
    def __init__(self, socket_path: Path):
        self.socket_path = Path(socket_path)
        self._ids = itertools.count(1)
        self._intent: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        self._intent_lock = asyncio.Lock()
        self.connected = False
        self.ack: dict | None = None  # robot.subscribe's acknowledgement
        self.last_state: dict | None = None
        self.last_state_at = 0.0
        self.states_received = 0
        self.last_error: str | None = None

    async def _open(self, timeout: float = 1.0):
        if not self.socket_path.exists():
            raise RobotdUnavailable(f"no robotd socket at {self.socket_path} — is the simulator up?")
        try:
            return await asyncio.wait_for(asyncio.open_unix_connection(str(self.socket_path), limit=2**22), timeout)
        except (OSError, asyncio.TimeoutError) as error:
            raise RobotdUnavailable(f"robotd at {self.socket_path} refused: {error}") from error

    async def call(self, method: str, params: dict | None = None, timeout: float = 3.0) -> Any:
        if method not in ALLOWED_REQUESTS:
            raise PermissionError(f"{method} is not an allowlisted robotd request")
        reader, writer = await self._open()
        try:
            msg: dict[str, Any] = {"jsonrpc": "2.0", "id": next(self._ids), "method": method}
            if params is not None:
                msg["params"] = params
            writer.write((json.dumps(msg) + "\n").encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout)
            if not line:
                raise RobotdUnavailable(f"robotd closed the connection during {method}")
            answer = json.loads(line)
            if "error" in answer:
                err = answer["error"]
                raise RpcError(err.get("code", 0), err.get("message", ""), err.get("data"))
            return answer.get("result")
        except asyncio.TimeoutError as error:
            raise RobotdUnavailable(f"robotd did not answer {method} within {timeout}s") from error
        finally:
            writer.close()

    async def notify(self, method: str, params: dict) -> None:
        """Send a continuous intent on a persistent connection, reconnecting once if it broke."""
        if method not in ALLOWED_NOTIFICATIONS:
            raise PermissionError(f"{method} is not an allowlisted robotd notification")
        payload = (json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n").encode()
        async with self._intent_lock:
            for attempt in range(2):
                if self._intent is None:
                    self._intent = await self._open()
                _, writer = self._intent
                try:
                    writer.write(payload)
                    await writer.drain()
                    return
                except (ConnectionError, OSError):
                    self._intent = None
                    if attempt:
                        raise RobotdUnavailable("robotd dropped the intent connection")

    async def move(self, vx: float, vy: float, vyaw: float) -> None:
        await self.notify("robot.move", {"vx": float(vx), "vy": float(vy), "vyaw": float(vyaw)})

    async def stream_states(self, hz: int, on_state: Callable[[dict], Awaitable[None] | None],
                            on_status: Callable[[bool, str | None], None] | None = None) -> None:
        """Hold a `robot.subscribe` open forever, reconnecting with backoff."""
        backoff = 0.25
        while True:
            try:
                reader, writer = await self._open()
                writer.write((json.dumps({"jsonrpc": "2.0", "id": next(self._ids), "method": "robot.subscribe",
                                          "params": {"hz": hz}}) + "\n").encode())
                await writer.drain()
                first = json.loads(await asyncio.wait_for(reader.readline(), 3.0))
                self.ack = first.get("result")
                self.connected, self.last_error, backoff = True, None, 0.25
                if on_status:
                    on_status(True, None)
                while True:
                    line = await asyncio.wait_for(reader.readline(), 3.0)
                    if not line:
                        raise RobotdUnavailable("robotd closed the state stream")
                    msg = json.loads(line)
                    if msg.get("method") == "robot.state":
                        self.last_state, self.last_state_at = msg["params"], time.monotonic()
                        self.states_received += 1
                        result = on_state(msg["params"])
                        if asyncio.iscoroutine(result):
                            await result
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 — any failure means "reconnect"
                was = self.connected
                self.connected, self.last_error = False, str(error)
                if on_status and was:
                    on_status(False, str(error))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 3.0)

    def state_age(self) -> float:
        return time.monotonic() - self.last_state_at if self.last_state_at else float("inf")

    @property
    def socket_present(self) -> bool:
        return os.path.exists(self.socket_path)
