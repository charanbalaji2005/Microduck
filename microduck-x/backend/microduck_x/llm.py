"""Optional intent interpretation through a local Ollama model.

Consulted only when the deterministic parser could not place some of the text, and only when
`MDX_OLLAMA_MODEL` is set. The model proposes JSON; every proposal is validated by the same
pydantic schema the API uses, so it can only ever produce a command a button could have produced.
It never sees a shell, a file, or anything but the user's sentence and the schema below.
Emergency stop does not route through here.
"""
from __future__ import annotations

import asyncio
import json
import urllib.request

from pydantic import ValidationError

from .commands import Envelope, action_names

SYSTEM = """You translate a spoken request for a small simulated biped robot into JSON commands.
Answer with JSON only: {"commands": [ ... ]}. Use only these actions and fields:
walk {vx -0.4..0.4 m/s, vy -0.3..0.3, vyaw -1..1 rad/s, duration_s 0..30}
turn {vyaw -1..1, angle_deg -720..720 (positive = left)} ; stop ; estop ; enable ; disable
skill {skill: kick_left|kick_right|roulade|ground_pick|sit_toggle} ; pause ; resume
reset {keyframe: SIT|STAND} ; set_environment {environment: stage|hangar|bare|apartment|lab|garden|arena, layout: open|slalom|corridor|scatter}
push {direction: front|back|left|right, newtons 0..10} ; camera {mode: orbit|follow|first_person|top|reset}
show_panel {panel: joints|velocities|telemetry|contacts|collision|logs|experiments}
goto {target: marker name} ; waypoints ; run_experiment {experiment, trials} ; status {topic: general|fall|joints|policy}
If the request is impossible for this robot, answer {"commands": [], "reason": "..."}.
Every command object has an "action" field."""


async def interpret(url: str, model: str, text: str, timeout: float = 8.0) -> tuple[list, str | None]:
    body = json.dumps({"model": model, "system": SYSTEM, "prompt": text, "format": "json", "stream": False,
                       "options": {"temperature": 0}}).encode()

    def call():
        req = urllib.request.Request(url.rstrip("/") + "/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — localhost by configuration
            return json.loads(resp.read())

    raw = await asyncio.to_thread(call)
    try:
        proposal = json.loads(raw.get("response", "{}"))
    except json.JSONDecodeError:
        return [], "the language model did not return JSON"
    valid, problems = [], []
    allowed = set(action_names()) - {"estop", "clear_estop"}
    for item in proposal.get("commands", [])[:4]:
        if not isinstance(item, dict) or item.get("action") not in allowed:
            problems.append(f"dropped non-allowlisted proposal {item!r:.80}")
            continue
        try:
            valid.append(Envelope.model_validate({"command": item, "source": "llm"}).command)
        except ValidationError as error:
            problems.append(f"dropped invalid {item.get('action')}: {error.errors()[0]['msg']}")
    reason = proposal.get("reason")
    return valid, "; ".join(filter(None, [reason, *problems])) or None
