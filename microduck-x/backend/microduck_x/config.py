"""Configuration, from environment variables only. Every default is localhost and local disk."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # microduck-x/
REPO = ROOT.parent  # microduck-main/


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """`microduck-x/.env`, if there is one: KEY=VALUE lines. The real environment wins — a variable
    already set is never overridden — and nothing read here is logged."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _flag(name: str, default: bool = False) -> bool:
    return os.environ.get(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    state_dir: Path = field(default_factory=lambda: Path(_env("MDX_STATE", "~/.cache/duck-sim")).expanduser())
    robot_socket: Path | None = None
    duck: str = field(default_factory=lambda: _env("MDX_DUCK", "duck-a"))
    xbody_host: str = "127.0.0.1"
    xbody_state_port: int = field(default_factory=lambda: int(_env("MDX_STATE_PORT", "7811")))
    body_port: int = field(default_factory=lambda: int(_env("DUCK_SIM_PORT", "7801")))

    host: str = field(default_factory=lambda: _env("MDX_HOST", "127.0.0.1"))
    # A host that assigns the port (Render, most PaaS) sets PORT; MDX_PORT still wins when both are set.
    port: int = field(default_factory=lambda: int(_env("MDX_PORT", _env("PORT", "8765"))))
    # Remote control must be asked for twice: a non-local bind *and* a token.
    token: str = field(default_factory=lambda: _env("MDX_TOKEN", ""))

    # Speech. `auto` = faster-whisper if its model is already on disk, else pocketsphinx.
    stt_engine: str = field(default_factory=lambda: _env("MDX_STT", "auto"))
    whisper_model: str = field(default_factory=lambda: _env("MDX_WHISPER_MODEL", "base.en"))
    whisper_device: str = field(default_factory=lambda: _env("MDX_WHISPER_DEVICE", "auto"))
    models_dir: Path | None = None
    stt_min_confidence: float = field(default_factory=lambda: float(_env("MDX_STT_MIN_CONFIDENCE", "0.45")))
    require_wake_word: bool = field(default_factory=lambda: _flag("MDX_REQUIRE_WAKE_WORD"))
    confirm_uncalibrated: bool = field(default_factory=lambda: _flag("MDX_CONFIRM_UNCALIBRATED", True))

    # Optional local LLM (Ollama). Off unless a model is named. Its output is validated against
    # the same allowlist as everything else; it can never run anything the parser could not.
    ollama_url: str = field(default_factory=lambda: _env("MDX_OLLAMA_URL", "http://127.0.0.1:11434"))
    ollama_model: str = field(default_factory=lambda: _env("MDX_OLLAMA_MODEL", ""))

    # Open conversation through Groq (brain.py). Off unless GROQ_API_KEY is set. Only chat lines the
    # persona and the parser could not place are sent; commands, stops and the e-stop never are.
    # The key is read here, on the server, and never logged, returned or sent to the page.
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY", "").strip(), repr=False)
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "openai/gpt-oss-120b").strip())
    groq_reasoning_effort: str = field(default_factory=lambda: _env("GROQ_REASONING_EFFORT", "medium").strip())
    groq_temperature: float = field(default_factory=lambda: float(_env("GROQ_TEMPERATURE", "1")))
    groq_max_tokens: int = field(default_factory=lambda: int(_env("GROQ_MAX_COMPLETION_TOKENS", "2048")))
    groq_timeout_s: float = field(default_factory=lambda: float(_env("GROQ_TIMEOUT_S", "30")))
    groq_history_messages: int = field(default_factory=lambda: int(_env("GROQ_HISTORY_MESSAGES", "16")))
    groq_history_chars: int = field(default_factory=lambda: int(_env("GROQ_HISTORY_CHARS", "16000")))
    groq_rate_per_min: int = field(default_factory=lambda: int(_env("GROQ_RATE_PER_MIN", "20")))
    # Groq's built-in browser search for current facts (an external search, run by Groq). Off by default.
    groq_web_search: bool = field(default_factory=lambda: _flag("GROQ_WEB_SEARCH"))
    chat_max_chars: int = field(default_factory=lambda: int(_env("MDX_CHAT_MAX_CHARS", "2000")))

    # Simulator process management (environment switching restarts the MuJoCo body).
    manage_sim: bool = field(default_factory=lambda: _flag("MDX_MANAGE_SIM"))
    rl_dir: Path = field(default_factory=lambda: Path(_env("DUCK_SIM_RL", "~/Pollen/microduck_rl")).expanduser())
    initial_env: str = field(default_factory=lambda: _env("MDX_ENV", "stage"))
    initial_layout: str = field(default_factory=lambda: _env("MDX_LAYOUT", ""))
    keyframe: str = field(default_factory=lambda: _env("DUCK_SIM_KEYFRAME", "SIT"))
    native_viewer: bool = field(default_factory=lambda: _flag("MDX_NATIVE_VIEWER"))

    # Movement limits. The velocity bounds are robotd's own pad defaults for the v8 velstand
    # gait (`robotd-params::PadDriveParams`): what the policy follows well, not a hard limit of
    # robotd — which is exactly why this side enforces them.
    vx_max: float = 0.4
    vy_max: float = 0.3
    vyaw_max: float = 1.0
    # The smallest twist the velstand gait steps for. Below it the policy *stands* — robotd still
    # reports the twist as requested and applied, but the feet never leave the ground. Measured in
    # this simulator (stage scene, velstand v8, from standing): vx 0.2 and 0.25 back, vx -0.3 and
    # vyaw 0.6 all stand still; vx 0.28, vx -0.38 and vyaw 0.85 all step, every time. A nonzero
    # twist below these is scaled up to them (executor.gait_floor). 0 turns a floor off.
    gait_min_vx_fwd: float = field(default_factory=lambda: float(_env("MDX_GAIT_MIN_VX_FWD", "0.28")))
    gait_min_vx_back: float = field(default_factory=lambda: float(_env("MDX_GAIT_MIN_VX_BACK", "0.38")))
    gait_min_vyaw: float = field(default_factory=lambda: float(_env("MDX_GAIT_MIN_VYAW", "0.85")))
    max_command_s: float = field(default_factory=lambda: float(_env("MDX_MAX_COMMAND_S", "30")))
    intent_hz: float = 20.0
    stale_robot_s: float = 0.6
    stale_sim_s: float = 1.0
    fall_tilt_deg: float = 60.0

    mock: bool = field(default_factory=lambda: _flag("MDX_MOCK"))
    frontend_dist: Path = ROOT / "frontend" / "dist"
    experiments_dir: Path = ROOT / "configs" / "experiments"

    def __post_init__(self) -> None:
        if self.robot_socket is None:
            self.robot_socket = Path(_env("MDX_ROBOT_SOCKET", str(self.state_dir / f"{self.duck}.sock")))
        if self.models_dir is None:
            self.models_dir = Path(_env("MDX_MODELS", str(self.state_dir / "models"))).expanduser()
        self.runs_dir = self.state_dir / "x-runs"

    @property
    def local_only(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    def validate(self) -> list[str]:
        problems = []
        if not self.local_only and not self.token:
            problems.append(
                f"MDX_HOST={self.host} exposes robot control beyond this machine; set MDX_TOKEN too, "
                "or bind 127.0.0.1"
            )
        return problems
