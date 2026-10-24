"""Executor + safety + robotd client against the MOCK robotd/simulator (not the Microduck)."""
import asyncio
import json
import math

import pytest

from microduck_x.commands import Envelope
from conftest import wait_terminal


def env(cmd, source="api"):
    return Envelope.model_validate({"command": cmd, "source": source})


async def enable(svc):
    ex = await wait_terminal(await svc.executor.submit(env({"action": "enable"})))
    assert ex.status == "completed", ex.message
    return ex


def moving(svc) -> bool:
    """Is anything still telling the (mock) robot to move?"""
    running = [e for e in svc.executor.history if e.status == "running"]
    req = (svc.hub.robot or {}).get("move", {}).get("requested", [0, 0, 0])
    return bool(running) or any(abs(v) > 1e-9 for v in req)


async def test_motion_refused_until_a_policy_drives(svc):
    ex = await svc.executor.submit(env({"action": "walk", "vx": 0.2, "duration_s": 1}))
    assert ex.status == "rejected"
    assert "no policy is driving" in ex.message


async def test_enable_then_walk_is_measured(svc):
    ex = await enable(svc)
    assert "driving" in ex.message
    ex = await svc.executor.submit(env({"action": "walk", "vx": 0.2, "duration_s": 1.5}))
    await wait_terminal(ex)
    assert ex.status == "completed", ex.message
    assert ex.outcome["acknowledged"] is True  # robot.state reflected the intent
    # 0.2 m/s is inside the gait's dead band (velstand stands still): raised to the 0.28 floor
    assert ex.outcome["sent"] == pytest.approx([0.28, 0, 0])
    assert ex.outcome["forward_m"] == pytest.approx(0.42, abs=0.08)  # measured, not assumed
    assert ex.outcome["pose_source"] == "simulator ground truth"


async def test_turn_by_angle_closes_the_loop(svc):
    await enable(svc)
    ex = await svc.executor.submit(env({"action": "turn", "vyaw": 0.8, "angle_deg": 90}))
    await wait_terminal(ex)
    assert ex.status == "completed", ex.message
    assert ex.outcome["turned_deg"] == pytest.approx(90, abs=6)


async def test_new_motion_supersedes(svc):
    await enable(svc)
    a = await svc.executor.submit(env({"action": "walk", "vx": 0.2, "duration_s": 5}))
    await asyncio.sleep(0.3)
    b = await svc.executor.submit(env({"action": "walk", "vx": -0.1, "duration_s": 0.6}))
    await wait_terminal(b)
    assert a.status == "superseded"
    assert b.status == "completed"


async def test_estop_latches_and_blocks(svc):
    await enable(svc)
    walking = await svc.executor.submit(env({"action": "walk", "vx": 0.2, "duration_s": 5}))
    await asyncio.sleep(0.3)
    result = await svc.executor.estop("test")
    assert result["latched"] and result["robot_stop"]["accepted"]
    assert walking.status == "cancelled"
    ex = await svc.executor.submit(env({"action": "walk", "vx": 0.2, "duration_s": 1}))
    assert ex.status == "rejected" and "emergency stop" in ex.message
    svc.safety.clear()
    ex = await svc.executor.submit(env({"action": "walk", "vx": 0.2, "duration_s": 0.5}))
    await wait_terminal(ex)
    assert ex.status == "completed"


async def test_stale_telemetry_blocks_motion(svc):
    await enable(svc)
    svc.hub.robot_at -= 5  # pretend the stream stopped 5 s ago
    assert any("stale" in b for b in svc.safety.motion_blockers())


async def test_unknown_skill_is_rejected_with_the_real_list(svc):
    await enable(svc)
    ex = await svc.executor.submit(env({"action": "skill", "skill": "backflip"}))
    await wait_terminal(ex)
    assert ex.status == "rejected" and "roulade" in ex.message


async def test_scene_switch_needs_a_managed_simulator(svc):
    ex = await wait_terminal(await svc.executor.submit(env({"action": "set_environment", "environment": "lab"})))
    assert ex.status == "rejected" and "does not manage" in ex.message


async def test_goto_plans_and_arrives(svc):
    await enable(svc)
    ex = await svc.executor.submit(env({"action": "goto", "target": "goal_1", "speed": 0.3}))
    await wait_terminal(ex, timeout=40)
    assert ex.status == "completed", ex.message
    assert ex.outcome["error_m"] < 0.2
    assert svc.executor.last_plan["goal"] == "goal_1"


async def test_bad_marker(svc):
    await enable(svc)
    ex = await svc.executor.submit(env({"action": "goto", "target": "kitchen"}))
    await wait_terminal(ex)
    assert ex.status == "rejected" and "goal_1" in ex.message


async def test_teleop_and_deadman(svc):
    watchdog = asyncio.create_task(svc.executor.teleop_watchdog())
    res = await svc.executor.teleop(0.2, 0, 0)
    assert not res["ok"]  # no policy yet
    await enable(svc)
    assert (await svc.executor.teleop(0.2, 0, 0))["ok"]
    await asyncio.sleep(0.8)  # no more teleop messages → the dashboard's deadman stops it
    assert not svc.executor._teleop_active
    watchdog.cancel()


async def test_sequence_runs_in_order(svc):
    await enable(svc)
    envs = [env({"action": "walk", "vx": 0.2, "duration_s": 0.5}), env({"action": "turn", "vyaw": 0.8, "angle_deg": 45}),
            env({"action": "stop"})]
    svc.executor.submit_sequence(envs)
    for _ in range(200):
        done = [e for e in svc.executor.history if e.id in {x.id for x in envs} and e.status == "completed"]
        if len(done) == 3:
            break
        await asyncio.sleep(0.05)
    assert [e.action for e in done] == ["walk", "turn", "stop"]


async def test_reset_and_status(svc):
    await enable(svc)
    ex = await wait_terminal(await svc.executor.submit(env({"action": "reset"})))
    assert ex.status == "completed", ex.message
    ex = await svc.executor.submit(env({"action": "status"}))
    assert "Position (simulator ground truth)" in ex.message


async def test_experiment_record(svc, settings):
    (settings.experiments_dir / "mock_walk.json").write_text(json.dumps({
        "name": "mock_walk", "title": "mock walk", "environment": "mock", "settle_s": 5,
        "steps": [{"action": "walk", "vx": 0.2, "duration_s": 1.0}],
        "success": {"min_forward_m": 0.1, "max_lateral_m": 0.1, "upright": True}}))
    ex = await svc.executor.submit(env({"action": "run_experiment", "experiment": "mock_walk", "trials": 2}))
    await wait_terminal(ex, timeout=60)
    assert ex.status == "completed", ex.message
    run = svc.store.get(ex.outcome["run_id"])
    assert run["passed_trials"] == 2 and len(run["results"]) == 2
    t = run["results"][0]
    assert t["metrics"]["forward_m"] > 0.1 and t["samples"]
    assert run["software"]["robotd_ack"]["walk"]
    csv = svc.store.csv(run["id"])
    assert csv.splitlines()[0].startswith("trial,t,sim_t,x")
    cmp = svc.runner.compare(run, run)
    assert cmp["delta_b_minus_a"]["forward_m"] == 0


# ── races found in review (each reproduced a robot that kept moving) ─────────────


async def test_concurrent_submits_leave_no_orphan(svc):
    ex = svc.executor
    await enable(svc)
    await ex.submit(env({"action": "walk", "vx": 0.2, "duration_s": 5}))
    await asyncio.sleep(0.3)
    b, c = await asyncio.gather(ex.submit(env({"action": "walk", "vx": 0.1, "duration_s": 5})),
                                ex.submit(env({"action": "walk", "vx": -0.1, "duration_s": 5})))
    await asyncio.sleep(0.3)
    await ex.submit(env({"action": "stop"}))
    await asyncio.sleep(1.0)
    assert b.status != "running" and c.status != "running"
    assert not moving(svc)


async def test_stop_during_supersede_wins(svc):
    ex = svc.executor
    await enable(svc)
    await ex.submit(env({"action": "walk", "vx": 0.2, "duration_s": 5}))
    await asyncio.sleep(0.3)
    pending = asyncio.create_task(ex.submit(env({"action": "walk", "vx": 0.2, "duration_s": 3})))
    await asyncio.sleep(0)
    await ex.submit(env({"action": "stop"}))
    b = await pending
    await asyncio.sleep(1.0)
    assert b.status in ("cancelled", "superseded")
    assert not moving(svc)


async def test_cancel_all_ends_a_sequence(svc):
    ex = svc.executor
    await enable(svc)
    await ex.submit(env({"action": "walk", "vx": 0.2, "duration_s": 5}))
    await asyncio.sleep(0.3)
    records = ex.submit_sequence([env({"action": "walk", "vx": 0.2, "duration_s": 2}),
                                  env({"action": "turn", "vyaw": 0.5, "duration_s": 2})])
    assert all(r.status == "queued" for r in records)  # every step has a record from the start
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await ex.cancel_all("user cancel")
    await asyncio.sleep(0.8)
    assert ex._sequence.done()
    assert all(r.status in ("cancelled", "superseded") for r in records), [r.status for r in records]
    assert not moving(svc)


async def test_estop_blocks_enable_and_nan_teleop(svc):
    await enable(svc)
    await svc.executor.estop("test")
    ex = await svc.executor.submit(env({"action": "enable"}))
    assert ex.status == "rejected"
    svc.safety.clear()
    res = await svc.executor.teleop(float("nan"), 0, 0)
    assert not res["ok"]


async def test_short_walk_still_needs_acknowledgement(svc):
    await enable(svc)
    ex = await wait_terminal(await svc.executor.submit(env({"action": "walk", "vx": 0.2, "duration_s": 0.2})))
    assert ex.status == "completed" and ex.outcome["acknowledged"]


async def test_skill_needs_to_be_seen(svc):
    await enable(svc)
    ex = await wait_terminal(await svc.executor.submit(env({"action": "skill", "skill": "roulade"})))
    assert ex.status == "completed" and "roulade" in ex.outcome["policies_seen"]


async def test_sit_toggle_is_seen_as_sit(svc):
    # robotd labels the sit/stand network's ticks `sit` and `rise` in robot.state.policy
    await enable(svc)
    ex = await wait_terminal(await svc.executor.submit(env({"action": "skill", "skill": "sit_toggle"})))
    assert ex.status == "completed", ex.message
    assert "sit" in ex.outcome["policies_seen"]
