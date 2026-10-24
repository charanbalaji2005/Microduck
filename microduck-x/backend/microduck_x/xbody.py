"""Client for the xbody state port (`microduck-x/sim/xbody.py`): ground truth and sim admin."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable


class SimUnavailable(ConnectionError):
    pass


class XbodyClient:
    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.connected = False
        self.hello: dict | None = None
        self.last_frame: dict | None = None
        self.last_frame_at = 0.0
        self.frames_received = 0
        self.last_error: str | None = None
        self._model: tuple[dict, bytes, bytes] | None = None
        self._model_key: str | None = None
        self._model_lock = asyncio.Lock()

    async def _open(self, timeout: float = 1.0):
        try:
            return await asyncio.wait_for(asyncio.open_connection(self.host, self.port, limit=2**24), timeout)
        except (OSError, asyncio.TimeoutError) as error:
            raise SimUnavailable(f"no simulator state port at {self.host}:{self.port}: {error}") from error

    async def op(self, request: dict, timeout: float = 5.0) -> dict:
        reader, writer = await self._open()
        try:
            writer.write((json.dumps(request) + "\n").encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout)
            if not line:
                raise SimUnavailable("simulator closed the connection")
            answer = json.loads(line)
            if "error" in answer:
                raise RuntimeError(f"simulator refused {request.get('op')}: {answer['error']}")
            return answer
        except asyncio.TimeoutError as error:
            raise SimUnavailable(f"simulator did not answer {request.get('op')}") from error
        finally:
            writer.close()

    async def model(self) -> tuple[dict, bytes, bytes]:
        """The compiled model (header, mesh blob, texture blob), cached per environment."""
        async with self._model_lock:
            hello = await self.op({"op": "hello"})
            key = json.dumps([hello.get("env"), hello.get("opts")], sort_keys=True)
            if self._model is not None and key == self._model_key:
                return self._model
            reader, writer = await self._open()
            try:
                writer.write(b'{"op":"model"}\n')
                await writer.drain()
                header = json.loads(await asyncio.wait_for(reader.readline(), 30.0))
                mesh = await asyncio.wait_for(reader.readexactly(header["mesh_bytes"]), 60.0)
                tex = await asyncio.wait_for(reader.readexactly(header["texture_bytes"]), 60.0)
            finally:
                writer.close()
            self._model, self._model_key = (header, mesh, tex), key
            return self._model

    def invalidate_model(self) -> None:
        self._model, self._model_key = None, None

    async def stream(self, hz: int, on_frame: Callable[[dict], Awaitable[None] | None],
                     on_status: Callable[[bool, str | None], None] | None = None) -> None:
        backoff = 0.25
        while True:
            try:
                self.hello = await self.op({"op": "hello"})
                reader, writer = await self._open()
                writer.write((json.dumps({"op": "subscribe", "hz": hz}) + "\n").encode())
                await writer.drain()
                self.connected, self.last_error, backoff = True, None, 0.25
                if on_status:
                    on_status(True, None)
                while True:
                    line = await asyncio.wait_for(reader.readline(), 3.0)
                    if not line:
                        raise SimUnavailable("state stream closed")
                    frame = json.loads(line)
                    self.last_frame, self.last_frame_at = frame, time.monotonic()
                    self.frames_received += 1
                    result = on_frame(frame)
                    if asyncio.iscoroutine(result):
                        await result
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001
                was = self.connected
                self.connected, self.last_error = False, str(error)
                if on_status and was:
                    on_status(False, str(error))
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 2.0)

    def frame_age(self) -> float:
        return time.monotonic() - self.last_frame_at if self.last_frame_at else float("inf")
