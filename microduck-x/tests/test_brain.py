"""The Groq brain, against a scripted stand-in for the Groq SDK: no key, no network, no model.

What is checked is ours — streaming assembly, reasoning kept out, the action allowlist, sources
only from a lookup that ran, bounded memory, safe errors, and the chat endpoints around it."""
import asyncio
import json
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from microduck_x import commands as C
from microduck_x.app import create_app
from microduck_x.brain import Brain, BrainError, Retrieved, Sessions, classify, sources_in
from microduck_x.persona import Memory, Persona


# ── a stand-in for groq.AsyncGroq ─────────────────────────────────────────────

def chunk(content=None, reasoning=None, tool_calls=None):
    return NS(choices=[NS(delta=NS(content=content, reasoning=reasoning, tool_calls=tool_calls))])


def tool_chunks(name, args, cid="call_1", index=0):
    """A tool call split across deltas the way a stream delivers it."""
    half = len(args) // 2
    return [chunk(tool_calls=[NS(index=index, id=cid, function=NS(name=name, arguments=args[:half]))]),
            chunk(tool_calls=[NS(index=index, id=None, function=NS(name=None, arguments=args[half:]))])]


class Stream:
    def __init__(self, chunks, delay=0.0):
        self.chunks, self.delay, self.closed = list(chunks), delay, False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield c

    async def close(self):
        self.closed = True


class FakeGroq:
    """Each create() returns the next scripted reply: a list of chunks, or an exception to raise."""

    def __init__(self, *script, delay=0.0):
        self.script, self.delay, self.calls = list(script), delay, []
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=str)))
        nxt = self.script.pop(0)
        if isinstance(nxt, BaseException):
            raise nxt
        return Stream(nxt, self.delay)


def brain_settings(settings, **over):
    settings.groq_api_key = "test-key-not-real"
    for k, v in over.items():
        setattr(settings, k, v)
    return settings


async def no_act(cmds):
    raise AssertionError(f"nothing should have been executed: {cmds}")


# ── configuration ─────────────────────────────────────────────────────────────

def test_off_without_a_key_and_the_key_never_shows(settings):
    settings.groq_api_key = ""
    b = Brain(settings)
    assert not b.enabled and "GROQ_API_KEY" in b.problem
    with pytest.raises(BrainError) as e:
        asyncio.run(b.answer("s", "hi", "", lambda t: None, no_act))
    assert e.value.kind == "disabled" and not e.value.retry

    settings.groq_api_key = "gsk_secret_value"
    on = Brain(settings, client=FakeGroq())
    assert on.enabled
    assert "gsk_secret_value" not in json.dumps(on.status()) + repr(settings)


def test_bad_settings_are_reported(settings):
    assert "GROQ_REASONING_EFFORT" in Brain(brain_settings(settings, groq_reasoning_effort="max"), client=FakeGroq()).problem


# ── streaming and memory ──────────────────────────────────────────────────────

async def test_streams_text_and_keeps_reasoning_out(settings):
    fake = FakeGroq([NS(choices=[]), chunk(reasoning="The user wants..."), chunk(content="Rayleigh "),
                     chunk(), chunk(content="scattering!")])
    b = Brain(brain_settings(settings), client=fake)
    seen = []
    ans = await b.answer("s1", "why is the sky blue", "Scene: stage.", seen.append, no_act)
    assert ans.text == "Rayleigh scattering!"
    assert seen[-1] == "Rayleigh scattering!" and all("user wants" not in s for s in seen)
    call = fake.calls[0]
    assert call["model"] == "openai/gpt-oss-120b" and call["stream"] is True and call["reasoning_effort"] == "medium"
    assert call["messages"][0]["role"] == "system" and "Microduck" in call["messages"][0]["content"]
    assert "Scene: stage." in call["messages"][1]["content"]
    assert [t["function"]["name"] for t in call["tools"]] == ["duck_actions"]  # no lookup without a retriever


async def test_multi_turn_context_and_clear(settings):
    fake = FakeGroq([chunk(content="Nice to meet you, Sam!")], [chunk(content="You're Sam.")], [chunk(content="?")])
    b = Brain(brain_settings(settings), client=fake)
    await b.answer("s1", "I'm Sam", "", lambda t: None, no_act)
    await b.answer("s1", "who am I?", "", lambda t: None, no_act)
    convo = [m["content"] for m in fake.calls[1]["messages"] if m["role"] != "system"]
    assert convo == ["I'm Sam", "Nice to meet you, Sam!", "who am I?"]
    b.sessions.clear("s1")
    await b.answer("s1", "who am I?", "", lambda t: None, no_act)
    assert [m["content"] for m in fake.calls[2]["messages"] if m["role"] != "system"] == ["who am I?"]


def test_history_is_bounded():
    s = Sessions(max_messages=4, max_chars=10_000)
    for i in range(10):
        s.add("a", f"q{i}", f"a{i}")
    assert [m["content"] for m in s.history("a")] == ["q8", "a8", "q9", "a9"]
    tight = Sessions(max_messages=100, max_chars=50)
    tight.add("a", "x" * 40, "y" * 40)  # one exchange over budget: dropped whole
    assert tight.history("a") == []


# ── actions: allowlisted, validated, executed by the executor ─────────────────

async def test_allowlisted_actions_become_one_sequence(settings):
    args = json.dumps({"actions": [{"action": "walk_forward", "seconds": 3}, {"action": "turn_left", "degrees": 45}]})
    fake = FakeGroq(tool_chunks("duck_actions", args), [chunk(content="Off I waddle!")])
    b = Brain(brain_settings(settings), client=fake)
    got = []

    async def act(cmds):
        got.append(cmds)
        return [{"id": f"c{i}", "status": "queued"} for i, _ in enumerate(cmds)]

    ans = await b.answer("s", "walk a bit then turn left", "", lambda t: None, act)
    assert len(got) == 1, "several actions must go to the executor as one sequence"
    walk, turn = got[0]
    assert isinstance(walk, C.Walk) and walk.vx > 0 and walk.duration_s == 3
    assert isinstance(turn, C.Turn) and turn.angle_deg == 45
    assert [a["action"] for a in ans.actions] == ["walk_forward", "turn_left"] and ans.text == "Off I waddle!"
    tool_msg = fake.calls[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "Started, not finished" in tool_msg["content"]


@pytest.mark.parametrize("args", [
    json.dumps({"actions": [{"action": "run_shell", "cmd": "rm -rf /"}]}),
    json.dumps({"actions": [{"action": "walk_forward", "seconds": 600}]}),
    json.dumps({"actions": []}),
    "{not json",
])
async def test_invalid_actions_are_refused_not_run(settings, args):
    fake = FakeGroq(tool_chunks("duck_actions", args), [chunk(content="I can't do that one.")])
    b = Brain(brain_settings(settings), client=fake)
    ans = await b.answer("s", "do something odd", "", lambda t: None, no_act)
    assert ans.actions == [] and ans.text == "I can't do that one."
    assert "nothing was done" in fake.calls[1]["messages"][-1]["content"]


async def test_unknown_tool_and_no_invented_lookup(settings):
    fake = FakeGroq(tool_chunks("look_up", json.dumps({"query": "news"})), [chunk(content="I can't look that up.")])
    b = Brain(brain_settings(settings), client=fake)
    ans = await b.answer("s", "news today?", "", lambda t: None, no_act)
    assert not ans.looked_up and ans.sources == []
    assert "no tool called" in fake.calls[1]["messages"][-1]["content"]


async def test_sources_only_from_a_lookup_that_ran(settings):
    class Docs:
        name = "approved_docs"

        async def search(self, query):
            return Retrieved("Microduck has 14 servos.", [{"title": "Robot model", "url": "https://example.org/model"}])

    fake = FakeGroq(tool_chunks("look_up", json.dumps({"query": "servo count"})), [chunk(content="Fourteen, I checked.")])
    b = Brain(brain_settings(settings), client=fake, retriever=Docs())
    ans = await b.answer("s", "how many servos?", "", lambda t: None, no_act)
    assert ans.looked_up and ans.sources == [{"title": "Robot model", "url": "https://example.org/model"}]
    assert {t["function"]["name"] for t in fake.calls[0]["tools"]} == {"duck_actions", "look_up"}


def test_sources_in_keeps_only_web_urls():
    got = sources_in({"results": [{"title": "A", "url": "https://a.example"}, {"url": "javascript:alert(1)"}]},
                     "see http://b.example/x.")
    assert got == [{"title": "A", "url": "https://a.example"}, {"title": "http://b.example/x", "url": "http://b.example/x"}]


# ── failures become sentences ─────────────────────────────────────────────────

def named(name):
    return type(name, (Exception,), {})("raw SDK text with request details")


@pytest.mark.parametrize("name,kind,retry", [
    ("AuthenticationError", "auth", False), ("RateLimitError", "rate_limit", True),
    ("APITimeoutError", "timeout", True), ("APIConnectionError", "connection", True),
    ("BadRequestError", "bad_request", False), ("InternalServerError", "upstream", True),
    ("KeyError", "internal", True),
])
async def test_errors_are_classified_and_safe(settings, name, kind, retry):
    b = Brain(brain_settings(settings), client=FakeGroq(named(name)))
    with pytest.raises(BrainError) as e:
        await b.answer("s", "hi", "", lambda t: None, no_act)
    assert (e.value.kind, e.value.retry) == (kind, retry)
    assert "request details" not in e.value.message
    assert b.sessions.history("s") == []  # a failed turn is not remembered


async def test_a_hung_stream_times_out(settings):
    b = Brain(brain_settings(settings, groq_timeout_s=0.01), client=FakeGroq([chunk(content="x")] * 1000, delay=0.01))
    import microduck_x.brain as brain_mod
    old, brain_mod.MAX_ROUNDS = brain_mod.MAX_ROUNDS, 1
    try:
        t0 = time.monotonic()
        with pytest.raises(BrainError) as e:
            # the overall budget is timeout × rounds + 5 s; the stream would take 10 s
            await b.answer("s", "hi", "", lambda t: None, no_act)
        assert e.value.kind == "timeout" and time.monotonic() - t0 < 8
    finally:
        brain_mod.MAX_ROUNDS = old


def test_classify_passes_brain_errors_through():
    err = BrainError("x", "y")
    assert classify(err) is err


# ── routing: only what nothing else placed, never a stop ──────────────────────

def test_persona_routes_open_questions_only(tmp_path):
    p = Persona(Memory.load(tmp_path / "mem.json"), lambda: {}, lambda: True)
    assert p.handle("why is the sky blue").ask_brain
    assert p.handle("explain recursion with an example in python").ask_brain
    for line in ("stop", "walk forward", "dance", "hello", "why is the sky blue and stop"):
        assert not p.handle(line).ask_brain, line
    off = Persona(Memory.load(tmp_path / "mem2.json"), lambda: {})
    assert not off.handle("why is the sky blue").ask_brain


# ── the chat endpoints ────────────────────────────────────────────────────────

def replies(client, rid):
    for _ in range(100):
        got = [e for e in client.get("/api/logs", params={"kind": "chat.reply"}).json() if e["data"].get("id") == rid]
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError("no reply")


def test_chat_endpoints(settings):
    app = create_app(brain_settings(settings, groq_rate_per_min=3))
    svc = app.state.svc
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"text": "   "}).status_code == 422
        assert client.post("/api/chat", json={"text": "x" * 2001}).status_code == 413
        assert client.post("/api/chat", json={"text": "hi", "session": "../etc"}).status_code == 422

        svc.brain = Brain(settings, client=FakeGroq([chunk(content="**Rayleigh** scattering.")]))
        r = client.post("/api/chat", json={"text": "why is the sky blue", "session": "tab1"}).json()
        assert r["pending"]["id"]
        (reply,) = replies(client, r["pending"]["id"])
        assert reply["data"]["text"] == "**Rayleigh** scattering." and reply["data"]["session"] == "tab1"
        assert reply["data"]["brain"]["model"] == "openai/gpt-oss-120b" and reply["data"]["sources"] == []
        assert svc.brain.sessions.history("tab1")

        assert client.post("/api/chat/clear", json={"session": "tab1"}).json() == {"cleared": True}
        assert svc.brain.sessions.history("tab1") == []

        # one open question at a time per session; a stop always goes through
        svc.brain = Brain(settings, client=FakeGroq([chunk(content="word ")] * 200, delay=0.05))
        first = client.post("/api/chat", json={"text": "tell me about the moon", "session": "tab1"}).json()
        assert client.post("/api/chat", json={"text": "and about mars please", "session": "tab1"}).status_code == 409
        assert client.post("/api/chat", json={"text": "stop", "session": "tab1"}).status_code == 200
        assert client.post("/api/chat/cancel", json={"session": "tab1"}).json() == {"cancelled": True}
        (stopped,) = replies(client, first["pending"]["id"])
        assert stopped["data"]["cancelled"] is True

        # the per-minute limit, used up
        svc.brain = Brain(settings, client=FakeGroq(named("RateLimitError")))
        svc.brain.limiter.allow(); svc.brain.limiter.allow(); svc.brain.limiter.allow()
        assert client.post("/api/chat", json={"text": "what is a black hole", "session": "tab2"}).status_code == 429

        # an upstream error is a reply the page can retry, not a crash
        svc.brain = Brain(settings, client=FakeGroq(named("RateLimitError")))
        r = client.post("/api/chat", json={"text": "what is a black hole", "session": "tab2"}).json()
        (err,) = replies(client, r["pending"]["id"])
        assert err["data"]["error"] == "rate_limit" and err["data"]["retry"] == "what is a black hole"
        assert client.get("/api/status").json()["brain"]["enabled"] is True


# ── the reply's shape for the page ────────────────────────────────────────────

def test_followups_are_split_off_and_bounded():
    from microduck_x.brain import split_followups
    text, qs = split_followups("Done.\n>> follow-ups: why? | how does it work | a | " + "x" * 300)
    assert text == "Done." and qs == ["why?", "how does it work"]
    assert split_followups("No suggestions.") == ("No suggestions.", [])


def test_structure_reads_kind_code_and_maths():
    from microduck_x.brain import structure
    code = structure('Here:\n\n```python title="app.py"\nprint(1)\nprint(2)\n```')
    assert code["kind"] == "code" and code["code"] == [{"language": "python", "filename": "app.py", "lines": 2}]
    maths = structure("**Step 1:** factor $x^2-5x+6$.\n\n$$(x-2)(x-3)=0$$")
    assert maths["kind"] == "math" and maths["math"] == 2
    assert "x^2" not in maths["summary"] and "Step 1" in maths["summary"]  # a summary in words, no TeX
    assert structure("It costs $5 and $10.")["kind"] == "conversation"   # dollars are not maths
    assert structure("word " * 300)["kind"] == "long"
    assert structure("- a\n- b\n- c")["kind"] == "list"
    assert structure("Oops", error="timeout")["kind"] == "error"
    assert structure("```js\nconst a = 1;")["code"][0]["lines"] == 1      # an unclosed fence still counts


async def test_answer_carries_followups_and_strips_them(settings):
    fake = FakeGroq([chunk(content="Plants make food from light.\n>> follow-ups: what is chlorophyll? | do fungi?")])
    ans = await Brain(brain_settings(settings), client=fake).answer("s", "photosynthesis?", "", lambda t: None, no_act)
    assert ans.text == "Plants make food from light." and ans.followups == ["what is chlorophyll?", "do fungi?"]


def test_regenerate_and_feedback_endpoints(settings):
    app = create_app(brain_settings(settings))
    svc = app.state.svc
    with TestClient(app) as client:
        svc.brain = Brain(settings, client=FakeGroq([chunk(content="First $x=1$.")], [chunk(content="Second.")]))
        r = client.post("/api/chat", json={"text": "why is the sky blue", "session": "t"}).json()
        (first,) = replies(client, r["pending"]["id"])
        d = first["data"]
        assert d["question"] == "why is the sky blue" and d["structured"]["kind"] == "conversation"
        assert "x=1" not in d["babble"] or d["babble"] == ""  # babble is from the words, not the TeX
        r = client.post("/api/chat", json={"text": "why is the sky blue", "session": "t", "regenerate": True}).json()
        (second,) = replies(client, r["pending"]["id"])
        assert second["data"]["text"] == "Second."
        history = [m["content"] for m in svc.brain.sessions.history("t")]
        assert history == ["why is the sky blue", "Second."]  # the first answer was replaced, not stacked

        assert client.post("/api/chat/feedback", json={"id": first["data"]["id"], "rating": "up", "session": "t"}).json() == {"recorded": True}
        assert client.post("/api/chat/feedback", json={"id": "x", "rating": "meh"}).status_code == 422
        fb = client.get("/api/logs", params={"kind": "chat.feedback"}).json()
        assert fb[-1]["data"]["rating"] == "up"
