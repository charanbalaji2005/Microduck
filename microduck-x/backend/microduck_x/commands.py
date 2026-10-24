"""The command schema — the allowlist. Every route into the robot (voice, LLM, buttons, keyboard,
experiments, the API) produces one of these, and nothing else is executable.

Bounds are enforced here, at construction: a command that exists is a command inside its limits.
Which commands touch which real interface is stated per class:

* ``robotd``   — sent to robotd's JSON-RPC (the original control software).
* ``sim``      — sent to the MuJoCo body's state port (physics admin: pause, reset, shove, scene).
* ``x``        — a Microduck X extension built on top of robotd (closed-loop turn, navigation,
                 experiments). Documented as an extension, not as a robot capability.
* ``ui``       — delivered to the dashboard (camera, panels); touches neither robot nor physics.
"""
from __future__ import annotations

import time
import uuid
from typing import Annotated, ClassVar, Literal, Union, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

VX_MAX, VY_MAX, VYAW_MAX = 0.4, 0.3, 1.0
MAX_DURATION_S = 30.0


class _Cmd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    INTERFACE: ClassVar[str] = "robotd"


class Walk(_Cmd):
    """robot.move at 20 Hz for `duration_s`, then robot.stop. Velocities in the trunk frame."""

    action: Literal["walk"] = "walk"
    vx: float = Field(0.0, ge=-VX_MAX, le=VX_MAX)
    vy: float = Field(0.0, ge=-VY_MAX, le=VY_MAX)
    vyaw: float = Field(0.0, ge=-VYAW_MAX, le=VYAW_MAX)
    duration_s: float = Field(3.0, gt=0, le=MAX_DURATION_S)

    @model_validator(mode="after")
    def _moves(self):
        if self.vx == self.vy == self.vyaw == 0:
            raise ValueError("a walk with zero velocity is a stop")
        return self


class Turn(_Cmd):
    """Timed turn (robotd), or turn-by-angle closed on simulator yaw (x extension)."""

    action: Literal["turn"] = "turn"
    vyaw: float = Field(0.6, ge=-VYAW_MAX, le=VYAW_MAX)
    angle_deg: float | None = Field(None, ge=-720, le=720)
    duration_s: float | None = Field(None, gt=0, le=MAX_DURATION_S)

    @model_validator(mode="after")
    def _consistent(self):
        if self.vyaw == 0:
            raise ValueError("turn needs a non-zero vyaw")
        if self.angle_deg is not None and self.angle_deg != 0 and (self.angle_deg > 0) != (self.vyaw > 0):
            self.vyaw = -self.vyaw  # the angle's sign decides the direction
        if self.angle_deg is None and self.duration_s is None:
            self.duration_s = 2.0
        return self


class Hold(_Cmd):
    """Stand still for `duration_s` with a zero twist, watching for a fall (x extension)."""

    action: Literal["hold"] = "hold"
    duration_s: float = Field(5.0, gt=0, le=120)
    INTERFACE: ClassVar[str] = "x"


class Emote(_Cmd):
    """Body language through robotd's own intents: `robot.head` offsets (neck/head pitch, yaw,
    roll) and `robot.pose` body offsets, resent at 20 Hz for the gesture, then released. The
    offsets stay inside the ranges robotd documents as trained (pose: z −0.025…+0.010 m,
    roll/pitch ±0.26 rad); the head ones are kept small for the same reason."""

    action: Literal["emote"] = "emote"
    gesture: Literal["nod", "shake", "look_left", "look_right", "look_up", "look_down", "wiggle", "crouch",
                     "bow", "celebrate", "curious", "sleepy"]
    duration_s: float = Field(2.0, gt=0, le=6.0)


class Neutral(_Cmd):
    """Back to the upright neutral posture: the head command to robotd's trained nominal (all four
    head offsets zero) and the body pose released. Whatever activity holds the head is stopped
    first. robotd slews the head there, so it settles rather than snaps."""

    action: Literal["neutral"] = "neutral"


class Dance(_Cmd):
    """A short routine of real moves: wiggle, turn left, turn right, nod (x extension)."""

    action: Literal["dance"] = "dance"
    style: Literal["shuffle", "spin", "bob"] = "shuffle"
    INTERFACE: ClassVar[str] = "x"


class Stop(_Cmd):
    action: Literal["stop"] = "stop"


class EStop(_Cmd):
    """Latching stop. Cancels everything, zeroes the twist, refuses motion until cleared."""

    action: Literal["estop"] = "estop"


class ClearEStop(_Cmd):
    action: Literal["clear_estop"] = "clear_estop"


class Enable(_Cmd):
    """robot.enable {on: true}: torque on, home pose, the policy takes the robot (it stands up)."""

    action: Literal["enable"] = "enable"


class Disable(_Cmd):
    """robot.enable {on: false}: policy off, robot returns to its home pose."""

    action: Literal["disable"] = "disable"


class Skill(_Cmd):
    """robot.do {skill}. Validated against the skills this robotd reported, at execution time."""

    action: Literal["skill"] = "skill"
    skill: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")


class Pause(_Cmd):
    action: Literal["pause"] = "pause"
    INTERFACE: ClassVar[str] = "sim"


class Resume(_Cmd):
    action: Literal["resume"] = "resume"
    INTERFACE: ClassVar[str] = "sim"


class Reset(_Cmd):
    """Disable the policy, put every duck back at its spawn, restore the scenery, re-enable."""

    action: Literal["reset"] = "reset"
    keyframe: Literal["SIT", "STAND", "HOME", "FOLD"] = "SIT"
    enable_after: bool = True
    INTERFACE: ClassVar[str] = "sim"


class SetEnvironment(_Cmd):
    action: Literal["set_environment"] = "set_environment"
    environment: Literal["stage", "hangar", "bare", "apartment", "lab", "garden", "arena"]
    layout: Literal["open", "slalom", "corridor", "scatter"] | None = None
    INTERFACE: ClassVar[str] = "sim"


class Push(_Cmd):
    """A shove on the trunk, world frame, for robustness tests."""

    action: Literal["push"] = "push"
    direction: Literal["front", "back", "left", "right"] = "front"
    newtons: float = Field(3.0, gt=0, le=10.0)
    seconds: float = Field(0.2, gt=0, le=1.0)
    INTERFACE: ClassVar[str] = "sim"


class Camera(_Cmd):
    action: Literal["camera"] = "camera"
    mode: Literal["orbit", "follow", "first_person", "top", "reset"]
    INTERFACE: ClassVar[str] = "ui"


class ShowPanel(_Cmd):
    action: Literal["show_panel"] = "show_panel"
    panel: Literal["joints", "velocities", "telemetry", "contacts", "collision", "logs", "experiments",
                   "voice", "policy", "navigation"]
    visible: bool = True
    INTERFACE: ClassVar[str] = "ui"


class Goto(_Cmd):
    """Plan around the scene's collision geometry and walk the path (x extension)."""

    action: Literal["goto"] = "goto"
    target: str | None = Field(None, max_length=40)
    x: float | None = Field(None, ge=-20, le=20)
    y: float | None = Field(None, ge=-20, le=20)
    speed: float = Field(0.2, gt=0, le=VX_MAX)
    INTERFACE: ClassVar[str] = "x"

    @model_validator(mode="after")
    def _has_goal(self):
        if self.target is None and (self.x is None or self.y is None):
            raise ValueError("goto needs a target marker or x and y")
        return self


class Waypoints(_Cmd):
    action: Literal["waypoints"] = "waypoints"
    targets: list[str] = Field(default_factory=list, max_length=12)  # empty = every goal marker
    speed: float = Field(0.2, gt=0, le=VX_MAX)
    INTERFACE: ClassVar[str] = "x"


class RunExperiment(_Cmd):
    action: Literal["run_experiment"] = "run_experiment"
    experiment: str | None = Field(None, max_length=60, pattern=r"^[a-z0-9_]*$")  # None = the last one
    trials: int = Field(1, ge=1, le=20)
    INTERFACE: ClassVar[str] = "x"


class SaveExperiment(_Cmd):
    action: Literal["save_experiment"] = "save_experiment"
    INTERFACE: ClassVar[str] = "x"


class CompareRuns(_Cmd):
    action: Literal["compare_runs"] = "compare_runs"
    INTERFACE: ClassVar[str] = "x"


class Status(_Cmd):
    action: Literal["status"] = "status"
    topic: Literal["general", "fall", "joints", "policy"] = "general"
    INTERFACE: ClassVar[str] = "x"


class Help(_Cmd):
    action: Literal["help"] = "help"
    INTERFACE: ClassVar[str] = "x"


Command = Annotated[
    Union[Walk, Turn, Hold, Emote, Neutral, Dance, Stop, EStop, ClearEStop, Enable, Disable, Skill, Pause, Resume, Reset,
          SetEnvironment, Push, Camera, ShowPanel, Goto, Waypoints, RunExperiment, SaveExperiment,
          CompareRuns, Status, Help],
    Field(discriminator="action"),
]

MOTION_ACTIONS = {"walk", "turn", "goto", "waypoints", "skill", "emote", "neutral", "dance"}


class Envelope(BaseModel):
    """A command plus where it came from. `id` correlates request, events and result."""

    model_config = ConfigDict(extra="forbid")
    command: Command
    source: Literal["voice", "text", "chat", "llm", "ui", "keyboard", "api", "task", "experiment"] = "api"
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = Field(default_factory=time.time)
    confidence: float | None = Field(None, ge=0, le=1)
    transcript: str | None = Field(None, max_length=500)
    deadline_s: float | None = Field(None, gt=0, le=600)

    @field_validator("transcript")
    @classmethod
    def _strip(cls, v):
        return v.strip() if v else v


def command_classes() -> tuple[type[_Cmd], ...]:
    return get_args(get_args(Command)[0])


def action_names() -> list[str]:
    return sorted(c.model_fields["action"].default for c in command_classes())


def interface_of(command: _Cmd) -> str:
    return type(command).INTERFACE
