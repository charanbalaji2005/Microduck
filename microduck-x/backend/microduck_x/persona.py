"""The duck's personality: chat replies, robot babble, moods, and what it learns from you.

Everything the duck *says about itself* is read from real telemetry at the moment it says it —
"No faults detected" only when robotd is healthy, a policy is driving and motion is allowed;
otherwise it names the actual blocker. Everything it *does* goes through the same command
allowlist and executor as a button press.

What "learning" means here, precisely: the duck remembers your name, its own name, a speed
preference, and new words you teach it ("when I say zoom, walk forward fast for 3 seconds").
A taught word is stored only if what it stands for parses into valid commands. It is memory,
persisted to disk — not machine learning. The robot's *movement* skills are the trained RL
policies robotd runs; nothing here changes them.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import commands as C
from .parser import normalize, parse, strip_wake_word

SYLLABLES = {
    "happy": ["bweep", "wheep", "dee", "bip", "tweedle", "pwee", "dit", "bloop", "wee-woo", "zip", "bwee"],
    "proud": ["brrdee", "taa", "dwee", "vroo", "bop", "ta-da", "zoom", "bweep", "deedle"],
    "curious": ["hmm", "bwuh", "dwip", "tik", "tok", "wuh", "brrp", "dee?", "bip?", "whirr"],
    "alert": ["bzzt", "grzk", "vrrk", "zzap", "krk", "blat", "bzt-bzt", "eep", "vwee"],
    "sorry": ["dwoo", "boo", "bwoo", "mrrp", "wuuh", "dooo", "blub", "fwoo"],
    "sleepy": ["zzz", "mmm", "hwoo", "fwee", "shh", "dooo", "mrr", "wuhh"],
    "ramble": ["brdee", "dit", "bwoop", "vwee", "beep", "dwoo", "blat", "grzzk", "vrrk", "boo", "bweep", "dit-dit"],
}

JOKES = [
    "Why did the duck cross the hangar? Its walking policy said the twist was positive.",
    "I asked for a raise. robotd clamped it to the trained range.",
    "What do you call a duck with fourteen servos? Thirteen short of a quack-tet. No wait.",
    "My favourite exercise? Duck-squats. Two centimetres, exactly as trained.",
    "I tried to fly once. MuJoCo said gravity is not a parameter.",
    "Knock knock. Who's there? Odometry. Odometry who? Odometry thinks I'm two centimetres to the left.",
]


def babble(text: str, mood: str = "ramble") -> str:
    """Deterministic robot babble: the same sentence in the same mood always sounds the same."""
    words = SYLLABLES.get(mood, SYLLABLES["ramble"])
    seed = int(hashlib.sha1(f"{mood}:{text}".encode()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    n = max(4, min(18, len(text) // 4 + 3))
    out = []
    for i in range(n):
        w = rng.choice(words)
        if rng.random() < 0.18:
            w = f"{w}-{w}"
        out.append(w)
    return " ".join(out) + rng.choice([".", ".", "!", "?"] if mood == "curious" else [".", "!"])


@dataclass
class Memory:
    path: Path
    duck_name: str = "Microduck"
    user_name: str | None = None
    speed: float = 1.0
    aliases: dict[str, str] = field(default_factory=dict)
    last_command: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    look: dict[str, str] = field(default_factory=dict)  # colours chosen in "Build your duck"
    mood: str = "happy"

    @classmethod
    def load(cls, path: Path) -> "Memory":
        m = cls(path=path)
        try:
            data = json.loads(path.read_text())
            for k in ("duck_name", "user_name", "speed", "aliases", "last_command", "counts", "look", "mood"):
                if k in data:
                    setattr(m, k, data[k])
        except (OSError, json.JSONDecodeError):
            pass
        return m

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: getattr(self, k) for k in ("duck_name", "user_name", "speed", "aliases", "last_command",
                                               "counts", "look", "mood")}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        tmp.replace(self.path)

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("duck_name", "user_name", "speed", "aliases", "last_command",
                                               "counts", "look", "mood")}


# The scenery the hangar page can show (frontend/src/sim/scenery.ts). Photographs, drawn by the
# viewer only; the duck stands on the stage scene's pedestal in all of them but `hangar`.
SCENERY = {
    "meadow": "Meadow sunrise", "beach": "Beach sunrise", "snow": "Snowy park", "desert": "Desert reserve",
    "hills": "Cloudy hills", "city": "Shanghai nights", "venice": "Venice sunset", "stars": "Milky Way savanna",
    "orbit": "Low Earth orbit", "hangar": "Orbital hangar",
}
SCENERY_WORDS = {
    "meadow": "meadow", "field": "meadow", "grass": "meadow", "countryside": "meadow",
    "beach": "beach", "sea": "beach", "ocean": "beach", "seaside": "beach",
    "snow": "snow", "snowy park": "snow", "winter": "snow",
    "desert": "desert", "sand": "desert",
    "hills": "hills", "mountains": "hills", "clouds": "hills",
    "city": "city", "shanghai": "city", "night city": "city", "neon": "city", "the bund": "city",
    "venice": "venice", "italy": "venice", "sunset": "venice",
    "stars": "stars", "milky way": "stars", "night sky": "stars", "savanna": "stars",
    "earth": "orbit", "planet earth": "orbit", "low earth orbit": "orbit",
}
_SCENERY_RE = re.compile(
    r"(?:show me|take me to|let'?s go to|go to|switch to|change to|i want|bring me to)?\s*(?:the |a )?"
    rf"({'|'.join(sorted(map(re.escape, SCENERY_WORDS), key=len, reverse=True))})(?: (?:scenery|view|background|theme))?")
_NEXT_SCENERY_RE = re.compile(
    r"(next|change|another|new|different|shuffle|switch)( the)? (scenery|scene|background|theme|view|world)"
    r"|(shuffle|surprise me|change it up)")

# What the duck says to itself about each scenery, when nobody has said anything for a while.
SCENERY_IDLE = {
    "meadow": ["The sun's coming up over the field. Morning, everyone!", "Fresh grass, golden light. Perfect waddling weather.",
               "I counted three power pylons. They don't wave back."],
    "beach": ["Waves! I'm not waterproof, so I'll admire them from the pedestal.", "Sunrise on the beach. Sand in the servos is a no from me.",
              "If I listen very hard I can almost hear the sea. I can't, though. It's a photo."],
    "snow": ["Brr! XL330s like it cool, but not this cool.", "Fresh snow, no footprints. Should I make the first ones?",
             "Those trees look cold. I'd lend them my shell."],
    "desert": ["So much sand. Good thing my feet only touch the pedestal.", "Desert sun overhead. My shadow is tiny.",
               "Rocks, sand and sky. A very minimalist room."],
    "hills": ["Look at those clouds — they're just sky-ducks drifting by.", "Rolling hills and a big blue sky. Nice view, captain.",
              "That cloud looks like a servo horn. Or a croissant."],
    "city": ["All those lights! Bet none of them run a fifty-hertz control loop.", "Shanghai at night. The tall pink one is showing off.",
             "City lights, river breeze. I'm a cosmopolitan duck now."],
    "venice": ["Venice at sunset. I'd take a gondola, but I sink.", "The sea's gone gold. Very fancy, very Italian.",
               "Ducks have lived in Venice for centuries. I'm just visiting."],
    "stars": ["Look up — that band of light is the Milky Way, photographed for real.", "Out in the savanna, stars everywhere. Make a wish!",
              "So dark out here. Good thing I have a pedestal light."],
    "orbit": ["Earth's down there, turning slowly. Hi, everyone!", "No air up here. Lucky I don't breathe.",
              "I'm on a platform in orbit. Technically still on a floor, though. Physics insisted."],
    "hangar": ["The coolant pipes are humming again.", "Hangar's quiet tonight. Just me and the crates.",
               "If that planet out there had ducks, I'd wave."],
}

TIPS = [
    "Psst — say 'dance' and watch me go.", "Try 'when I say zoom, spin' — I'll remember it.",
    "Tap me to open my menu. The palette lets you repaint me.", "Say 'next scenery' and I'll take us somewhere new.",
    "Ask me 'status report' and I'll read you my real telemetry.", "Say 'show me the beach' — or the snow, or Venice.",
    "Hold Space to talk to me.",
]


@dataclass
class Reply:
    text: str
    mood: str = "happy"
    babble: str = ""
    id: str = ""
    t: float = 0.0
    ui: dict | None = None  # a viewer action for the page (e.g. {"scenery": "beach"}); never a robot command
    idle: bool = False      # the duck spoke on its own, nobody asked

    def as_dict(self) -> dict:
        out = dict(id=self.id, t=self.t, text=self.text, mood=self.mood, babble=self.babble)
        if self.ui:
            out["ui"] = self.ui
        if self.idle:
            out["idle"] = True
        return out


@dataclass
class ChatTurn:
    replies: list[Reply]
    commands: list
    learned: str | None = None
    parse: dict | None = None
    side: bool = True   # commands are body language accompanying a chat reply, not the request itself
    ack: bool = False   # the reply only acknowledges commands; drop it if they are refused at once
    ask_brain: bool = False  # nothing here placed the line: the Groq brain answers it (brain.py)
    brain_text: str | None = None  # what to ask it, when that is only part of the line


ACK = {
    "walk": ["Waddling!", "On my way!", "Left foot, right foot…"], "turn": ["Turning!", "Swivel time!", "Wheee, turning."],
    "emote": ["Watch this.", "Ooh, okay!", "Like this?"], "dance": ["Music, maestro!", "Dance mode: engaged!"],
    "skill": ["Trick time!", "Stand back, captain."], "enable": ["Waking up!", "Up, up, up!"],
    "disable": ["Powering down the legs."], "stop": ["Stopping."], "estop": ["EMERGENCY STOP. Holding everything."],
    "goto": ["Plotting a course!", "Navigating…"], "waypoints": ["Starting the tour!"],
    "set_environment": ["Changing scenery — hold on to your beak!"], "reset": ["Back to the start position."],
    "pause": ["Freezing the universe."], "resume": ["And… action!"], "push": ["Uh oh, incoming shove!"],
    "run_experiment": ["Science time!"], "camera": ["Camera moving."], "show_panel": ["Opening it."],
    "hold": ["Holding still."], "clear_estop": ["Emergency stop cleared. Careful now."],
}

# Activities that move the legs. A reply's body language (an emote) runs in the executor's one
# activity slot and would supersede them, so while one of these runs the duck only talks.
MOVING = {"walk", "turn", "goto", "waypoints"}
# "Stop talking" silences the voice (the page cuts the audio); it is not "stop", which halts the legs.
_QUIET_RE = re.compile(r"(stop|quit|no more|enough) (talking|speaking|quacking|chattering|babbling|noise)"
                       r"|be quiet|(keep )?quiet( please)?|hush|shush|sh+|shut up|silence|zip it|mute yourself")
# "say hello while walking": what to say, and which way to walk while saying it.
_WHILE_RE = re.compile(r"(?:say )?(.+?),? (?:while|as) (?:you(?:'re| are)? )?(?:walk(?:ing)?|mov(?:e|ing)|waddl(?:e|ing))"
                       r"( forwards?| backwards?| back)?")


class Persona:
    def __init__(self, memory: Memory, context: Callable[[], dict], brain_on: Callable[[], bool] = lambda: False):
        self.mem = memory
        self.context = context  # live facts: status, env, pose, falls …
        self.brain_on = brain_on  # is there a Groq brain for what neither persona nor parser places?
        self._ids = 0
        self._recent: list[str] = []  # the last idle lines, so it does not repeat itself

    def _reply(self, text: str, mood: str) -> Reply:
        self._ids += 1
        self.mem.mood = mood
        return Reply(text=text, mood=mood, babble=babble(text, mood), id=f"r{int(time.time() * 1000)}-{self._ids}",
                     t=time.time())

    def _speed(self, cmds: list) -> list:
        k = self.mem.speed
        if abs(k - 1.0) < 1e-6:
            return cmds
        out = []
        for c in cmds:
            if isinstance(c, C.Walk):
                c = C.Walk(vx=max(-C.VX_MAX, min(C.VX_MAX, c.vx * k)), vy=max(-C.VY_MAX, min(C.VY_MAX, c.vy * k)),
                           vyaw=max(-C.VYAW_MAX, min(C.VYAW_MAX, c.vyaw * k)), duration_s=c.duration_s)
            elif isinstance(c, C.Turn):
                c = C.Turn(vyaw=max(-C.VYAW_MAX, min(C.VYAW_MAX, c.vyaw * k)), angle_deg=c.angle_deg,
                           duration_s=c.duration_s)
            out.append(c)
        return out

    # ── facts, always from live telemetry ─────────────────────────────────

    def status_text(self) -> tuple[str, str]:
        ctx = self.context()
        st = ctx.get("status") or {}
        r, sim, safety = st.get("robotd", {}), st.get("sim", {}), st.get("safety", {})
        if not r.get("connected"):
            return "I can't feel my legs — robotd isn't connected.", "sorry"
        if safety.get("estop"):
            return "Emergency stop is latched, captain. Say 'clear emergency stop' when it's safe.", "alert"
        if r.get("policy_unavailable"):
            return f"My walking brain didn't load: {r['policy_unavailable']}", "sorry"
        if safety.get("blockers"):
            return "Not ready to move: " + "; ".join(safety["blockers"]) + ".", "alert"
        loop = r.get("loop_hz") or 0
        rtf = sim.get("rtf")
        if loop and loop < 47:
            return f"Control loop is sluggish at {loop:.0f} Hz — I might wobble.", "alert"
        bits = [f"control loop {loop:.0f} Hz", f"policy '{r.get('policy')}' driving"]
        if rtf:
            bits.append(f"world at {rtf:.2f}× real time")
        return f"No faults detected, captain. ({', '.join(bits)}.)", "proud"

    def where_text(self) -> str:
        ctx = self.context()
        env = ctx.get("env") or {}
        pose = ctx.get("pose")
        where = env.get("title") or "somewhere unnamed"
        text = f"We're in the {where.lower()}."
        if pose:
            text += f" I'm at x {pose[0]:.2f}, y {pose[1]:.2f}, facing {round(pose[2] * 57.3) % 360}°."
        if env.get("name") in ("hangar", "stage"):
            text += " Mind the pedestal edge — it's a real five-centimetre drop."
        if env.get("name") == "stage":
            text += " The world out there is a photograph; the glass ring at two metres is the real edge."
        return text

    # ── one turn of conversation ──────────────────────────────────────────

    def moving(self) -> bool:
        """Are the legs busy — a walk, turn or route running, or the keyboard driving?"""
        ctx = self.context()
        act = (ctx.get("status") or {}).get("activity") or {}
        return bool(ctx.get("teleop")) or act.get("action") in MOVING

    def handle(self, text: str) -> ChatTurn:
        turn = self._handle(text)
        # Talking never stops a walk: body language that would supersede it is left out.
        if turn.side and turn.commands and all(isinstance(c, C.Emote) for c in turn.commands) and self.moving():
            turn.commands = []
        return turn

    def _handle(self, text: str) -> ChatTurn:
        raw = text.strip()
        norm, _wake = strip_wake_word(normalize(raw))
        norm = re.sub(r"^(please|hey|ok|okay)\s+", "", norm).strip(" .!?")
        R = self._reply
        name = self.mem.user_name
        you = f", {name}" if name else ""
        self.mem.counts["turns"] = self.mem.counts.get("turns", 0) + 1

        # ── teaching ──────────────────────────────────────────────────────
        low, _ = strip_wake_word(raw.lower().strip().lstrip(",").strip())
        low = low.lstrip(", ").strip()
        m = re.match(r"(?:when i say|if i say)\s+['\"]?(.+?)['\"]?\s*(?:,|:|\bthen\b|\byou should\b|\bdo\b)\s*(.+?)[.!]?$", low) or \
            re.match(r"(?:teach|learn)\s*:?\s*['\"]?(.+?)['\"]?\s+(?:means|is|=)\s+(.+?)[.!]?$", low)
        if m:
            word, meaning = m.group(1).strip(" '\""), m.group(2).strip(" '\"")
            if len(word.split()) > 4:
                return ChatTurn([R("That's a long word. Keep it to four words or fewer?", "curious")], [])
            p = parse(meaning)
            if not p.commands or p.errors or p.unmatched:
                why = (p.errors or [f"I don't know how to '{' '.join(p.unmatched) or meaning}'"])[0]
                return ChatTurn([R(f"I can't learn that: {why}.", "sorry")], [], parse=p.as_dict())
            self.mem.aliases[word] = meaning
            self.mem.save()
            return ChatTurn([R(f"Got it{you}! When you say '{word}', I'll {meaning}.", "proud")], [],
                            learned=word, parse=p.as_dict())
        m = re.match(r"forget\s+(.+)$", norm)
        if m and m.group(1).strip(" '\"") in self.mem.aliases:
            w = m.group(1).strip(" '\"")
            del self.mem.aliases[w]
            self.mem.save()
            return ChatTurn([R(f"Forgotten: '{w}'.", "curious")], [])
        if re.fullmatch(r"what (have you learned|do you know|did i teach you)|(show )?(your )?(words|aliases)", norm):
            if not self.mem.aliases:
                return ChatTurn([R("Nothing yet! Teach me: 'when I say zoom, walk forward fast for three seconds'.",
                                   "curious")], [])
            listing = "; ".join(f"'{k}' → {v}" for k, v in self.mem.aliases.items())
            return ChatTurn([R(f"I know: {listing}.", "proud")], [])
        m = re.match(r"(?:my name is|call me|i am|i'm)\s+([a-z][a-z' -]{0,30})$", norm)
        if m and not re.search(r"\b(tired|sad|happy|bored|fine|ok|good|here|back)\b", m.group(1)):
            self.mem.user_name = m.group(1).strip().title()
            self.mem.save()
            return ChatTurn([R(f"Nice to meet you, {self.mem.user_name}! I'll remember that.", "happy")],
                            [C.Emote(gesture="nod", duration_s=1.4)])
        m = re.match(r"(?:your name is|i(?:'ll| will) call you|you are called)\s+([a-z][a-z' -]{0,30})$", norm)
        if m:
            self.mem.duck_name = m.group(1).strip().title()
            self.mem.save()
            return ChatTurn([R(f"{self.mem.duck_name}! I love it.", "proud")], [C.Emote(gesture="celebrate", duration_s=1.6)])
        if re.fullmatch(r"what('?s| is) my name|who am i|do you know me", norm):
            return ChatTurn([R(f"You're {name}!" if name else "You haven't told me! Say 'my name is …'.",
                               "happy" if name else "curious")], [])
        if re.fullmatch(r"(who|what) are you|what('?s| is) your name|introduce yourself", norm):
            return ChatTurn([R(f"I'm {self.mem.duck_name}: a Microduck — 25 cm, 0.74 kg, fourteen XL330 servos, "
                               "walking on a policy trained in MuJoCo.", "proud")], [C.Emote(gesture="bow", duration_s=1.6)])

        # ── preferences and repetition ────────────────────────────────────
        if re.fullmatch(r"(go )?(faster|quicker|speed up|hurry( up)?)", norm):
            self.mem.speed = min(1.6, round(self.mem.speed + 0.2, 2))
            self.mem.save()
            return ChatTurn([R(f"Zoom! Speed set to {self.mem.speed:.1f}×.", "happy")], [])
        if re.fullmatch(r"(go )?(slower|slow down|easy|gently|calm down)", norm):
            self.mem.speed = max(0.6, round(self.mem.speed - 0.2, 2))
            self.mem.save()
            return ChatTurn([R(f"Nice and easy: speed {self.mem.speed:.1f}×.", "sleepy")], [])
        if re.fullmatch(r"(do it )?(again|repeat|one more( time)?|encore|more)", norm):
            if not self.mem.last_command:
                return ChatTurn([R("Again what? We haven't done anything yet!", "curious")], [])
            return self._commands(self.mem.last_command, prefix="Again! ")

        # ── conversation ──────────────────────────────────────────────────
        if _QUIET_RE.fullmatch(norm):
            reply = R("Okay — beak shut.", "sleepy")
            reply.ui = {"voice": "stop"}  # the page stops the voice and does not say this line aloud
            return ChatTurn([reply], [])
        m = _WHILE_RE.fullmatch(norm)
        if m:
            said = self._handle(m.group(1))
            # a movement request inside ("turn left while walking") is not something to *say*
            if said.side or not said.commands:
                walk = "walk backward" if m.group(2) and "back" in m.group(2) else "walk forward"
                p = parse(walk)
                return ChatTurn(said.replies, self._speed(p.commands), parse=p.as_dict(), side=False,
                                ask_brain=said.ask_brain, brain_text=m.group(1) if said.ask_brain else None)
        if re.fullmatch(r"(hello|hi|hey|hiya|howdy|greetings|good (morning|afternoon|evening)|yo|sup)( there)?( duck| buddy)?", norm) or norm == "":
            return ChatTurn([R(f"Oh! Hi{you}! Hi hi hi!", "happy")], [C.Emote(gesture="nod", duration_s=1.4)])
        if re.fullmatch(r"(status( report)?|report|how are you( doing)?|how do you feel|diagnostics|are you ok|system check)", norm):
            text_, mood = self.status_text()
            return ChatTurn([R(text_, mood)], [])
        if re.fullmatch(r"where are we|where am i|where are you|what is this place|location", norm):
            return ChatTurn([R(self.where_text(), "curious")], [C.Emote(gesture="look_left", duration_s=1.0),
                                                                 C.Emote(gesture="look_right", duration_s=1.0)])
        if re.fullmatch(r"(good|nice|clever|smart|well done|great job|good job|good boy|good girl|good duck|bravo)( duck| job| work)?", norm):
            return ChatTurn([R("Hehe! Thank you! *happy wiggle*", "happy")], [C.Emote(gesture="wiggle", duration_s=1.6)])
        if re.fullmatch(r"(bad|naughty|silly) duck|bad", norm):
            return ChatTurn([R("Sorry, captain… I'll do better.", "sorry")], [C.Emote(gesture="sleepy", duration_s=1.6)])
        if re.fullmatch(r"(tell me )?(a )?joke|make me laugh|say something funny", norm):
            joke = JOKES[self.mem.counts.get("jokes", 0) % len(JOKES)]
            self.mem.counts["jokes"] = self.mem.counts.get("jokes", 0) + 1
            return ChatTurn([R(joke, "ramble")], [C.Emote(gesture="celebrate", duration_s=1.2)])
        if re.fullmatch(r"(watch out|look out|careful|danger|incoming)", norm):
            return ChatTurn([R("Eep! Stopping and looking around!", "alert")],
                            [C.Stop(), C.Emote(gesture="look_left", duration_s=0.9), C.Emote(gesture="look_right", duration_s=0.9)])
        if re.fullmatch(r"good ?night|go to sleep|bed ?time|sleep tight", norm):
            return ChatTurn([R(f"Good night{you}. Powering down the quack drive… zzz.", "sleepy")],
                            [C.Skill(skill="sit_toggle")])
        if re.fullmatch(r"(thanks|thank you|cheers|ty)( duck)?", norm):
            return ChatTurn([R("Any time!", "happy")], [])
        if re.fullmatch(r"i love you|love you|you('?re| are) (cute|the best|adorable)", norm):
            return ChatTurn([R("Bweep! My servos are blushing.", "happy")], [C.Emote(gesture="celebrate", duration_s=1.6)])
        if re.fullmatch(r"why did (you|it) fall|what happened", norm):
            ctx = self.context()
            return ChatTurn([R(ctx.get("fall_text") or "I haven't fallen. Yet.", "sorry")], [])

        # ── taught words ──────────────────────────────────────────────────
        if norm in self.mem.aliases:
            return self._commands(self.mem.aliases[norm], prefix=f"'{norm}' — ")

        # ── scenery (the page's backdrop; the stage scene is the only physics it needs) ──
        m = _SCENERY_RE.fullmatch(norm)
        if m or _NEXT_SCENERY_RE.fullmatch(norm):
            return self._scenery(SCENERY_WORDS[m.group(1)] if m else "next")

        return self._commands(raw)

    def _scenery(self, which: str) -> ChatTurn:
        env = (self.context().get("env") or {}).get("name")
        title = SCENERY.get(which, "somewhere new")
        text = "Ooh, somewhere new! Hold on to your beak." if which == "next" else f"{title}? Coming right up!"
        cmds = []
        if env != "stage":
            # the photographed worlds surround the stage scene's pedestal; get there the normal way
            cmds = [C.SetEnvironment(environment="stage")]
            text += " Moving us to the scenery stage first."
        reply = self._reply(text, "happy")
        reply.ui = {"scenery": which}
        return ChatTurn([reply], cmds)

    # ── talking to itself ─────────────────────────────────────────────────

    def idle(self, hint: dict) -> Reply:
        """Something to say when nobody has spoken for a while — never a command, and every fact
        in it read from live telemetry. `hint` is what the page knows: the scenery on show, the
        background ducks' names, and how many idle lines in a row went unanswered."""
        ctx = self.context()
        st = ctx.get("status") or {}
        r, sim = st.get("robotd") or {}, st.get("sim") or {}
        scenery = str(hint.get("scenery") or "")
        cousins = [str(c)[:16] for c in (hint.get("cousins") or [])][:8]
        unanswered = int(hint.get("unanswered") or 0)
        name = self.mem.user_name
        rng = random.Random()
        options: list[tuple[str, str]] = []

        if unanswered >= 3:
            options += [(f"Helloooo{', ' + name if name else ''}? I'll just practise standing. I'm very good at it.", "sleepy"),
                        ("Still here! Standing majestically. Whenever you're ready.", "sleepy"),
                        ("I'll be quiet for a bit. Say anything and I'm all yours.", "sleepy")]
        else:
            options += [(line, "happy") for line in SCENERY_IDLE.get(scenery, [])]
            if cousins:
                a = rng.choice(cousins)
                b = rng.choice([c for c in cousins if c != a] or [a])
                options += [(f"Look at {a} go! Somebody's had too much battery.", "happy"),
                            (f"{a} and {b} are playing tag again. {a} is cheating.", "ramble"),
                            ("Those are my cousins. They're viewer animations — I'm the only one here with real physics!", "proud"),
                            (f"{a} keeps hopping. Show-off.", "ramble")]
            if r.get("connected"):
                hz = r.get("loop_hz")
                if hz:
                    options.append((f"Control loop's ticking at {hz:.0f} hertz. Steady as a duck.", "proud"))
                if r.get("policy"):
                    options.append((f"Policy '{r['policy']}' has my legs right now.", "curious"))
            if sim.get("rtf"):
                options.append((f"MuJoCo is running the world at {sim['rtf']:.2f}× real time. Not too fast, not too slow.", "curious"))
            up = st.get("uptime_s")
            if up and up > 120:
                options.append((f"I've been awake for {int(up // 60)} minutes. Feeling great.", "happy"))
            if ctx.get("fall_text"):
                options.append(("I took a tumble earlier. Ask me 'why did you fall' — I kept the data.", "sorry"))
            if self.mem.aliases:
                w = rng.choice(list(self.mem.aliases))
                options.append((f"You taught me '{w}'. Want to see it again?", "curious"))
            if name:
                options += [(f"Hey {name}, what shall we do next?", "curious"), (f"{name}! Still there? Just checking.", "curious")]
            hour = time.localtime().tm_hour
            if hour >= 23 or hour < 5:
                options.append(("It's late. Ducks need beauty sleep too, you know.", "sleepy"))
            elif hour < 10:
                options.append(("Good morning! Coffee for you, fresh calibration for me.", "happy"))
            options += [(tip, "curious") for tip in TIPS]
            options.append((JOKES[rng.randrange(len(JOKES))], "ramble"))

        fresh = [o for o in options if o[0] not in self._recent] or options
        text, mood = rng.choice(fresh)
        self._recent.append(text)
        del self._recent[:-12]
        reply = self._reply(text, mood)
        reply.idle = True
        return reply

    def _commands(self, text: str, prefix: str = "") -> ChatTurn:
        p = parse(text)
        # Open questions go to the brain: nothing parsed, or most of the line was not a command.
        # A line with a stop in it never does — the parser's stop runs, deterministically.
        stopping = any(c.action in ("stop", "estop") for c in p.commands)
        unplaced = not p.commands or len(" ".join(p.unmatched).split()) >= 3
        if self.brain_on() and unplaced and not stopping and not p.errors and not prefix:
            return ChatTurn([], [], parse=p.as_dict(), ask_brain=True)
        if p.errors:
            return ChatTurn([self._reply(f"{prefix}I can't: {p.errors[0]}", "sorry")], [], parse=p.as_dict())
        if not p.commands:
            words = " ".join(p.unmatched) or p.normalized
            return ChatTurn([self._reply(f"Bwuh? I don't know '{words}' yet. Teach me: "
                                         f"'when I say {words}, …'", "curious")], [], parse=p.as_dict())
        cmds = self._speed(p.commands)
        self.mem.last_command = text
        self.mem.save()
        first = cmds[0].action
        ack = random.Random(text).choice(ACK.get(first, ["Okay!"]))
        if p.unmatched:
            ack += f" (I skipped '{' '.join(p.unmatched)}'.)"
        return ChatTurn([self._reply(prefix + ack, "proud" if first in ("dance", "skill") else "happy")], cmds,
                        parse=p.as_dict(), side=False, ack=True)

    def outcome_reply(self, action: str, status: str, message: str) -> Reply | None:
        """What the duck says when a command it started has finished — from the real result."""
        if status in ("completed", "delivered"):
            if action in ("status", "help"):
                return self._reply(message, "curious")
            if action in ("camera", "show_panel", "stop", "estop", "clear_estop", "pause", "resume"):
                return None
            lead = {"dance": "Ta-daaa! ", "skill": "Nailed it! ", "goto": "Made it! ", "turn": "", "walk": ""}.get(action, "Done! ")
            return self._reply(lead + message[0].upper() + message[1:] + ".", "proud")
        if status in ("superseded",):
            return None
        if status == "cancelled":
            return self._reply(f"Stopped the {action.replace('_', ' ')}.", "alert")
        return self._reply(f"I couldn't {action.replace('_', ' ')}: {message}", "sorry")
