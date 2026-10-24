"""Deterministic natural language → validated commands.

No model, no network, no randomness: the same words always produce the same commands, and
anything not matched is reported as unmatched rather than guessed. The optional LLM (`llm.py`)
is only consulted for text this parser could not place, and its output is validated by the same
schema.

Speeds are deliberately conservative and inside robotd's pad envelope for the v8 velstand gait
(vx ±0.4 m/s, vyaw ±1.0 rad/s).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from . import commands as C

WAKE_WORDS = ("hey microduck", "ok microduck", "microduck", "micro duck", "hey duck", "duck")

_UNITS = {"zero": 0, "oh": 0, "one": 1, "a": 1, "an": 1, "two": 2, "to": None, "too": None, "three": 3,
          "four": 4, "for": None, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
          "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_NUMBER_WORDS = set(_UNITS) | set(_TENS) | {"hundred", "half", "quarter"}

SPEEDS = {"slow": 0.12, "normal": 0.2, "fast": 0.3}
TURN_SPEEDS = {"slow": 0.4, "normal": 0.7, "fast": 1.0}

ENV_WORDS = {
    "laboratory": "lab", "robotics lab": "lab", "robot lab": "lab", "lab": "lab", "workshop": "lab",
    "garden": "garden", "outdoor": "garden", "outdoors": "garden", "outside": "garden", "park": "garden",
    "apartment": "apartment", "flat": "apartment", "house": "apartment", "home environment": "apartment",
    "living room": "apartment", "arena": "arena", "training arena": "arena", "test arena": "arena",
    "bare floor": "bare", "empty floor": "bare", "empty world": "bare", "default scene": "bare",
    "hangar": "hangar", "space station": "hangar", "spaceship": "hangar", "space": "hangar", "orbit": "hangar",
    "stage": "stage", "scenery stage": "stage", "showcase": "stage", "pedestal stage": "stage",
}
LAYOUTS = ("slalom", "corridor", "scatter", "open")
EXPERIMENT_ALIASES = {
    "walking test": "walk_forward_1m", "walk test": "walk_forward_1m", "walking experiment": "walk_forward_1m",
    "turning test": "turn_in_place_90", "turn test": "turn_in_place_90", "rotation test": "turn_in_place_90",
    "push test": "push_recovery", "push recovery test": "push_recovery", "balance test": "push_recovery",
    "stability test": "push_recovery", "navigation test": "nav_to_goal_2", "waypoint test": "waypoint_tour",
    "slalom test": "nav_slalom",
}
UNSUPPORTED = {
    "jump": "there is no jumping policy", "fly": "it is a duck, but not that kind", "swim": "no water physics",
    "dance": "there is no dance policy (try 'do a roll')", "run": "running is outside the trained speed range",
    "climb": "no climbing policy", "backflip": "the backflip policy is experimental and not in the official set",
    "grab": "the Microduck has no gripper", "talk": "speech output is the dashboard's, not the robot's",
}


# One- and two-word commands, matched on the whole clause before anything else (after stops).
# Each value builds fresh, validated commands.
SHORT: dict[str, callable] = {}


def _short(names: str, build):
    for n in names.split("|"):
        SHORT[n] = build


_short("forward|go|ahead|walk|go forward|walk forward|move|onward|march", lambda: [C.Walk(vx=0.2, duration_s=2.0)])
_short("back|backward|backwards|reverse|back up|go back|walk back", lambda: [C.Walk(vx=-0.15, duration_s=2.0)])
_short("left|turn left|go left", lambda: [C.Turn(vyaw=0.7, angle_deg=90)])
_short("right|turn right|go right", lambda: [C.Turn(vyaw=-0.7, angle_deg=-90)])
_short("around|turn around|about face|u turn", lambda: [C.Turn(vyaw=0.8, angle_deg=180)])
_short("spin|spin around|twirl|pirouette", lambda: [C.Turn(vyaw=1.0, angle_deg=360)])
_short("strafe left|step left|slide left|sidestep left", lambda: [C.Walk(vy=0.12, duration_s=2.0)])
_short("strafe right|step right|slide right|sidestep right", lambda: [C.Walk(vy=-0.12, duration_s=2.0)])
_short("sit|sit down|sleep|nap|rest|lie down", lambda: [C.Skill(skill="sit_toggle")])
_short("stand|stand up|up|wake|wake up|get up|rise|start", lambda: [C.Enable()])
_short("dance|boogie|party|shuffle|dance shuffle|show off", lambda: [C.Dance(style="shuffle")])
_short("spin dance|dance spin", lambda: [C.Dance(style="spin")])
_short("bob|groove|bounce|dance bob", lambda: [C.Dance(style="bob")])
_short("nod|nod yes|yes|yep|nod your head", lambda: [C.Emote(gesture="nod")])
_short("shake|shake your head|no|nope|shake head", lambda: [C.Emote(gesture="shake")])
_short("look left", lambda: [C.Emote(gesture="look_left")])
_short("look right", lambda: [C.Emote(gesture="look_right")])
_short("look up|look at me|up there", lambda: [C.Emote(gesture="look_up")])
_short("look down|look at the floor", lambda: [C.Emote(gesture="look_down")])
_short("look around|scan|look", lambda: [C.Emote(gesture="look_left", duration_s=1.2), C.Emote(gesture="look_right", duration_s=1.2)])
_short("reset pose|reset your pose|neutral|neutral pose|head up|heads up|chin up|stand tall|stay alert|be alert|"
       "alert|go back to idle|back to idle|idle|reset head|reset your head|straighten up|look ahead|look forward",
       lambda: [C.Neutral()])
_short("wiggle|waddle|wobble|shimmy", lambda: [C.Emote(gesture="wiggle")])
_short("crouch|squat|get low|duck down", lambda: [C.Emote(gesture="crouch")])
_short("bow|take a bow", lambda: [C.Emote(gesture="bow")])
_short("celebrate|yay|hooray|woohoo|victory", lambda: [C.Emote(gesture="celebrate")])
_short("curious|tilt|tilt head|hmm", lambda: [C.Emote(gesture="curious")])
_short("sleepy|tired|yawn", lambda: [C.Emote(gesture="sleepy")])
_short("kick|kick right|right kick", lambda: [C.Skill(skill="kick_right")])
_short("kick left|left kick", lambda: [C.Skill(skill="kick_left")])
_short("roll|tumble|somersault|roulade", lambda: [C.Skill(skill="roulade")])
_short("pick|pick up|peck|grab", lambda: [C.Skill(skill="ground_pick")])
_short("home|come back|come here|return|go home|back home", lambda: [C.Goto(target="start")])
_short("patrol|tour|explore", lambda: [C.Waypoints()])
_short("push|shove|nudge|poke", lambda: [C.Push(direction="left", newtons=2.0)])
_short("status|report|status report|diagnostics", lambda: [C.Status()])
_short("help|commands|what can you do", lambda: [C.Help()])
_short("test|walk test|walking test", lambda: [C.RunExperiment(experiment="walk_forward_1m")])
_short("pause|hold on", lambda: [C.Pause()])
_short("resume|continue|unpause|play", lambda: [C.Resume()])
_short("reset|restart|start over", lambda: [C.Reset()])
_short("follow|follow me|chase cam", lambda: [C.Camera(mode="follow")])
_short("orbit|free camera", lambda: [C.Camera(mode="orbit")])
_short("top|top view|overhead|map view", lambda: [C.Camera(mode="top")])
_short("fpv|head cam|eyes|your view|first person", lambda: [C.Camera(mode="first_person")])
for _name, _env in (("lab", "lab"), ("laboratory", "lab"), ("garden", "garden"), ("arena", "arena"),
                    ("apartment", "apartment"), ("hangar", "hangar"), ("bare", "bare"), ("stage", "stage"),
                    ("showcase", "stage")):
    _short(f"{_name}|go to the {_name}|to the {_name}|{_name} please", lambda e=_env: [C.SetEnvironment(environment=e)])


@dataclass
class ParseResult:
    text: str
    normalized: str
    wake_word: str | None
    commands: list = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.commands) and not self.errors and not self.unmatched

    def as_dict(self) -> dict:
        return dict(text=self.text, normalized=self.normalized, wake_word=self.wake_word,
                    commands=[c.model_dump() for c in self.commands], unmatched=self.unmatched,
                    errors=self.errors, notes=self.notes, ok=self.ok)


def words_to_numbers(text: str) -> str:
    """'ninety degrees' → '90 degrees', 'two and a half seconds' → '2.5 seconds', 'a second' → '1 second'."""
    tokens = text.split()
    out: list[str] = []
    i = 0
    n = len(tokens)
    nxt = lambda k: tokens[k] if k < n else ""  # noqa: E731
    while i < n:
        tok = tokens[i]
        if tok in ("a", "an"):
            if nxt(i + 1) == "half":
                out.append("0.5"); i += 2; continue
            if nxt(i + 1) == "quarter":
                out.append("0.25"); i += 2; continue
            if re.match(r"(second|meter|metre|minute|degree)", nxt(i + 1)):
                out.append("1"); i += 1; continue
            out.append(tok); i += 1; continue
        if tok == "half":
            out.append("0.5"); i += 1; continue
        if tok == "quarter":
            out.append("0.25"); i += 1; continue
        if tok in _TENS or (tok in _UNITS and _UNITS[tok] is not None):
            value = float(_TENS.get(tok, _UNITS.get(tok) or 0))
            i += 1
            if tok in _TENS and nxt(i) in _UNITS and _UNITS[nxt(i)] and 0 < _UNITS[nxt(i)] < 10 \
                    and nxt(i) not in ("a", "an"):
                value += _UNITS[nxt(i)]; i += 1
            if nxt(i) == "hundred":
                value *= 100; i += 1
                if nxt(i) == "and" and (nxt(i + 1) in _TENS or nxt(i + 1) in _UNITS) and nxt(i + 1) not in ("a", "an"):
                    i += 1
                if nxt(i) in _TENS:
                    value += _TENS[nxt(i)]; i += 1
                if nxt(i) in _UNITS and _UNITS[nxt(i)] and nxt(i) not in ("a", "an"):
                    value += _UNITS[nxt(i)]; i += 1
            if nxt(i) == "and" and nxt(i + 1) == "a" and nxt(i + 2) == "half":
                value += 0.5; i += 3
            elif nxt(i) == "and" and nxt(i + 1) == "a" and nxt(i + 2) == "quarter":
                value += 0.25; i += 3
            elif nxt(i) == "and" and nxt(i + 1) == "half":
                value += 0.5; i += 2
            out.append(f"{value:g}")
            continue
        out.append(tok)
        i += 1
    return " ".join(out)


def normalize(text: str) -> str:
    t = text.lower().strip()
    t = t.replace("°", " degrees ").replace("-", " ").replace("’", "'")
    t = re.sub(r"[^a-z0-9.' ]+", " ", t)
    t = re.sub(r"(?<![0-9])\.|\.(?![0-9])", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\be stop\b", "estop", t)
    return words_to_numbers(t)


def strip_wake_word(t: str) -> tuple[str, str | None]:
    for w in WAKE_WORDS:
        if t == w:
            return "", w
        if t.startswith(w + " "):
            return t[len(w) + 1:].strip(), w
    m = re.search(r"\b(hey )?(microduck|micro duck)\b", t)
    if m:
        return (t[:m.start()] + t[m.end():]).strip(), m.group(0)
    return t, None


_VERB_START = r"(?:walk|go|move|step|head|turn|rotate|spin|stop|halt|freeze|pause|resume|unpause|continue|reset|restart|" \
              r"start|run|switch|change|load|take|show|hide|display|track|follow|kick|roll|do|pick|push|shove|nudge|" \
              r"sit|stand|get|enable|disable|wake|explain|tell|report|describe|what|why|how|save|compare|return|" \
              r"navigate|visit|patrol|clear|release|emergency|estop|camera|look|help)\b"


def split_clauses(t: str) -> list[str]:
    parts = re.split(r"\s*(?:,|;)?\s*\b(?:and then|then|after that|afterwards|and)\b\s*(?=" + _VERB_START + ")", t)
    return [p.strip() for p in parts if p and p.strip()]


def _speed(clause: str, table: dict[str, float]) -> float:
    if re.search(r"\b(slow|slowly|gently|carefully|a bit|a little)\b", clause):
        return table["slow"]
    if re.search(r"\b(fast|quickly|quick|faster|hurry)\b", clause):
        return table["fast"]
    return table["normal"]


def _seconds(clause: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(seconds?|secs?|s)\b", clause)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(minutes?|mins?)\b", clause)
    if m:
        return float(m.group(1)) * 60
    if re.search(r"\b(a|one) second\b", clause):
        return 1.0
    return None


def _metres(clause: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(meters?|metres?|m)\b", clause)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(centimeters?|centimetres?|cm)\b", clause)
    if m:
        return float(m.group(1)) / 100
    return None


def _degrees(clause: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(degrees?|deg)\b", clause)
    if m:
        return float(m.group(1))
    if re.search(r"\b(turn|spin|rotate) around\b|\babout face\b|\b0.5 (a )?(turn|circle)\b", clause):
        return 180.0
    if re.search(r"\b0.25 (a )?(turn|circle)\b", clause):
        return 90.0
    if re.search(r"\b(full|whole|complete) (turn|circle|rotation)\b|\b360\b", clause):
        return 360.0
    return None


def _build(cls, **kw):
    return cls(**kw)


def parse_clause(clause: str) -> tuple[list, list[str]]:
    """One clause → (commands, notes). Raises ValueError for recognised-but-invalid requests."""
    c = clause
    notes: list[str] = []
    if re.fullmatch(r"(emergency|emergency stop|estop|freeze|halt|abort|kill)", c):
        return [C.EStop()], notes
    if c in SHORT:
        return SHORT[c](), notes
    # A clause that ends mid-phrase ("go to", "walk forward for", "turn by") is a recogniser that
    # stopped early, not a request. Executing what is left would do something nobody asked for.
    if re.search(r"\b(to|for|by|the|a|and|then|with|at|of|from|into)$", c) and not re.search(r"\bestop|emergency|stop\b", c):
        raise ValueError(f"'{c}' sounds cut off — say the whole command")

    # ── safety first: anything that sounds like stopping is a stop ──────────
    if re.search(r"\b(emergency|estop|stop (moving )?(immediately|now|right now|at once)|freeze|halt|abort|kill)\b", c):
        return [C.EStop()], notes
    if re.search(r"\b(clear|release|reset|cancel) (the )?(emergency( stop)?|estop|stop latch)\b|\bresume control\b", c):
        return [C.ClearEStop()], notes
    if re.fullmatch(r"(please )?(stop|stop moving|stop walking|stop it|stand still|hold still|wait)( please)?", c):
        return [C.Stop()], notes
    if re.match(r"(stop|halt|whoa|woah)\b", c) and not re.search(r"\b(policy|test|experiment|camera|following)\b", c):
        return [C.Stop()], ["anything that starts with 'stop' stops"]

    # ── simulation ──────────────────────────────────────────────────────────
    if re.search(r"\b(unpause|resume|continue)\b( the)?( simulation| sim| physics| world)?$", c):
        return [C.Resume()], notes
    if re.search(r"\bpause\b", c):
        return [C.Pause()], notes
    if re.search(r"\b(reset|restart|start over)\b", c) and not re.search(r"\bcamera\b|\bexperiment\b", c):
        keyframe = "STAND" if re.search(r"\bstanding\b", c) else "SIT"
        out = [C.Reset(keyframe=keyframe, enable_after=True)]
        return out, notes

    # ── environments ────────────────────────────────────────────────────────
    if re.search(r"\b(switch|change|go|move|take( me)?|load|teleport|open|show)\b.*\b(to|the|into)?\b", c) or \
            re.search(r"\b(environment|scene|world|map)\b", c):
        for phrase, env in sorted(ENV_WORDS.items(), key=lambda kv: -len(kv[0])):
            if re.search(rf"\b{phrase}\b", c) and not re.search(r"\b(goal|marker|waypoint|start)\b", c):
                layout = next((l for l in LAYOUTS if re.search(rf"\b{l}\b", c)), None)
                if layout and env != "arena":
                    notes.append(f"layout '{layout}' only exists in the arena; ignored")
                    layout = None
                return [C.SetEnvironment(environment=env, layout=layout)], notes
    m = re.search(r"\b(slalom|corridor|scatter|open) (layout|course|arena)\b", c)
    if m:
        return [C.SetEnvironment(environment="arena", layout=m.group(1))], notes

    # ── camera and panels ───────────────────────────────────────────────────
    if re.search(r"\bcamera\b|\btrack (the )?(robot|duck|it)\b|\bfollow (the )?(robot|duck)\b|\b(first person|fpv|bird'?s eye|top down|overhead) view\b", c):
        if re.search(r"\breset\b", c):
            mode = "reset"
        elif re.search(r"\b(first person|fpv|head|eyes?|robot'?s view|duck'?s view)\b", c):
            mode = "first_person"
        elif re.search(r"\b(top|overhead|bird'?s eye|above|plan)\b", c):
            mode = "top"
        elif re.search(r"\b(orbit|free|manual)\b", c):
            mode = "orbit"
        elif re.search(r"\b(follow|track|chase)\b", c):
            mode = "follow"
        else:
            raise ValueError("which camera mode? follow, orbit, first person or top")
        return [C.Camera(mode=mode)], notes
    m = re.search(r"\b(show|display|open|hide|close)( me)?( the)?( robot'?s?)? (joint angles|joint positions|joints|joint velocities|"
                  r"velocities|velocity|telemetry|sensors?|contacts?|contact points|collision( geometry| meshes)?|logs?|"
                  r"experiments?|voice|policy|policies|navigation|path)\b", c)
    if m:
        what = m.group(5)
        panel = ("velocities" if "velocit" in what else "joints" if "joint" in what else
                 "contacts" if "contact" in what else "collision" if "collision" in what else
                 "logs" if "log" in what else "experiments" if "experiment" in what else
                 "policy" if "polic" in what else "navigation" if what in ("navigation", "path") else
                 "voice" if what == "voice" else "telemetry")
        return [C.ShowPanel(panel=panel, visible=m.group(1) not in ("hide", "close"))], notes

    # ── reports ─────────────────────────────────────────────────────────────
    if re.search(r"\bwhy did (it|the robot|the duck|you|he|she) fall\b|\bwhy .*\b(fell|fall|fallen)\b|\bexplain (the )?fall\b", c):
        return [C.Status(topic="fall")], notes
    if re.search(r"\b(status|state|condition|how are you|what are you doing|what'?s (it|the robot) doing|report)\b", c) and \
            re.search(r"\b(explain|what|tell|report|give|describe|status|how)\b", c):
        topic = "policy" if re.search(r"\bpolic", c) else "joints" if re.search(r"\bjoint", c) else "general"
        return [C.Status(topic=topic)], notes
    if re.search(r"\b(help|what can (you|i) (do|say)|commands)\b", c):
        return [C.Help()], notes

    # ── experiments ─────────────────────────────────────────────────────────
    if re.search(r"\bcompare\b", c):
        return [C.CompareRuns()], notes
    if re.search(r"\bsave\b.*\b(experiment|run|result|this)\b", c):
        return [C.SaveExperiment()], notes
    times = None
    m = re.search(r"(\d+) times\b", c) or re.search(r"\b(once|twice|thrice)\b", c)
    if m:
        times = {"once": 1, "twice": 2, "thrice": 3}.get(m.group(1), None) or int(float(m.group(1)))
    for phrase, name in sorted(EXPERIMENT_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{phrase}\b", c):
            return [C.RunExperiment(experiment=name, trials=times or 1)], notes
    m = re.search(r"\b(run|start|repeat)( the| an| a)? (experiment|test|trial)( ([a-z0-9_]+))?\b", c)
    if m:
        name = m.group(5)
        if name in ("again", "times") or (name and name.isdigit()):
            name = None
        return [C.RunExperiment(experiment=name, trials=times or 1)], notes

    # ── skills (robot.do) ───────────────────────────────────────────────────
    m = re.search(r"\bkick\b.*\b(left|right)\b|\b(left|right)\b.*\bkick\b", c)
    if m:
        side = m.group(1) or m.group(2)
        return [C.Skill(skill=f"kick_{side}")], notes
    if re.search(r"\bkick\b", c):
        return [C.Skill(skill="kick_right")], ["no foot named; kicking with the right"]
    if re.search(r"\b(roll|roulade|somersault|tumble)\b", c):
        return [C.Skill(skill="roulade")], notes
    if re.search(r"\b(pick (it |something |that |this )?up|ground pick|peck|grab from the floor)\b", c):
        return [C.Skill(skill="ground_pick")], notes
    if re.search(r"\bsit( down)?\b", c):
        return [C.Skill(skill="sit_toggle")], ["sit is a toggle on robotd: the same request stands it back up"]

    if re.fullmatch(r"(start|begin|go) (again|over)", c):
        return [C.Enable()], ["starting again: the policy is (re)enabled"]

    # ── power and policy ────────────────────────────────────────────────────
    if re.search(r"\b(stand up|get up|wake up|enable|start (the )?(policy|robot|walking policy)|power on|turn on)\b", c):
        return [C.Enable()], notes
    if re.search(r"\b(disable|turn off|power off|stop the policy|go to sleep|rest)\b", c):
        return [C.Disable()], notes

    # ── shoves ──────────────────────────────────────────────────────────────
    if re.search(r"\b(push|shove|nudge|poke|bump)\b", c):
        direction = "front"
        m = re.search(r"\bfrom the (front|back|left|right)\b", c)
        if m:
            direction = m.group(1)
        else:
            m = re.search(r"\b(forward|backward|back|left|right)\b", c)
            if m:
                # "push it forward" = force towards +x = applied from behind
                direction = {"forward": "back", "backward": "front", "back": "front",
                             "left": "right", "right": "left"}[m.group(1)]
        newtons = 2.0 if re.search(r"\b(gentle|gently|light|lightly|small|little)\b", c) else \
            5.0 if re.search(r"\b(hard|strong|big)\b", c) else 3.0
        return [C.Push(direction=direction, newtons=newtons)], notes

    # ── navigation ──────────────────────────────────────────────────────────
    if re.search(r"\b(return|go back|walk back|head back|come back)\b.*\b(start|starting|home|origin|beginning)\b|\bgo home\b", c):
        return [C.Goto(target="start", speed=_speed(c, SPEEDS))], notes
    if re.search(r"\b(waypoints?|way points?|patrol|tour|visit (all|every|each) (the )?(goals?|markers?))\b", c):
        return [C.Waypoints(speed=_speed(c, SPEEDS))], notes
    m = re.search(r"\b(go|walk|navigate|move|head|drive|get) (over )?to (the )?([a-z0-9_ ]+)", c)
    if m:
        target = m.group(4).strip()
        target = re.sub(r"\b(without|avoiding|while|but)\b.*", "", target).strip()
        target = re.sub(r"\b(please|now|slowly|quickly|fast|location|position|point|marked)\b", "", target).strip()
        target = re.sub(r"\s+", " ", target)
        g = re.fullmatch(r"(goal|marker|waypoint|target)? ?(\d+)", target)
        if g:
            target = f"goal_{g.group(2)}"
        elif target in ("", "goal", "marker", "target", "waypoint", "marked location", "location"):
            target = "goal"  # the executor resolves this to the nearest goal marker
        return [C.Goto(target=target.replace(" ", "_"), speed=_speed(c, SPEEDS))], notes

    # ── walking and turning ─────────────────────────────────────────────────
    turn = re.search(r"\b(turn|rotate|spin|pivot)\b", c)
    walk = re.search(r"\b(walk|go|move|step|head|drive|march|come)\b", c)
    if turn and not re.search(r"\b(forward|backward|backwards|ahead|straight)\b", c):
        left = re.search(r"\b(left|anticlockwise|counterclockwise|counter clockwise)\b", c)
        right = re.search(r"\b(right|clockwise)\b", c)
        sign = 1.0 if left or not right else -1.0
        if not left and not right:
            if not re.search(r"\baround\b", c):
                notes.append("no direction named; turning left")
        vyaw = sign * _speed(c, TURN_SPEEDS)
        deg = _degrees(c)
        secs = _seconds(c)
        if deg is None and secs is None:
            deg = 90.0
            notes.append("no angle named; turning 90 degrees")
        if deg is not None:
            return [C.Turn(vyaw=vyaw, angle_deg=sign * deg)], notes
        return [C.Turn(vyaw=vyaw, duration_s=secs)], notes

    if walk or re.fullmatch(r"(forward|backward|backwards|ahead|straight ahead)", c):
        speed = _speed(c, SPEEDS)
        vx = vy = vyaw = 0.0
        if re.search(r"\b(backward|backwards|back|reverse)\b", c):
            vx = -speed
        elif re.search(r"\b(sideways|strafe|step)\b.*\bleft\b|\bleft\b.*\bsideways\b", c):
            vy = min(speed, C.VY_MAX)
        elif re.search(r"\b(sideways|strafe|step)\b.*\bright\b|\bright\b.*\bsideways\b", c):
            vy = -min(speed, C.VY_MAX)
        elif re.search(r"\b(left)\b", c):
            vx, vyaw = speed, 0.5
            notes.append("walking forward while curving left")
        elif re.search(r"\b(right)\b", c):
            vx, vyaw = speed, -0.5
            notes.append("walking forward while curving right")
        else:
            vx = speed
        secs = _seconds(c)
        metres = _metres(c)
        if metres is not None and secs is None:
            v = math.hypot(vx, vy) or speed
            secs = metres / v
            notes.append(f"{metres:g} m at {v:.2f} m/s ≈ {secs:.1f} s, open loop (use 'go to' for closed loop)")
        if secs is None:
            secs = 3.0
            notes.append("no duration named; walking 3 seconds")
        if secs > C.MAX_DURATION_S:
            raise ValueError(f"{secs:g} s is longer than the {C.MAX_DURATION_S:g} s limit per command")
        return [C.Walk(vx=vx, vy=vy, vyaw=vyaw, duration_s=secs)], notes

    for word, why in UNSUPPORTED.items():
        if re.search(rf"\b{word}", c):
            raise ValueError(f"'{word}' is not supported: {why}")
    return [], notes


def parse(text: str) -> ParseResult:
    normalized = normalize(text)
    body, wake = strip_wake_word(normalized)
    result = ParseResult(text=text, normalized=body, wake_word=wake)
    if not body:
        if wake:
            result.notes.append("heard the wake word and nothing after it")
        return result
    for clause in split_clauses(body):
        clause = re.sub(r"^(please|can you|could you|would you|i want you to|let'?s|now)\s+", "", clause).strip()
        clause = re.sub(r"\s+please$", "", clause)
        if not clause:
            continue
        try:
            cmds, notes = parse_clause(clause)
        except ValidationError as error:
            result.errors.append(f"'{clause}': {error.errors()[0]['msg']}")
            continue
        except ValueError as error:
            result.errors.append(str(error))
            continue
        result.notes.extend(notes)
        if cmds:
            result.commands.extend(cmds)
        else:
            result.unmatched.append(clause)
    # An emergency stop anywhere in an utterance is the whole utterance.
    if any(isinstance(c, C.EStop) for c in result.commands):
        result.commands = [C.EStop()]
    return result
