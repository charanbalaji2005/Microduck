"""The duck's personality, against the MOCK robot: replies, grounded status, learning, actions."""
import asyncio

from microduck_x.commands import Envelope
from microduck_x.persona import Memory, Persona, babble
from conftest import wait_terminal


def test_babble_is_deterministic_and_moody():
    assert babble("hello", "happy") == babble("hello", "happy")
    assert babble("hello", "happy") != babble("hello", "sorry")


def persona(tmp_path, ctx=None):
    return Persona(Memory.load(tmp_path / "mem.json"), ctx or (lambda: {}))


def test_learning_names_and_words(tmp_path):
    p = persona(tmp_path)
    assert "Charan" in p.handle("my name is charan").replies[0].text
    turn = p.handle("when I say zoom, walk forward fast for 3 seconds")
    assert turn.learned == "zoom"
    again = Persona(Memory.load(tmp_path / "mem.json"), lambda: {})  # persisted
    assert again.mem.user_name == "Charan"
    t = again.handle("zoom")
    assert t.commands[0].action == "walk" and t.commands[0].vx == 0.3
    assert "can't learn" in again.handle("when I say fly, fly to the moon").replies[0].text
    assert again.handle("what is my name").replies[0].text == "You're Charan!"


def test_speed_preference_and_repeat(tmp_path):
    p = persona(tmp_path)
    p.handle("faster")
    walk = p.handle("forward").commands[0]
    assert walk.vx > 0.2
    assert p.handle("again").commands[0].action == "walk"


def test_status_is_grounded(tmp_path):
    bad = persona(tmp_path, lambda: {"status": {"robotd": {"connected": True, "policy_unavailable": "no velstand.onnx"},
                                                 "safety": {}, "sim": {}}})
    assert "didn't load" in bad.handle("status report").replies[0].text
    good = persona(tmp_path, lambda: {"status": {"robotd": {"connected": True, "loop_hz": 50, "policy": "walk"},
                                                  "safety": {"blockers": []}, "sim": {"rtf": 1.0}}})
    assert good.handle("status report").replies[0].text.startswith("No faults detected")


def test_chit_chat_maps_to_real_actions(tmp_path):
    p = persona(tmp_path)
    assert [c.action for c in p.handle("hello").commands] == ["emote"]
    assert p.handle("good night").commands[0].skill == "sit_toggle"
    assert p.handle("watch out").commands[0].action == "stop"
    assert p.handle("dance").commands[0].action == "dance"
    assert "don't know" in p.handle("flibbertigibbet").replies[0].text


async def test_emote_and_dance_go_through_robotd(svc):
    ex = await wait_terminal(await svc.executor.submit(Envelope.model_validate({"command": {"action": "enable"}})))
    assert ex.status == "completed"
    nod = await wait_terminal(await svc.executor.submit(Envelope.model_validate(
        {"command": {"action": "emote", "gesture": "nod", "duration_s": 1.0}})))
    assert nod.status == "completed", nod.message
    assert nod.outcome["head_moved_rad"] > 0.03  # robot.state.head moved
    dance = await wait_terminal(await svc.executor.submit(Envelope.model_validate(
        {"command": {"action": "dance", "style": "shuffle"}})), timeout=60)
    assert dance.status == "completed", dance.message
    assert [s["step"] for s in dance.outcome["steps"]][0] == "wiggle"


def test_scenery_requests_change_the_view_and_reach_the_stage_first(tmp_path):
    on_stage = persona(tmp_path, lambda: {"env": {"name": "stage"}})
    turn = on_stage.handle("show me the beach")
    assert turn.replies[0].ui == {"scenery": "beach"} and turn.commands == []
    assert on_stage.handle("next scenery").replies[0].ui == {"scenery": "next"}
    in_hangar = persona(tmp_path, lambda: {"env": {"name": "hangar"}})
    turn = in_hangar.handle("venice")
    assert turn.replies[0].ui == {"scenery": "venice"}
    assert [(c.action, c.environment) for c in turn.commands] == [("set_environment", "stage")]
    # scene words still mean scenes
    assert on_stage.handle("go to the garden").commands[0].environment == "garden"


def test_idle_talk_is_talk_only_and_grounded(tmp_path):
    ctx = {"status": {"robotd": {"connected": True, "loop_hz": 50.2, "policy": "walk"}, "sim": {"rtf": 1.0},
                      "uptime_s": 600}, "env": {"name": "stage"}}
    p = persona(tmp_path, lambda: ctx)
    lines = set()
    for _ in range(40):
        r = p.idle({"scenery": "snow", "cousins": ["Pip", "Bix"]})
        assert r.idle and r.as_dict()["idle"] is True and r.ui is None
        lines.add(r.text)
    assert len(lines) > 10, "it should not repeat itself"
    assert any("50 hertz" in t for t in lines)
    lonely = p.idle({"unanswered": 3})
    assert lonely.mood == "sleepy"
    # nothing it says is invented: no hertz line without a connected robotd
    quiet = persona(tmp_path, lambda: {"status": {"robotd": {"connected": False}}})
    assert not any("hertz" in quiet.idle({}).text for _ in range(40))
