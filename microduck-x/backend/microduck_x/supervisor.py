"""Owns the MuJoCo body process when `MDX_MANAGE_SIM=1`, so a scene switch can restart it.

robotd is *not* managed here: it is the robot's own daemon and survives a body restart
(`duck_control::sim::RemoteIo` reconnects on the next tick). Only the body is restarted.
Pidfile in the shared state directory, identity checked through /proc before any signal —
the same rule `scripts/duck-sim` follows after it once killed a login session by pid reuse.
"""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from .config import ROOT, Settings
from .events import EventBus


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


class SimSupervisor:
    def __init__(self, settings: Settings, events: EventBus):
        self.s = settings
        self.events = events
        self.pidfile = settings.state_dir / "x-body.pid"
        self.logfile = settings.state_dir / "x-body.log"
        self.env = settings.initial_env
        self.layout = settings.initial_layout or None
        self._lock = asyncio.Lock()

    @property
    def python(self) -> Path:
        return self.s.rl_dir / ".venv" / "bin" / "python"

    def _argv(self, env: str, layout: str | None) -> list[str]:
        argv = [str(self.python), "-I", str(ROOT / "sim" / "xbody.py"), "--env", env,
                "--port", str(self.s.body_port), "--state-port", str(self.s.xbody_state_port),
                "--keyframe", self.s.keyframe]
        if layout:
            argv += ["--layout", layout]
        if self.s.native_viewer:
            argv.append("--viewer")
        return argv

    def running_pid(self) -> int | None:
        try:
            pid = int(self.pidfile.read_text().strip())
        except (OSError, ValueError):
            return None
        return pid if "xbody.py" in _cmdline(pid) and f"--state-port {self.s.xbody_state_port}" in _cmdline(pid) else None

    def stop_sync(self) -> None:
        pid = self.running_pid()
        self.pidfile.unlink(missing_ok=True)
        if pid is None:
            return
        os.kill(pid, signal.SIGTERM)
        for _ in range(25):
            if not _cmdline(pid):
                return
            time.sleep(0.2)
        os.kill(pid, signal.SIGKILL)

    def start_sync(self, env: str, layout: str | None) -> None:
        if not self.python.exists():
            raise RuntimeError(f"no simulator venv at {self.python} — run microduck-x/scripts/x-setup.sh")
        if _port_open(self.s.xbody_state_port) or _port_open(self.s.body_port):
            raise RuntimeError(f"ports {self.s.body_port}/{self.s.xbody_state_port} are taken by a process "
                               "this dashboard did not start (scripts/duck-sim? run its `down`)")
        self.s.state_dir.mkdir(parents=True, exist_ok=True)
        log = open(self.logfile, "ab")
        envvars = dict(os.environ, DUCK_SIM_RL=str(self.s.rl_dir))
        proc = subprocess.Popen(self._argv(env, layout), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                env=envvars, start_new_session=True)
        self.pidfile.write_text(str(proc.pid))
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError(f"the simulator exited ({proc.returncode}); see {self.logfile}")
            if _port_open(self.s.xbody_state_port) and _port_open(self.s.body_port):
                self.env, self.layout = env, layout
                return
            time.sleep(0.15)
        raise RuntimeError(f"the simulator did not open its ports in 15 s; see {self.logfile}")

    async def restart(self, env: str, layout: str | None) -> None:
        async with self._lock:
            self.events.emit("sim.restart", f"restarting the MuJoCo body in '{env}'", env=env, layout=layout)
            await asyncio.to_thread(self.stop_sync)
            # The ports must actually be free before the new body binds them.
            for _ in range(30):
                if not _port_open(self.s.xbody_state_port) and not _port_open(self.s.body_port):
                    break
                await asyncio.sleep(0.1)
            await asyncio.to_thread(self.start_sync, env, layout)

    async def ensure_started(self) -> bool:
        if self.running_pid() or _port_open(self.s.xbody_state_port):
            return False
        await asyncio.to_thread(self.start_sync, self.env, self.layout)
        return True
