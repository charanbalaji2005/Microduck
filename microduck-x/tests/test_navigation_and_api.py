"""Planner on synthetic geometry, the experiment evaluator, and the HTTP API surface."""
import math

import pytest
from fastapi.testclient import TestClient

from microduck_x.app import create_app
from microduck_x.experiments import Criteria, evaluate, metrics
from microduck_x.navigation import Grid, PlanError, heading_command, obstacles_from_model, plan


def header_with_box(x, y, sx, sy, sz, collides=True):
    return dict(
        bodies=[dict(id=0, name="world", parent=0, duck=None, dynamic=False, pos=[0, 0, 0], quat=[1, 0, 0, 0])],
        geoms=[dict(id=0, name="floor", body=0, type=0, size=[0, 0, 0.05], pos=[0, 0, 0], quat=[1, 0, 0, 0], collides=True, mesh=-1),
               dict(id=1, name="wall", body=0, type=6, size=[sx, sy, sz], pos=[x, y, sz], quat=[1, 0, 0, 0], collides=collides, mesh=-1)],
        dynamic_bodies=[], meshes=[],
    )


def test_obstacles_respect_height_band_and_collision():
    assert len(obstacles_from_model(header_with_box(1, 0, 0.1, 1, 0.2), None)) == 1
    assert obstacles_from_model(header_with_box(1, 0, 0.1, 1, 0.2, collides=False), None) == []  # decal
    assert obstacles_from_model(header_with_box(1, 0, 0.1, 1, 0.004), None) == []  # 8 mm plate is walkable


def test_plan_goes_around_a_wall():
    obs = obstacles_from_model(header_with_box(1.0, 0.0, 0.05, 0.8, 0.2), None)
    grid = Grid((-2, 3, -2, 2), obs)
    path = plan(grid, (0, 0), (2, 0))
    assert path[-1] == (2, 0)
    # every leg is collision-free, and the route leaves the straight line to get past the wall
    assert max(abs(y) for _, y in path) > 0.8
    pts = [(0, 0)] + path
    for a, b in zip(pts, pts[1:]):
        assert grid.line_free(grid.cell(*a), grid.cell(*b))


def test_plan_refuses_goal_inside_obstacle():
    obs = obstacles_from_model(header_with_box(1.0, 0.0, 0.6, 0.6, 0.2), None)
    with pytest.raises(PlanError):
        plan(Grid((-2, 3, -2, 2), obs), (-1.5, 0), (1.0, 0.0))


def test_heading_controller_turns_in_place_first():
    vx, vyaw, _ = heading_command((0, 0, 0), (0, 1), 0.2, final=True)
    assert vx == 0 and vyaw > 0
    vx, vyaw, _ = heading_command((0, 0, 0), (1, 0.01), 0.2, final=False)
    assert vx > 0.15 and abs(vyaw) < 0.1


def test_evaluate_never_passes_on_acceptance_alone():
    m = metrics([], None, {}, None, 0, 1.0)
    ok, _, failed = evaluate(Criteria(min_forward_m=0.5), m, [])
    assert not ok and any("not measurable" in f for f in failed)


def test_evaluate_on_measured_samples():
    samples = [dict(t=i, x=0.1 * i, y=0, z=0.12, yaw=0.0, tilt=3.0, v_fwd=0.1) for i in range(8)]
    m = metrics(samples, samples[0], {"goal_1": (0.7, 0.0)}, "goal_1", 0, 8.0)
    ok, passed, failed = evaluate(Criteria(min_forward_m=0.6, max_lateral_m=0.1, goal="goal_1"), m, [])
    assert ok, failed
    assert m["forward_m"] == pytest.approx(0.7) and m["goal_error_m"] == pytest.approx(0.0)
    ok, _, failed = evaluate(Criteria(min_forward_m=1.0), m, [])
    assert not ok


def test_api_with_mock(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        import time
        for _ in range(60):
            if client.get("/api/health").json()["robotd"]:
                break
            time.sleep(0.1)
        st = client.get("/api/status").json()
        assert st["mock"] is True and st["config"]["local_only"] is True
        r = client.post("/api/text", json={"text": "jump over the moon"}).json()
        assert r["commands"] == [] and r["parse"]["errors"]
        r = client.post("/api/command", json={"command": {"action": "walk", "vx": 5}})
        assert r.status_code == 422
        r = client.post("/api/command", json={"command": {"action": "enable"}, "wait": True}).json()
        assert r["status"] == "completed"
        r = client.post("/api/text", json={"text": "walk forward for one second"}).json()
        assert r["executions"][0]["action"] == "walk"
        assert client.post("/api/estop").json()["latched"]
        assert client.get("/api/status").json()["safety"]["estop"] is True
        client.post("/api/estop/clear")
        idle = client.post("/api/chat/idle", json={"scenery": "beach", "cousins": ["Pip"]}).json()
        assert idle["replies"][0]["idle"] is True and idle["replies"][0]["text"]
        assert not [c for c in client.get("/api/commands").json() if c.get("source") == "chat"], "idle talk ran a command"
        model = client.get("/api/model").json()
        assert model["mock"] is True
        assert client.get("/api/voice/status").json()["engine"] is None
        with client.websocket_connect("/ws") as ws:
            hello = ws.receive_json()
            assert hello["type"] == "hello"
            kinds = {ws.receive_json()["type"] for _ in range(10)}
            assert "sim" in kinds or "robot" in kinds


def test_remote_bind_requires_token(tmp_path):
    from microduck_x.config import Settings
    s = Settings(state_dir=tmp_path, host="0.0.0.0")
    assert s.validate()
    s = Settings(state_dir=tmp_path, host="0.0.0.0", token="x")
    assert not s.validate()
