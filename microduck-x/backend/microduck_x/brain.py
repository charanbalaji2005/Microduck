"""Open conversation through Groq: questions the persona and the parser cannot place.

The turn order is unchanged (HANGAR.md): persona phrases, then the deterministic parser, and only
what is left — "why is the sky blue", "explain recursion" — comes here. Stops and the e-stop never
do: the parser answers them first, and neither is an action this module can ask for.

What the model can do, and nothing else:

* **Talk.** Its text streams to the page and is then voiced by the duck's own synth (the browser,
  duckVoice.ts) — Groq produces words, never the duck's voice. Reasoning (`delta.reasoning`) is
  never shown or spoken.
* **Ask for allowlisted actions** through one function tool, `duck_actions`. Each is validated
  here, mapped onto the same commands a chat chip produces, and handed to the same executor; the
  model is told what was *started*, and the outcome is narrated later from the execution record.
* **Look things up** through `look_up`, offered only when a retriever is configured
  (`GROQ_WEB_SEARCH=1`: Groq's built-in browser search). Sources are shown only when a lookup
  actually ran and returned them.

It never sees a shell, a file or a key, and nothing it returns is executed as code.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import commands as C
from .config import Settings
from .parser import parse

EFFORTS = ("low", "medium", "high")  # what Groq accepts for gpt-oss (docs: reasoning)
MAX_ROUNDS = 3  # model → tool → model … at most this many completions per turn
MAX_SESSIONS = 32

SYSTEM_PROMPT = """You are Microduck, a small robot duck: 25 cm tall, 0.74 kg, fourteen XL330 servos, \
walking on a reinforcement-learning policy. Right now you live in a MuJoCo physics simulation shown \
in a 3D hangar in the user's browser. You are curious, playful, warm and genuinely knowledgeable.

How to answer:
- Be accurate. If you are unsure, say so. Never invent facts, sources, numbers, quotes or links.
- Match the user's level. Use a short example or numbered steps when that helps.
- The chat panel renders Markdown: headings, lists, **bold**, `inline code`, and tables (GitHub \
pipe syntax). Use them when they help; plain sentences for plain answers.
- Code goes in fenced blocks with the language, and a file name when there is one: ```python \
title="app.py". Write complete code; never elide parts with "..." unless asked for a sketch.
- Mathematics uses LaTeX: $x^2$ inline, $$\\int_0^1 x\\,dx$$ on its own line for displayed \
equations. For problems to solve, show numbered steps ("**Step 1:** …") and end with the answer. \
Say a result is checked only if you actually checked it.
- When it helps, end with one line of up to three short follow-up questions the user might ask \
next about the topic (never about the live facts below), exactly in this form: \
>> follow-ups: first question | second question
- Your first sentence is read out by your voice, so make it short and natural. Keep answers to a \
few sentences unless the user asks for detail, code or a step-by-step explanation.
- If a request is ambiguous, ask one short follow-up question instead of guessing.
- A little duck personality is welcome (a "quack" now and then), but the answer comes first.

What you can and cannot do:
- You can move only by calling the duck_actions tool, with actions from its list. Calling it \
*starts* an action; the robot reports the real outcome afterwards. Never say an action is done or \
describe how it went — say you are starting it. Never claim to do anything that is not in the list.
- {lookup}
- You remember this conversation only. You do not remember earlier conversations. The user's name, \
if listed in the live facts, is saved by the app.
- You cannot see through a camera, browse files, run code or reach anything outside this chat."""

LOOKUP_ON = ("For current events, prices, recent releases or anything after your training data, call "
             "look_up. Cite only sources it returned, and say you looked it up.")
LOOKUP_OFF = ("You have no web access in this session. For current events or recent facts, say your "
              "knowledge may be out of date instead of guessing.")


# ── the action allowlist ───────────────────────────────────────────────────────────────────────

ACTIONS = {
    "walk_forward": "walk forward (seconds: 0.5–10, default 2)",
    "walk_backward": "walk backward (seconds: 0.5–10, default 2)",
    "turn_left": "turn left in place (degrees: 5–360, default 90)",
    "turn_right": "turn right in place (degrees: 5–360, default 90)",
    "look_around": "look left, then right",
    "stop_movement": "stop walking or turning",
    "start_exploring": "walk the scene's waypoints",
    "rest": "sit down, or stand back up if sitting",
    "speak": "say something out loud (your reply is always spoken; this changes nothing)",
}


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal[tuple(ACTIONS)]  # type: ignore[valid-type]
    seconds: float | None = Field(None, ge=0.5, le=10)
    degrees: float | None = Field(None, ge=5, le=360)


class ActionList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actions: list[ActionRequest] = Field(min_length=1, max_length=3)


def commands_for(a: ActionRequest) -> list:
    """The commands an allowlisted action stands for: the same ones a chat chip or a phrase makes."""
    secs = a.seconds or 2.0
    deg = a.degrees or 90.0
    return {
        "walk_forward": lambda: [C.Walk(vx=0.3, duration_s=secs)],
        "walk_backward": lambda: [C.Walk(vx=-0.38, duration_s=secs)],
        "turn_left": lambda: [C.Turn(vyaw=0.9, angle_deg=deg)],
        "turn_right": lambda: [C.Turn(vyaw=-0.9, angle_deg=-deg)],
        "look_around": lambda: parse("look around").commands,
        "stop_movement": lambda: [C.Stop()],
        "start_exploring": lambda: parse("explore").commands,
        "rest": lambda: parse("rest").commands,
        "speak": lambda: [],
    }[a.action]()


TOOL_ACTIONS = {
    "type": "function",
    "function": {
        "name": "duck_actions",
        "description": "Start one to three physical actions of the robot duck, in order. "
                       + "; ".join(f"{k}: {v}" for k, v in ACTIONS.items()),
        "parameters": {
            "type": "object",
            "properties": {"actions": {"type": "array", "minItems": 1, "maxItems": 3, "items": {
                "type": "object", "additionalProperties": False, "required": ["action"],
                "properties": {"action": {"type": "string", "enum": list(ACTIONS)},
                               "seconds": {"type": "number", "minimum": 0.5, "maximum": 10},
                               "degrees": {"type": "number", "minimum": 5, "maximum": 360}}}}},
            "required": ["actions"], "additionalProperties": False,
        },
    },
}

TOOL_LOOKUP = {
    "type": "function",
    "function": {
        "name": "look_up",
        "description": "Search for current or specific facts. Returns text and the sources it came from.",
        "parameters": {"type": "object", "additionalProperties": False, "required": ["query"],
                       "properties": {"query": {"type": "string", "maxLength": 300}}},
    },
}


# ── retrieval: an interface, one implementation ───────────────────────────────────────────────

@dataclass
class Retrieved:
    text: str
    sources: list[dict]  # [{"title": str, "url": str}]


class Retriever(Protocol):
    """Anything that can answer a query with text and its sources: a web search, approved docs, a
    local RAG index. `brain.look_up` offers whichever one is configured."""
    name: str

    async def search(self, query: str) -> Retrieved: ...


def sources_in(obj: Any, text: str = "") -> list[dict]:
    """Every distinct http(s) URL in a response (executed_tools results carry title + url), plus any
    written into the text. Nothing is shown that the response did not contain."""
    found: OrderedDict[str, str] = OrderedDict()

    def walk(o):
        if isinstance(o, dict):
            url = o.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                found.setdefault(url, str(o.get("title") or url)[:120])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    for url in re.findall(r"https?://[^\s)\]>\"'】]+", text or ""):
        found.setdefault(url.rstrip(".,;"), url.rstrip(".,;"))
    return [{"title": t, "url": u} for u, t in list(found.items())[:8]]


class GroqBrowserSearch:
    """Groq's built-in `browser_search` (gpt-oss models), in a request of its own: the docs do not
    say it combines with function tools, and it does not stream sources, so it runs as a lookup
    whose answer and sources go back to the conversation as a tool result."""
    name = "groq_browser_search"

    def __init__(self, client_fn: Callable[[], Any], model: str):
        self.client_fn, self.model = client_fn, model

    async def search(self, query: str) -> Retrieved:
        res = await self.client_fn().chat.completions.create(
            model=self.model, tools=[{"type": "browser_search"}], tool_choice="required",
            reasoning_effort="low", max_completion_tokens=1024, temperature=0.3,
            messages=[{"role": "system", "content": "Search the web and answer the query with concise facts. "
                                                     "Name the sources you used."},
                      {"role": "user", "content": query}])
        msg = res.choices[0].message
        dumped = msg.model_dump() if hasattr(msg, "model_dump") else dict(msg)
        text = (dumped.get("content") or "").strip()
        return Retrieved(text=text[:4000], sources=sources_in(dumped.get("executed_tools"), text))


# ── the reply's shape, for the page ────────────────────────────────────────────────────────────

FOLLOWUPS = re.compile(r"^\s*>>\s*follow-?ups?\s*:\s*(.+?)\s*$", re.I | re.M)
FENCE = re.compile(r"^[ \t]*```[ \t]*([\w+#.-]*)([^\n]*)\n(.*?)(?:^[ \t]*```[ \t]*$|\Z)", re.S | re.M)
DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)
INLINE_MATH = re.compile(r"(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)|\\\((.+?)\\\)")
FILENAME = re.compile(r"(?:title|file(?:name)?)\s*=\s*[\"']?([\w./-]{1,80})")
KINDS = ("conversation", "math", "code", "long", "list", "error", "action")


def split_followups(text: str) -> tuple[str, list[str]]:
    """The answer without its `>> follow-ups:` line, and those questions (at most three, short)."""
    found: list[str] = []
    for m in FOLLOWUPS.finditer(text):
        found += [q.strip(" -•\t") for q in m.group(1).split("|")]
    clean = FOLLOWUPS.sub("", text).rstrip()
    return clean, [q[:120] for q in found if 3 <= len(q) <= 200][:3]


def structure(text: str, actions: list[dict] | None = None, error: str | None = None) -> dict:
    """What the page needs to render a reply well, derived from the reply itself (never trusted
    from the model as JSON): its kind, a one-line summary, its code blocks and its mathematics."""
    code = []
    for m in FENCE.finditer(text):
        lang, rest, body = m.group(1).lower()[:20], m.group(2), m.group(3)
        fname = FILENAME.search(rest)
        code.append({"language": lang or "text", "filename": fname.group(1) if fname else None,
                     "lines": body.rstrip("\n").count("\n") + 1})
    prose = FENCE.sub(" ", text)
    display = [a or b for a, b in DISPLAY_MATH.findall(prose)]
    inline = [a or b for a, b in INLINE_MATH.findall(DISPLAY_MATH.sub(" ", prose))]
    lines = text.count("\n") + 1
    listy = len(re.findall(r"^\s*(?:[-*•]|\d+[.)])\s+|^\s*\|.*\|\s*$", text, re.M))
    if error:
        kind = "error"
    elif code:
        kind = "code"
    elif display or len(inline) >= 2 or re.search(r"\*\*step\s*1\b", text, re.I) and inline:
        kind = "math"
    elif len(text) > 900 or lines > 16:
        kind = "long"
    elif listy >= 3:
        kind = "list"
    elif actions:
        kind = "action"
    else:
        kind = "conversation"
    no_math = INLINE_MATH.sub(" ", DISPLAY_MATH.sub(" ", prose))
    words = re.sub(r"\s+", " ", re.sub(r"[#*`>|$\\]", "", no_math)).strip()
    first = re.split(r"(?<=[.!?])\s", words, maxsplit=1)[0]
    return {"kind": kind, "summary": first[:160], "code": code[:20], "math": len(display) + len(inline),
            "length": len(text), "lines": lines}


# ── errors the user can understand ─────────────────────────────────────────────────────────────

class BrainError(Exception):
    def __init__(self, kind: str, message: str, retry: bool = True):
        super().__init__(message)
        self.kind, self.message, self.retry = kind, message, retry


def classify(error: BaseException) -> BrainError:
    """Groq SDK exception → a safe sentence. Matched by class name so the module loads without the
    SDK; the SDK's own message (which may echo request details) is never shown."""
    names = {k.__name__ for k in type(error).__mro__}
    if isinstance(error, BrainError):
        return error
    if names & {"AuthenticationError", "PermissionDeniedError"}:
        return BrainError("auth", "My Groq API key was refused. Check GROQ_API_KEY on the server.", retry=False)
    if "RateLimitError" in names:
        return BrainError("rate_limit", "Groq says I'm asking too fast. Give me a few seconds and try again.")
    if names & {"APITimeoutError", "TimeoutError"}:
        return BrainError("timeout", "Groq took too long to answer. Try again?")
    if "APIConnectionError" in names:
        return BrainError("connection", "I can't reach Groq right now — is the internet up?")
    if names & {"BadRequestError", "NotFoundError", "UnprocessableEntityError"}:
        return BrainError("bad_request", "Groq didn't accept that request (check GROQ_MODEL).", retry=False)
    if names & {"InternalServerError", "APIStatusError", "APIError"}:
        return BrainError("upstream", "Groq had a problem answering. Try again in a moment.")
    return BrainError("internal", "Something went wrong while I was thinking. Try again?")


# ── sessions and pacing ─────────────────────────────────────────────────────────────────────────

class Sessions:
    """Per-session conversation history, bounded in messages and characters, a few sessions kept."""

    def __init__(self, max_messages: int, max_chars: int):
        self.max_messages, self.max_chars = max_messages, max_chars
        self._s: OrderedDict[str, list[dict]] = OrderedDict()

    def history(self, sid: str) -> list[dict]:
        return list(self._s.get(sid, []))

    def add(self, sid: str, user: str, assistant: str) -> None:
        h = self._s.pop(sid, [])
        h += [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
        while h and (len(h) > self.max_messages or sum(len(m["content"]) for m in h) > self.max_chars):
            del h[:2]  # the oldest exchange goes first
        self._s[sid] = h
        while len(self._s) > MAX_SESSIONS:
            self._s.popitem(last=False)

    def clear(self, sid: str) -> None:
        self._s.pop(sid, None)

    def drop_last(self, sid: str, user: str) -> bool:
        """Forget the last exchange if it was this question — "regenerate" asks it afresh."""
        h = self._s.get(sid)
        if h and len(h) >= 2 and h[-2]["content"] == user:
            del h[-2:]
            return True
        return False


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._t: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self._t and now - self._t[0] > 60:
            self._t.popleft()
        if len(self._t) >= self.per_minute:
            return False
        self._t.append(now)
        return True


# ── the brain ───────────────────────────────────────────────────────────────────────────────────

@dataclass
class Answer:
    text: str
    actions: list[dict] = field(default_factory=list)  # what was started: action, command id, status
    followups: list[str] = field(default_factory=list)  # the model's suggested next questions
    sources: list[dict] = field(default_factory=list)  # only from a lookup that ran
    looked_up: bool = False


Act = Callable[[list], Awaitable[list[dict]]]  # commands → execution summaries (the executor)


class Brain:
    def __init__(self, settings: Settings, client: Any = None, retriever: Retriever | None = None):
        self.s = settings
        self._client = client
        self.problem = self._check()
        self.sessions = Sessions(settings.groq_history_messages, settings.groq_history_chars)
        self.limiter = RateLimiter(settings.groq_rate_per_min)
        self.retriever = retriever
        if retriever is None and settings.groq_web_search and not self.problem:
            self.retriever = GroqBrowserSearch(self.client, settings.groq_model)

    def _check(self) -> str | None:
        """Why the brain is off, or None. Checked once, at start; never mentions the key's value."""
        s = self.s
        if not s.groq_api_key:
            return "GROQ_API_KEY is not set"
        if not s.groq_model:
            return "GROQ_MODEL is empty"
        if s.groq_reasoning_effort not in EFFORTS:
            return f"GROQ_REASONING_EFFORT must be one of {', '.join(EFFORTS)}"
        if s.groq_max_tokens < 64 or s.groq_timeout_s <= 0 or s.groq_history_messages < 0 or s.groq_rate_per_min < 1:
            return "a GROQ_* number is out of range"
        if self._client is None:
            try:
                import groq  # noqa: F401
            except ImportError:
                return "the groq package is not installed (pip install -r backend/requirements.txt)"
        return None

    @property
    def enabled(self) -> bool:
        return self.problem is None

    def status(self) -> dict:
        return dict(enabled=self.enabled, provider="groq", model=self.s.groq_model if self.enabled else None,
                    reasoning_effort=self.s.groq_reasoning_effort, problem=self.problem,
                    retrieval=self.retriever.name if self.retriever else None)

    def client(self):
        if self._client is None:
            import groq
            self._client = groq.AsyncGroq(api_key=self.s.groq_api_key, timeout=self.s.groq_timeout_s, max_retries=1)
        return self._client

    def _messages(self, sid: str, text: str, facts: str) -> list[dict]:
        system = SYSTEM_PROMPT.format(lookup=LOOKUP_ON if self.retriever else LOOKUP_OFF)
        out = [{"role": "system", "content": system}]
        if facts:
            out.append({"role": "system", "content": "Live facts, read from the robot just now:\n" + facts})
        return out + self.sessions.history(sid) + [{"role": "user", "content": text}]

    async def answer(self, sid: str, text: str, facts: str, on_delta: Callable[[str], None], act: Act) -> Answer:
        """One turn. Streams text through `on_delta(full_text_so_far)`; raises BrainError."""
        if not self.enabled:
            raise BrainError("disabled", f"My Groq brain is off: {self.problem}.", retry=False)
        try:
            async with asyncio.timeout(self.s.groq_timeout_s * MAX_ROUNDS + 5):
                ans = await self._turn(self._messages(sid, text, facts), on_delta, act)
        except (BrainError, asyncio.CancelledError):
            raise
        except BaseException as error:  # noqa: BLE001 — every failure becomes a sentence
            raise classify(error) from None
        note = "".join(f"\n[started {a['action']}: {a['status']}]" for a in ans.actions)
        self.sessions.add(sid, text, (ans.text or "(no words)") + note)
        return ans

    async def _turn(self, messages: list[dict], on_delta: Callable[[str], None], act: Act) -> Answer:
        tools = [TOOL_ACTIONS] + ([TOOL_LOOKUP] if self.retriever else [])
        ans, shown = Answer(text=""), ""
        for _ in range(MAX_ROUNDS):
            content, calls = await self._stream(messages, tools, lambda t: on_delta((shown + t).strip()))
            shown = (shown + content).strip() + (" " if content and calls else "")
            if not calls:
                break
            messages.append({"role": "assistant", "content": content or None, "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["args"]}}
                for c in calls]})
            for c in calls:
                result = await self._tool(c, ans, act)
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(result)})
        ans.text, ans.followups = split_followups(shown.strip())
        return ans

    async def _stream(self, messages, tools, on_text) -> tuple[str, list[dict]]:
        s = self.s
        stream = await self.client().chat.completions.create(
            model=s.groq_model, messages=messages, temperature=s.groq_temperature, top_p=1,
            max_completion_tokens=s.groq_max_tokens, reasoning_effort=s.groq_reasoning_effort,
            tools=tools, tool_choice="auto", stream=True)
        content, calls = "", {}
        try:
            async for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue  # usage-only and keep-alive chunks
                delta = chunk.choices[0].delta
                if delta is None:
                    continue
                if delta.content:  # reasoning arrives in delta.reasoning and is ignored
                    content += delta.content
                    on_text(content)
                for tc in delta.tool_calls or []:
                    entry = calls.setdefault(tc.index if tc.index is not None else len(calls),
                                             {"id": "", "name": "", "args": ""})
                    entry["id"] = tc.id or entry["id"]
                    if tc.function:
                        entry["name"] += tc.function.name or ""
                        entry["args"] += tc.function.arguments or ""
        finally:
            close = getattr(stream, "close", None)
            if close:
                res = close()
                if asyncio.iscoroutine(res):
                    await res
        out = [dict(c, id=c["id"] or f"call_{i}") for i, c in sorted(calls.items())]
        return content, out

    async def _tool(self, call: dict, ans: Answer, act: Act) -> dict:
        """Run one tool call. Anything malformed or not allowlisted is refused and said so."""
        try:
            args = json.loads(call["args"] or "{}")
        except json.JSONDecodeError:
            return {"error": "arguments were not valid JSON; nothing was done"}
        if call["name"] == "duck_actions":
            try:
                req = ActionList.model_validate(args)
            except ValidationError as error:
                return {"error": f"not an allowed action list ({error.errors()[0]['msg']}); nothing was done",
                        "allowed": list(ACTIONS)}
            # one sequence for the executor: submitted one by one, each would supersede the last
            plan = [(a.action, c) for a in req.actions for c in commands_for(a)]
            started = [{"action": a.action, "status": "nothing to do"} for a in req.actions if not commands_for(a)]
            for (name, _), ex in zip(plan, await act([c for _, c in plan]) if plan else []):
                entry = {"action": name, "id": ex.get("id"), "status": ex.get("status"),
                         "message": (ex.get("message") or "")[:160]}
                started.append(entry)
                ans.actions.append(entry)
            return {"started": started, "note": "Started, not finished. The robot reports the outcome itself."}
        if call["name"] == "look_up" and self.retriever:
            query = str(args.get("query", ""))[:300]
            if not query.strip():
                return {"error": "empty query; nothing was looked up"}
            try:
                got = await self.retriever.search(query)
            except Exception as error:  # noqa: BLE001
                return {"error": f"the lookup failed ({classify(error).kind}); nothing was retrieved"}
            ans.looked_up = True
            ans.sources += [s for s in got.sources if s not in ans.sources]
            return {"retrieved_by": self.retriever.name, "text": got.text, "sources": got.sources}
        return {"error": f"there is no tool called {call['name']!r}; nothing was done"}


def mood_of(text: str) -> str:
    """The reply's tone, for the babble and the voice tags (happy, curious, sorry)."""
    t = text.strip()
    if re.search(r"\b(sorry|can't|cannot|unable|don't know)\b", t[:200], re.I):
        return "sorry"
    if t.endswith("?"):
        return "curious"
    return "happy"
