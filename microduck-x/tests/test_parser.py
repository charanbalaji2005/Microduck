"""The deterministic parser: every example from the brief, plus the safety-relevant edge cases."""
import pytest

from microduck_x import commands as C
from microduck_x.parser import normalize, parse, words_to_numbers


def actions(text):
    r = parse(text)
    return [c.action for c in r.commands], r


@pytest.mark.parametrize("text, expected", [
    ("Microduck, walk forward.", ["walk"]),
    ("Walk backward for two seconds.", ["walk"]),
    ("Turn left slowly.", ["turn"]),
    ("Turn right by ninety degrees.", ["turn"]),
    ("Stop moving immediately.", ["estop"]),
    ("Reset the simulation.", ["reset"]),
    ("Switch to the laboratory.", ["set_environment"]),
    ("Switch camera to follow mode.", ["camera"]),
    ("Show the robot's joint angles.", ["show_panel"]),
    ("Start a walking test.", ["run_experiment"]),
    ("Pause the simulation.", ["pause"]),
    ("Resume the simulation.", ["resume"]),
    ("Return to the starting position.", ["goto"]),
    ("Explain the robot's current status.", ["status"]),
    ("Follow the waypoint sequence.", ["waypoints"]),
    ("Track the robot with the camera.", ["camera"]),
    ("Show the joint velocities.", ["show_panel"]),
    ("Explain why the robot fell.", ["status"]),
    ("Run the experiment three times.", ["run_experiment"]),
    ("Reset the robot and start again.", ["reset", "enable"]),
    ("Save this experiment.", ["save_experiment"]),
    ("Compare the last two runs.", ["compare_runs"]),
    ("Move to the marked location without touching the obstacles.", ["goto"]),
])
def test_brief_examples(text, expected):
    got, r = actions(text)
    assert got == expected, r.as_dict()
    assert not r.errors and not r.unmatched


def test_numbers():
    assert words_to_numbers("turn ninety degrees") == "turn 90 degrees"
    assert words_to_numbers("walk two and a half seconds") == "walk 2.5 seconds"
    assert words_to_numbers("one hundred eighty degrees") == "180 degrees"
    assert words_to_numbers("for a second") == "for 1 second"
    assert words_to_numbers("go to the garden") == "go to the garden"  # 'to' is not two


def test_values():
    _, r = actions("Walk backward for two seconds.")
    w = r.commands[0]
    assert w.vx < 0 and w.duration_s == 2
    _, r = actions("Turn right by ninety degrees.")
    t = r.commands[0]
    assert t.angle_deg == -90 and t.vyaw < 0
    _, r = actions("walk forward slowly for 2 seconds")
    assert r.commands[0].vx == pytest.approx(0.12)
    _, r = actions("switch to the arena with the slalom layout")
    assert r.commands[0].environment == "arena" and r.commands[0].layout == "slalom"
    _, r = actions("go to goal two")
    assert r.commands[0].target == "goal_2"
    _, r = actions("Run the experiment three times.")
    assert r.commands[0].trials == 3 and r.commands[0].experiment is None


def test_sequences():
    got, _ = actions("turn left and walk forward slowly for 2 seconds, then stop")
    assert got == ["turn", "walk", "stop"]


def test_estop_dominates():
    got, _ = actions("walk forward and then emergency stop")
    assert got == ["estop"]


def test_stop_prefix_is_a_stop():
    got, _ = actions("stop moving and the")  # a recogniser that cut off mid-sentence
    assert got == ["stop"]


def test_cut_off_phrases_are_refused():
    for text in ("go to", "walk forward for", "turn by"):
        got, r = actions(text)
        assert got == [] and r.errors, text


def test_unsupported_and_limits():
    got, r = actions("jump")
    assert got == [] and "not supported" in r.errors[0]
    got, r = actions("walk forward for a minute")
    assert got == [] and "30 s" in r.errors[0]
    got, r = actions("make me a sandwich")
    assert got == [] and r.unmatched


def test_wake_word():
    r = parse("hey microduck turn left")
    assert r.wake_word and r.commands[0].action == "turn"
    r = parse("turn left")
    assert r.wake_word is None


def test_schema_bounds():
    with pytest.raises(ValueError):
        C.Walk(vx=0.9, duration_s=1)
    with pytest.raises(ValueError):
        C.Walk(vx=0.1, duration_s=60)
    with pytest.raises(ValueError):
        C.Walk(vx=0, duration_s=1)
    with pytest.raises(ValueError):
        C.Push(newtons=50)
    with pytest.raises(ValueError):
        C.Skill(skill="rm -rf /")
    env = C.Envelope.model_validate({"command": {"action": "turn", "vyaw": 0.5, "angle_deg": -45}})
    assert env.command.vyaw < 0  # the angle's sign wins
    with pytest.raises(ValueError):
        C.Envelope.model_validate({"command": {"action": "shell", "cmd": "ls"}})
    with pytest.raises(ValueError):
        C.Envelope.model_validate({"command": {"action": "walk", "vx": 0.1, "interface": "x"}})


def test_normalize():
    assert normalize("Turn 90°!") == "turn 90 degrees"
    assert normalize("E-stop") == "estop"
