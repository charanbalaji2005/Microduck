"""Against the real thing: microduck_rl's body server (through xbody) and a real `robotd --sim`.

Opt in with MDX_IT=1. Needs DUCK_SIM_RL (with its .venv) and `cargo build -p robotd`.

    MDX_IT=1 DUCK_SIM_RL=~/Pollen/microduck_rl .venv/bin/python -m pytest -q -m integration

Uses its own ports (17801, 17811) and a temporary state directory, so it can run beside a live
x-start.sh stack. The last test loads robotd's *contract fixture* — an untrained ONNX that
passes the ONNX shape gate and nothing else — to prove the actuation path end to end
(robot.enable → torque on → MuJoCo moves). The duck falls over with it, as it should: it is not a gait.
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from microduck_x.robotd import RobotdClient

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
RL = Path(os.environ.get("DUCK_SIM_RL", "~/Pollen/microduck_rl")).expanduser()
ROBOTD = REPO / "target" / "debug" / "robotd"
BODY, STATE = 17801, 17811

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("MDX_IT") != "1", reason="set MDX_IT=1 to run against the real stack"),
]


def ort() -> str:
    found = list((RL / ".venv" / "lib").glob("python*/site-packages/onnxruntime/capi/libonnxruntime.so*"))
    return str(found[0]) if found else ""


def op(port, request, read_blob=False):
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    f = s.makefile("rwb")
    f.write((json.dumps(request) + "\n").encode()); f.flush()
    answer = json.loads(f.readline())
    blob = f.read(answer["mesh_bytes"] + answer["texture_bytes"]) if read_blob else None
    s.close()
    return (answer, blob) if read_blob else answer


def wait_port(port, proc, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"process exited: {proc.stdout.read().decode()[-2000:]}")
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise TimeoutError(port)


@pytest.fixture
def body(request):
    env = getattr(request, "param", "arena")
    proc = subprocess.Popen([str(RL / ".venv" / "bin" / "python"), "-I", str(ROOT / "sim" / "xbody.py"), "--env", env,
                             "--port", str(BODY), "--state-port", str(STATE)],
                            env=dict(os.environ, DUCK_SIM_RL=str(RL)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    wait_port(STATE, proc)
    wait_port(BODY, proc)
    yield proc
    proc.terminate(); proc.wait(5)


def start_robotd(tmp_path, params: str):
    (tmp_path / "robotd.toml").write_text(params)
    sock = tmp_path / "duck.sock"
    proc = subprocess.Popen([str(ROBOTD), "--sim", f"127.0.0.1:{BODY}", "--params", str(tmp_path / "robotd.toml"),
                             "--socket", str(sock)],
                            env=dict(os.environ, DUCK_RUNTIME_DIR=str(tmp_path), DUCK_IDENTITY="duck-it", ORT_DYLIB_PATH=ort(),
                                     RUST_LOG="warn"), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(100):
        if sock.exists():
            break
        time.sleep(0.1)
    return proc, sock


@pytest.mark.parametrize("body", ["bare", "apartment", "lab", "garden", "arena"], indirect=True)
def test_every_scene_serves_the_original_robot(body):
    hello = op(STATE, {"op": "hello"})
    assert hello["protocol"] == "xbody/1" and hello["robot_protocol"] == 1
    model, blob = op(STATE, {"op": "model"}, read_blob=True)
    assert len(blob) == model["mesh_bytes"] + model["texture_bytes"]
    names = {j["name"] for j in model["joints"]}
    wire = set(model["joint_names"]) - {"mouth"}
    assert wire <= names, wire - names  # the 14 actuated joints of the original model, by name
    assert model["ducks"][0]["trunk_body"] >= 0
    # The robot-facing port speaks the original protocol unchanged.
    s = socket.create_connection(("127.0.0.1", BODY), timeout=5); f = s.makefile("rw")
    f.write(json.dumps({"op": "hello", "protocol": 1, "joints": 15}) + "\n"); f.flush()
    assert json.loads(f.readline()) == {"protocol": 1}
    f.write('{"op":"read"}\n'); f.flush()
    r = json.loads(f.readline())
    assert len(r["positions"]) == 15 and set(r["imu"]) == {"gyro", "gravity", "quat"}
    s.close()


def test_state_stream_pause_reset_push(body):
    s = socket.create_connection(("127.0.0.1", STATE), timeout=5); f = s.makefile("rwb")
    f.write(b'{"op":"subscribe","hz":30}\n'); f.flush()
    a = json.loads(f.readline()); b = json.loads(f.readline())
    assert b["seq"] == a["seq"] + 1 and b["t"] >= a["t"]
    assert op(STATE, {"op": "pause"})["paused"] is True
    time.sleep(0.3); t1 = json.loads(f.readline())["t"]; time.sleep(0.3)
    for _ in range(5):
        t2 = json.loads(f.readline())["t"]
    assert t2 == t1  # frozen
    op(STATE, {"op": "resume"})
    assert "error" in op(STATE, {"op": "push", "force": [500, 0, 0]})  # refused: above 20 N
    assert op(STATE, {"op": "reset", "keyframe": "SIT"})["ok"]
    s.close()


async def test_robotd_reflects_intents_but_no_policy_means_no_driving(body, tmp_path):
    proc, sock = start_robotd(tmp_path, f'[policy]\nenabled = true\nwalk = "{tmp_path}/absent.onnx"\n')
    try:
        client = RobotdClient(sock)
        states = []
        task = asyncio.create_task(client.stream_states(25, states.append))
        for _ in range(50):
            if states:
                break
            await asyncio.sleep(0.1)
        assert client.ack and client.ack.get("unavailable")  # robotd says the gait would not load
        for _ in range(10):
            await client.move(0.15, 0, 0.2)
            await asyncio.sleep(0.05)
        assert states[-1]["move"]["requested"] == [0.15, 0.0, 0.2]  # the intent arrived
        assert states[-1]["policy"] == "held"  # and nothing is driving: honest, not walking
        enable = await client.call("robot.enable", {"on": True})
        assert enable["accepted"]  # accepted …
        await asyncio.sleep(0.5)
        assert states[-1]["policy"] == "held"  # … but still not driving
        task.cancel()
    finally:
        proc.terminate(); proc.wait(5)


async def test_fixture_policy_actuates_mujoco(body, tmp_path):
    fixture = REPO / "duck-control" / "tests" / "fixtures" / "feedforward.onnx"
    params = (f'[policy]\nenabled = true\nwalk = "{fixture}"\nsitstand = "none"\nground_pick = "none"\n'
              'kick_left = "none"\nkick_right = "none"\nroulade = "none"\n')
    proc, sock = start_robotd(tmp_path, params)
    try:
        client = RobotdClient(sock)
        before = json.loads(_one_frame())["ducks"][0]
        assert before["released"] is False  # the body holds the duck until robotd enables torque
        assert (await client.call("robot.enable", {"on": True}))["accepted"]
        await asyncio.sleep(4.0)
        after = json.loads(_one_frame())["ducks"][0]
        assert after["torque_on"] and after["released"]  # robotd switched the torque on over the TCP seam
        moved = max(abs(a - b) for a, b in zip(after["q"], before["q"]) if a is not None and b is not None)
        assert moved > 0.05  # the joints moved under robotd's targets, in MuJoCo
    finally:
        proc.terminate(); proc.wait(5)


def _one_frame() -> str:
    s = socket.create_connection(("127.0.0.1", STATE), timeout=5); f = s.makefile("rwb")
    f.write(b'{"op":"subscribe","hz":30}\n'); f.flush()
    line = f.readline().decode()
    s.close()
    return line


async def test_body_restart_needs_relax_then_enable(tmp_path):
    """Finding, verified: robotd does not replay torque when RemoteIo reconnects to a new body.

    A restarted body holds the duck (`released` false) until it sees a torque op, and a plain
    `robot.enable` on an already-enabled robotd does not send one. `robot.relax` then
    `robot.enable` does. Microduck X's scene switch relies on exactly this sequence.
    """
    def start_body():
        p = subprocess.Popen([str(RL / ".venv" / "bin" / "python"), "-I", str(ROOT / "sim" / "xbody.py"), "--env", "bare",
                              "--port", str(BODY), "--state-port", str(STATE)],
                             env=dict(os.environ, DUCK_SIM_RL=str(RL)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        wait_port(STATE, p); wait_port(BODY, p)
        return p

    fixture = REPO / "duck-control" / "tests" / "fixtures" / "feedforward.onnx"
    params = (f'[policy]\nenabled = true\nwalk = "{fixture}"\nsitstand = "none"\nground_pick = "none"\n'
              'kick_left = "none"\nkick_right = "none"\nroulade = "none"\n')
    body = start_body()
    proc, sock = start_robotd(tmp_path, params)
    client = RobotdClient(sock)
    duck = lambda: json.loads(_one_frame())["ducks"][0]  # noqa: E731
    try:
        await client.call("robot.enable", {"on": True})
        await asyncio.sleep(3)
        assert duck()["released"]
        body.terminate(); body.wait(5)
        body = start_body()
        await asyncio.sleep(1.5)
        assert not duck()["released"]
        await client.call("robot.enable", {"on": True})
        await asyncio.sleep(1.5)
        assert not duck()["released"]  # plain enable: still held
        await client.call("robot.relax")
        await asyncio.sleep(0.5)
        await client.call("robot.enable", {"on": True})
        await asyncio.sleep(3)
        assert duck()["released"]  # relax → enable re-arms torque
    finally:
        proc.terminate(); proc.wait(5)
        body.terminate(); body.wait(5)
