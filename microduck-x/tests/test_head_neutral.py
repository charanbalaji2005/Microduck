"""The head comes back upright: gestures, interruptions and the talking head all end at robotd's
trained neutral (every head offset zero), never at a reading of robot.state.head.

Against the MOCK robotd, which — like the real one — reports `intent + idle offset` as the head,
so the old "restore what robot.state said" behaviour is reproducible here (not the Microduck)."""
import asyncio

import pytest

from microduck_x.commands import Envelope
from microduck_x.parser import parse
from conftest import wait_terminal

NEUTRAL = [0.0, 0.0, 0.0, 0.0]
# what robotd's idle behaviour might be adding when a gesture starts: a glance left, chin down, a tilt
IDLE_GLANCE = [0.1, 0.3, 0.8, 0.27]


def env(cmd):
    return Envelope.model_validate({"command": cmd, "source": "api"})


async def run(svc, cmd):
    return await wait_terminal(await svc.executor.submit(env(cmd)))


async def enable(svc):
    ex = await run(svc, {"action": "enable"})
    assert ex.status == "completed", ex.message


async def settle(svc):
    await asyncio.sleep(0.15)  # a few robot.state ticks at 25 Hz


async def test_a_gesture_ends_at_neutral_not_at_the_reported_head(svc):
    await enable(svc)
    svc.mock.idle_offset = list(IDLE_GLANCE)
    await settle(svc)
    assert svc.hub.robot["head"] == pytest.approx(IDLE_GLANCE)  # what the old code captured and sent back
    ex = await run(svc, {"action": "emote", "gesture": "nod", "duration_s": 1.0})
    assert ex.status == "completed", ex.message
    assert svc.mock.head == pytest.approx(NEUTRAL), "the head intent must return to neutral"


async def test_repeated_replies_do_not_accumulate(svc):
    await enable(svc)
    for i, gesture in enumerate(("nod", "look_down", "sleepy", "curious", "celebrate")):
        svc.mock.idle_offset = [0.05 * (i + 1), 0.1, 0.2 * (i + 1), 0.05]  # a different glance each time
        await settle(svc)
        ex = await run(svc, {"action": "emote", "gesture": gesture, "duration_s": 0.8})
        assert ex.status == "completed", (gesture, ex.message)
        assert svc.mock.head == pytest.approx(NEUTRAL), gesture


async def test_an_interrupted_gesture_still_ends_at_neutral(svc):
    await enable(svc)
    svc.mock.idle_offset = list(IDLE_GLANCE)
    first = await svc.executor.submit(env({"action": "emote", "gesture": "look_down", "duration_s": 4.0}))
    await asyncio.sleep(0.5)
    assert svc.mock.head != pytest.approx(NEUTRAL)  # looking down, mid-gesture
    stop = await run(svc, {"action": "stop"})
    assert stop.status == "completed"
    await wait_terminal(first)
    assert first.status == "cancelled"
    assert svc.mock.head == pytest.approx(NEUTRAL)


async def test_reset_pose_brings_a_stuck_head_back(svc):
    await enable(svc)
    svc.mock.head = [0.0, -0.1, 0.81, 0.27]  # the pose found stuck on the live robot
    ex = await run(svc, {"action": "neutral"})
    assert ex.status == "completed", ex.message
    assert svc.mock.head == pytest.approx(NEUTRAL)
    assert ex.outcome["head_before"] == pytest.approx([0.0, -0.1, 0.81, 0.27], abs=1e-3)
    assert max(abs(v) for v in ex.outcome["head_after"]) < 0.01


@pytest.mark.parametrize("said", ["reset pose", "stay alert", "go back to idle", "head up", "neutral"])
def test_reset_pose_phrases(said):
    p = parse(said)
    assert [c.action for c in p.commands] == ["neutral"], said


async def test_talking_head_moves_then_returns_to_neutral(svc):
    await enable(svc)
    svc.executor.talk(True, [1.0, -0.4, 0.3])  # the viewer: in front of the duck and to its right
    seen = []
    for _ in range(30):
        svc.executor.talk(True)
        seen.append(list(svc.mock.head))
        await asyncio.sleep(0.1)
    assert any(max(abs(v) for v in h) > 0.02 for h in seen), "the head moves while the duck talks"
    svc.executor.talk(False)
    await asyncio.sleep(0.3)
    assert svc.mock.head == pytest.approx(NEUTRAL)


async def test_talking_head_stops_without_keepalives(svc):
    await enable(svc)
    svc.executor.talk(True, [1.0, 0.0, 0.3])
    await asyncio.sleep(1.5)
    await asyncio.sleep(svc.executor.TALK_KEEPALIVE_S)  # the page went away mid-sentence
    await asyncio.sleep(0.3)
    assert svc.mock.head == pytest.approx(NEUTRAL)


async def test_talking_does_not_stop_a_walk(svc):
    await enable(svc)
    walk = await svc.executor.submit(env({"action": "walk", "vx": 0.3, "duration_s": 2.0}))
    for _ in range(20):
        svc.executor.talk(True, [1.0, 0.0, 0.3])
        await asyncio.sleep(0.1)
    await wait_terminal(walk)
    assert walk.status == "completed", walk.message
    svc.executor.talk(False)
