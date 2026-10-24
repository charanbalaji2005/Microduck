"""Walking and talking together: the gait floor every twist passes, and the chat turns that walk,
talk, or stop one without the other. Executor tests run against the MOCK robotd/simulator."""
import asyncio

import pytest

from microduck_x.commands import Envelope
from microduck_x.executor import gait_floor
from conftest import wait_terminal
from test_persona import persona

FLOORS = (0.28, 0.38, 0.85)  # Settings defaults: measured on velstand v8 in this simulator


def env(cmd):
    return Envelope.model_validate({"command": cmd, "source": "api"})


async def enable(svc):
    ex = await wait_terminal(await svc.executor.submit(env({"action": "enable"})))
    assert ex.status == "completed", ex.message


def test_slow_twists_are_raised_to_what_the_gait_steps_for():
    assert gait_floor(0.2, 0, 0, *FLOORS) == pytest.approx((0.28, 0, 0))      # forward
    assert gait_floor(-0.2, 0, 0, *FLOORS) == pytest.approx((-0.38, 0, 0))    # backward has its own floor
    assert gait_floor(0, 0, 0.6, *FLOORS) == pytest.approx((0, 0, 0.85))      # turn left
    assert gait_floor(0, 0, -0.3, *FLOORS) == pytest.approx((0, 0, -0.85))    # turn right


def test_floor_keeps_direction_and_leaves_stepping_twists_alone():
    vx, vy, vyaw = gait_floor(0.14, 0, 0.1, *FLOORS)                          # a slow arc
    assert vx == pytest.approx(0.28) and vyaw / vx == pytest.approx(0.1 / 0.14)
    assert gait_floor(0.3, 0, 0.1, *FLOORS) == (0.3, 0, 0.1)                   # already walking: small corrections stay small
    assert gait_floor(0, 0, 0, *FLOORS) == (0, 0, 0)                           # standing still stays still
    assert gait_floor(0, 0.12, 0, *FLOORS) == (0, 0.12, 0)                     # sideways: no measured floor
    assert gait_floor(0.2, 0, 0, 0, 0, 0) == (0.2, 0, 0)                       # floors off


async def test_teleop_forward_backward_turn_and_stop_reach_robotd(svc):
    await enable(svc)

    async def requested(vx, vy, vyaw):
        assert (await svc.executor.teleop(vx, vy, vyaw))["ok"]
        await asyncio.sleep(0.25)
        return svc.hub.robot["move"]["requested"]

    assert await requested(0.2, 0, 0) == pytest.approx([0.28, 0, 0])
    assert await requested(-0.2, 0, 0) == pytest.approx([-0.38, 0, 0])
    assert await requested(0, 0, 0.6) == pytest.approx([0, 0, 0.85])
    assert await requested(0.5, 0, 0) == pytest.approx([0.4, 0, 0])  # still clamped to the envelope
    assert await requested(0, 0, 0) == pytest.approx([0, 0, 0])


async def test_chat_walk_and_turn_commands_step(svc):
    await enable(svc)
    for text, check in (("walk forward", lambda s: s[0] == pytest.approx(0.28)),
                        ("walk backward", lambda s: s[0] == pytest.approx(-0.38)),
                        ("turn left", lambda s: s[2] >= 0.85 - 1e-9),
                        ("turn right", lambda s: s[2] <= -0.85 + 1e-9)):
        turn = svc.persona.handle(text)
        ex = await wait_terminal(await svc.executor.submit(Envelope(command=turn.commands[0], source="chat")), timeout=30)
        assert ex.status == "completed", (text, ex.message)
        assert check(ex.outcome["sent"]), (text, ex.outcome["sent"])


def test_stop_talking_silences_without_stopping(tmp_path):
    p = persona(tmp_path)
    for text in ("stop talking", "be quiet", "shush", "stop quacking"):
        turn = p.handle(text)
        assert turn.commands == [], text                # the legs are not touched
        assert turn.replies[0].ui == {"voice": "stop"}  # the page cuts the voice
    assert [c.action for c in p.handle("stop moving").commands] == ["stop"]
    assert p.handle("stop moving").replies[0].ui is None  # stopping the legs leaves the voice alone


def test_say_hello_while_walking(tmp_path):
    p = persona(tmp_path)
    turn = p.handle("say hello while walking")
    assert [c.action for c in turn.commands] == ["walk"] and turn.commands[0].vx > 0
    assert "Hi" in turn.replies[0].text and not turn.side
    back = p.handle("say hello while walking backwards")
    assert back.commands[0].vx < 0
    joke = p.handle("tell me a joke while walking")
    assert joke.commands[0].action == "walk" and "duck" in joke.replies[0].text.lower()


def test_greeting_does_not_interrupt_a_walk(tmp_path):
    state = {"activity": None, "teleop": False}
    p = persona(tmp_path, lambda: {"status": {"activity": state["activity"]}, "teleop": state["teleop"]})
    assert [c.action for c in p.handle("hello").commands] == ["emote"]  # standing: it nods
    state["activity"] = {"action": "walk"}
    turn = p.handle("hello")
    assert turn.commands == [] and turn.replies  # walking: it only talks
    state.update(activity=None, teleop=True)
    assert p.handle("good duck").commands == []  # keyboard driving counts too


async def test_talking_while_a_chat_walk_runs_keeps_it_running(svc):
    await enable(svc)
    walk = await svc.executor.submit(env({"action": "walk", "vx": 0.3, "duration_s": 2.0}))
    await asyncio.sleep(0.3)
    turn = svc.persona.handle("hello")
    assert turn.commands == [] and turn.replies
    await wait_terminal(walk)
    assert walk.status == "completed", walk.message
