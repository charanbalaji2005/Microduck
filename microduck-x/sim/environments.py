"""Scenic worlds for the simulated Microduck — physics first, looks second.

Every environment here is a MuJoCo model. Nothing is drawn that is not in that model: the web
viewer builds its scene from the *compiled* `MjModel` this file produces (geoms, meshes,
materials) and moves it with the body poses MuJoCo computes. So the room you see and the room the
duck can bump into are the same object by construction, not by two files kept in step.

Three rules hold for every item below:

* **Anything with height collides.** Furniture, walls, rocks, rails and obstacles are ordinary
  collision geoms (`contype=1 conaffinity=1`, which is what the robot's collision geoms use).
* **Decals are flat.** Paths, floor markings, start/goal pads and tick marks are visual-only
  (`contype=0 conaffinity=0`) and at most 2 mm thick, so their absence from the contact set is
  physically negligible for a 25 cm robot. They carry `decal_` in their name.
* **The robot is untouched.** Scenic worlds are built on top of the original
  `scene_allcollisions.xml` from `microduck_rl` (the same robot model the original `apartment`
  scene uses), by adding geoms with `MjSpec`. The robot's joints, actuators, collision geometry
  and keyframes come from that file unchanged.

`bare` and `apartment` are the original `microduck_rl` scenes, loaded verbatim.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import mujoco

# Robot collision geoms are contype=1/conaffinity=1 in robot_allcollisions.xml.
SOLID = dict(contype=1, conaffinity=1, group=0)
DECAL = dict(contype=0, conaffinity=0, group=1)

BOX = mujoco.mjtGeom.mjGEOM_BOX
CYL = mujoco.mjtGeom.mjGEOM_CYLINDER
SPHERE = mujoco.mjtGeom.mjGEOM_SPHERE
ELLIPSOID = mujoco.mjtGeom.mjGEOM_ELLIPSOID
CAPSULE = mujoco.mjtGeom.mjGEOM_CAPSULE


@dataclass
class Marker:
    """A named point the dashboard and the navigator can refer to ("go to goal 2")."""

    name: str
    x: float
    y: float
    kind: str = "goal"  # start | goal | waypoint | station


@dataclass
class EnvInfo:
    name: str
    title: str
    description: str
    base_scene: str  # file name inside microduck_rl's robot/microduck/
    builder: Callable[[mujoco.MjSpec, dict], None] | None = None
    markers: list[Marker] = field(default_factory=list)
    bounds: tuple[float, float, float, float] = (-3.0, 3.0, -3.0, 3.0)  # xmin xmax ymin ymax
    look: dict = field(default_factory=dict)  # viewer-only lighting/sky hints
    layouts: list[str] = field(default_factory=lambda: ["default"])
    original: bool = False


def _quat_z(yaw: float) -> list[float]:
    return [math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)]


def _quat_y(pitch: float) -> list[float]:
    return [math.cos(pitch / 2), 0.0, math.sin(pitch / 2), 0.0]


def _tex(spec, name, rgb1, rgb2, mark=None, markrgb=(1, 1, 1), size=512, builtin=None, random_=0.0):
    kw = dict(
        name=name,
        type=mujoco.mjtTexture.mjTEXTURE_2D,
        builtin=builtin if builtin is not None else mujoco.mjtBuiltin.mjBUILTIN_CHECKER,
        rgb1=list(rgb1),
        rgb2=list(rgb2),
        width=size,
        height=size,
        markrgb=list(markrgb),
    )
    if mark is not None:
        kw["mark"] = mark
    if random_:
        kw["random"] = random_
    spec.add_texture(**kw)


def _mat(spec, name, texture=None, rgba=(1, 1, 1, 1), repeat=(1, 1), uniform=True, reflectance=0.0):
    mat = spec.add_material(
        name=name, rgba=list(rgba), texrepeat=list(repeat), texuniform=uniform, reflectance=reflectance
    )
    if texture:
        mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = texture
    return mat


def _geom(parent, name, gtype, size, pos, rgba=None, quat=None, material=None, solid=True, **extra):
    kw = dict(SOLID if solid else DECAL)
    kw.update(name=name, type=gtype, size=list(size) + [0.0] * (3 - len(size)), pos=list(pos))
    if rgba is not None:
        kw["rgba"] = list(rgba)
    if quat is not None:
        kw["quat"] = list(quat)
    if material is not None:
        kw["material"] = material
    kw.update(extra)
    return parent.add_geom(**kw)


def _decal(parent, name, gtype, size, pos, rgba, quat=None, material=None):
    """Flat, visual-only floor marking. Thickness is capped at 2 mm (see module doc)."""
    size = list(size)
    if gtype == BOX:
        size[2] = min(size[2], 0.001)
    elif gtype == CYL:
        size[1] = min(size[1], 0.001)
    return _geom(parent, f"decal_{name}", gtype, size, pos, rgba, quat, material, solid=False)


def _floor(spec, material):
    """Restyle the base scene's own floor plane — physics (friction, solref) untouched."""
    spec.geom("floor").material = material


def _wall(spec, name, x0, y0, x1, y1, height, thick, rgba, material=None):
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    length = math.hypot(x1 - x0, y1 - y0)
    yaw = math.atan2(y1 - y0, x1 - x0)
    _geom(spec.worldbody, name, BOX, [length / 2, thick / 2, height / 2], [cx, cy, height / 2],
          rgba, quat=_quat_z(yaw), material=material)


def _table(spec, name, x, y, sx, sy, height, top_rgba, leg_rgba, yaw=0.0, top_thick=0.03):
    """A table as a body: a top and four legs. The duck fits underneath a 0.75 m one."""
    body = spec.worldbody.add_body(name=name, pos=[x, y, 0], quat=_quat_z(yaw))
    _geom(body, f"{name}_top", BOX, [sx / 2, sy / 2, top_thick / 2], [0, 0, height - top_thick / 2], top_rgba)
    leg = 0.025
    for i, (dx, dy) in enumerate([(1, 1), (1, -1), (-1, 1), (-1, -1)]):
        _geom(body, f"{name}_leg{i}", BOX, [leg, leg, (height - top_thick) / 2],
              [dx * (sx / 2 - leg * 1.5), dy * (sy / 2 - leg * 1.5), (height - top_thick) / 2], leg_rgba)
    return body


def _movable_block(spec, name, x, y, half, rgba, mass):
    """A free body the duck can push. Light on purpose: foam blocks, not bricks."""
    body = spec.worldbody.add_body(name=name, pos=[x, y, half + 0.0005])
    body.add_freejoint(name=f"{name}_free")
    _geom(body, f"{name}_geom", BOX, [half, half, half], [0, 0, 0], rgba, mass=mass)
    return body


# ── the laboratory ────────────────────────────────────────────────────────────


def build_lab(spec: mujoco.MjSpec, opts: dict) -> None:
    _tex(spec, "lab_floor_t", (0.36, 0.38, 0.40), (0.33, 0.35, 0.37),
         mark=mujoco.mjtMark.mjMARK_EDGE, markrgb=(0.55, 0.58, 0.6), size=512)
    _mat(spec, "lab_floor", "lab_floor_t", repeat=(2, 2), reflectance=0.08)  # 2 tiles/m → 0.25 m checker cells
    _floor(spec, "lab_floor")
    _tex(spec, "lab_panel_t", (0.80, 0.82, 0.84), (0.76, 0.78, 0.80),
         mark=mujoco.mjtMark.mjMARK_EDGE, markrgb=(0.62, 0.64, 0.66), size=256)
    _mat(spec, "lab_panel", "lab_panel_t", repeat=(1, 1))
    _tex(spec, "calib_t", (1, 1, 1), (0.05, 0.05, 0.05), size=256)
    _mat(spec, "calib", "calib_t", repeat=(4, 4), uniform=False)

    # Room shell, 8 x 6 m, 1.4 m panels.
    h, t = 1.4, 0.06
    _wall(spec, "lab_wall_n", -4, 3, 4, 3, h, t, (1, 1, 1, 1), "lab_panel")
    _wall(spec, "lab_wall_s", -4, -3, 4, -3, h, t, (1, 1, 1, 1), "lab_panel")
    _wall(spec, "lab_wall_e", 4, -3, 4, 3, h, t, (1, 1, 1, 1), "lab_panel")
    _wall(spec, "lab_wall_w", -4, -3, -4, 3, h, t, (1, 1, 1, 1), "lab_panel")

    # Workbenches along the north and east walls, with kit on top.
    grey, steel = (0.28, 0.30, 0.33, 1), (0.62, 0.64, 0.66, 1)
    _table(spec, "bench_n1", -2.2, 2.55, 1.6, 0.7, 0.78, grey, steel)
    _table(spec, "bench_n2", 0.4, 2.55, 1.6, 0.7, 0.78, grey, steel)
    _table(spec, "bench_e1", 3.55, 0.6, 0.7, 1.6, 0.78, grey, steel)
    _geom(spec.worldbody, "scope", BOX, [0.18, 0.14, 0.12], [-2.5, 2.6, 0.9], (0.15, 0.16, 0.18, 1))
    _geom(spec.worldbody, "psu", BOX, [0.12, 0.15, 0.07], [-1.9, 2.6, 0.85], (0.85, 0.55, 0.1, 1))
    _geom(spec.worldbody, "monitor", BOX, [0.3, 0.02, 0.2], [0.4, 2.75, 1.05], (0.08, 0.08, 0.09, 1))
    _geom(spec.worldbody, "parts_bin", BOX, [0.2, 0.15, 0.06], [3.55, 1.0, 0.84], (0.1, 0.35, 0.7, 1))
    # A storage rack and a charging dock on the floor.
    rack = spec.worldbody.add_body(name="rack", pos=[-3.6, -1.6, 0])
    for i, z in enumerate([0.05, 0.45, 0.85, 1.25]):
        _geom(rack, f"rack_shelf{i}", BOX, [0.25, 0.6, 0.015], [0, 0, z], steel)
    for i, (dx, dy) in enumerate([(1, 1), (1, -1), (-1, 1), (-1, -1)]):
        _geom(rack, f"rack_post{i}", BOX, [0.015, 0.015, 0.65], [dx * 0.23, dy * 0.58, 0.65], grey)
    _geom(spec.worldbody, "dock", BOX, [0.12, 0.16, 0.03], [-3.0, 1.2, 0.03], (0.15, 0.55, 0.4, 1))

    # The test area: 3.6 x 3.0 m around the start, fenced by low hazard rails with a gate on the
    # west side. 4 cm rails: enough to stop a walking duck, low enough to see over.
    rail = (0.95, 0.75, 0.05, 1)
    rh, rt = 0.04, 0.03
    _wall(spec, "rail_n", -1.8, 1.5, 1.8, 1.5, rh, rt, rail)
    _wall(spec, "rail_s", -1.8, -1.5, 1.8, -1.5, rh, rt, rail)
    _wall(spec, "rail_e", 1.8, -1.5, 1.8, 1.5, rh, rt, rail)
    _wall(spec, "rail_w1", -1.8, 0.45, -1.8, 1.5, rh, rt, rail)
    _wall(spec, "rail_w2", -1.8, -1.5, -1.8, -0.45, rh, rt, rail)

    # Measurement: tick marks every 0.5 m along +x from the start, and a calibration board.
    for i in range(1, 7):
        x = i * 0.25
        long = i % 2 == 0
        _decal(spec.worldbody, f"tick_{i}", BOX, [0.004, 0.06 if long else 0.03, 0.001],
               [x, 0, 0.0011], (0.95, 0.95, 0.95, 1))
    _decal(spec.worldbody, "axis_x", BOX, [0.8, 0.003, 0.001], [0.8, 0, 0.001], (0.9, 0.2, 0.2, 1))
    _decal(spec.worldbody, "axis_y", BOX, [0.003, 0.8, 0.001], [0, 0.8, 0.001], (0.2, 0.8, 0.3, 1))
    _decal(spec.worldbody, "start_pad", CYL, [0.16, 0.001], [0, 0, 0.0009], (0.1, 0.7, 0.5, 0.6))
    _geom(spec.worldbody, "calib_board", BOX, [0.01, 0.3, 0.3], [1.72, 0.9, 0.34], (1, 1, 1, 1),
          material="calib")
    _geom(spec.worldbody, "calib_stand", BOX, [0.08, 0.08, 0.02], [1.72, 0.9, 0.02], grey)

    # Obstacle course inside the fence: three foam blocks the duck can shove.
    _movable_block(spec, "foam_1", 1.0, -0.6, 0.05, (0.9, 0.3, 0.3, 1), 0.04)
    _movable_block(spec, "foam_2", 1.3, -0.3, 0.05, (0.3, 0.5, 0.9, 1), 0.04)
    _movable_block(spec, "foam_3", 0.7, -0.95, 0.05, (0.95, 0.8, 0.2, 1), 0.04)
    # A fixed low step-plate, 1 cm — the first thing to test a gait against.
    _geom(spec.worldbody, "step_plate", BOX, [0.25, 0.25, 0.005], [-0.9, -0.8, 0.005], (0.3, 0.32, 0.34, 1))


# ── the garden ────────────────────────────────────────────────────────────────


def build_garden(spec: mujoco.MjSpec, opts: dict) -> None:
    _tex(spec, "grass_t", (0.30, 0.50, 0.20), (0.26, 0.45, 0.18),
         mark=mujoco.mjtMark.mjMARK_RANDOM, markrgb=(0.38, 0.6, 0.25), size=512, random_=0.25)
    _mat(spec, "grass", "grass_t", repeat=(3, 3))
    _floor(spec, "grass")
    _tex(spec, "paver_t", (0.70, 0.66, 0.60), (0.60, 0.56, 0.50),
         mark=mujoco.mjtMark.mjMARK_EDGE, markrgb=(0.42, 0.40, 0.36), size=256)
    _mat(spec, "paver", "paver_t", repeat=(3, 3))

    # Paths: a straight run east from the start and a branch north. Flat decals.
    _decal(spec.worldbody, "path_e", BOX, [2.4, 0.35, 0.001], [1.9, 0, 0.001], (1, 1, 1, 1), material="paver")
    _decal(spec.worldbody, "path_n", BOX, [0.35, 1.8, 0.001], [3.0, 2.15, 0.0012], (1, 1, 1, 1), material="paver")
    _decal(spec.worldbody, "start_pad", CYL, [0.18, 0.001], [0, 0, 0.0014], (0.95, 0.95, 0.85, 0.7))

    rng = random.Random(opts.get("seed", 7))
    bark, leaf = (0.36, 0.25, 0.16, 1), (0.20, 0.42, 0.18, 1)
    # Trees: only the trunk is in the duck's world; the canopy is far above it but still a geom.
    for i, (x, y, r) in enumerate([(-2.5, 2.2, 0.09), (-3.2, -1.8, 0.12), (4.2, -2.6, 0.1), (1.2, 3.6, 0.08)]):
        tree = spec.worldbody.add_body(name=f"tree_{i}", pos=[x, y, 0])
        _geom(tree, f"tree_{i}_trunk", CYL, [r, 0.9], [0, 0, 0.9], bark)
        _geom(tree, f"tree_{i}_canopy", SPHERE, [0.75 + r], [0, 0, 2.1], leaf)
        _geom(tree, f"tree_{i}_canopy2", SPHERE, [0.55], [0.35, -0.2, 2.6], (0.24, 0.48, 0.2, 1))
    # Rocks along the path edge — collidable ellipsoids.
    for i in range(9):
        x = rng.uniform(-0.5, 4.2)
        y = rng.choice([-1, 1]) * rng.uniform(0.6, 1.4)
        sx, sy, sz = rng.uniform(0.06, 0.16), rng.uniform(0.05, 0.13), rng.uniform(0.04, 0.09)
        g = rng.uniform(0.42, 0.55)
        _geom(spec.worldbody, f"rock_{i}", ELLIPSOID, [sx, sy, sz], [x, y, sz * 0.7],
              (g, g * 0.97, g * 0.92, 1), quat=_quat_z(rng.uniform(0, math.pi)))
    # Raised flower beds with a timber border (12 cm) and plants in them.
    for i, (x, y, sx, sy) in enumerate([(-1.8, -2.3, 1.0, 0.45), (1.5, 2.2, 0.8, 0.5)]):
        _wall(spec, f"bed_{i}_n", x - sx, y + sy, x + sx, y + sy, 0.12, 0.05, bark)
        _wall(spec, f"bed_{i}_s", x - sx, y - sy, x + sx, y - sy, 0.12, 0.05, bark)
        _wall(spec, f"bed_{i}_e", x + sx, y - sy, x + sx, y + sy, 0.12, 0.05, bark)
        _wall(spec, f"bed_{i}_w", x - sx, y - sy, x - sx, y + sy, 0.12, 0.05, bark)
        _geom(spec.worldbody, f"bed_{i}_soil", BOX, [sx - 0.03, sy - 0.03, 0.05], [x, y, 0.05], (0.25, 0.17, 0.1, 1))
        for j in range(6):
            px = x + rng.uniform(-sx + 0.15, sx - 0.15)
            py = y + rng.uniform(-sy + 0.12, sy - 0.12)
            colour = rng.choice([(0.85, 0.2, 0.3, 1), (0.95, 0.8, 0.2, 1), (0.6, 0.3, 0.8, 1)])
            _geom(spec.worldbody, f"plant_{i}_{j}_stem", CYL, [0.008, 0.08], [px, py, 0.18], (0.2, 0.5, 0.15, 1))
            _geom(spec.worldbody, f"plant_{i}_{j}_bloom", SPHERE, [0.04], [px, py, 0.28], colour)
    # Bushes.
    for i, (x, y) in enumerate([(-0.8, 1.7), (3.8, 0.9), (-3.5, 0.4)]):
        _geom(spec.worldbody, f"bush_{i}", ELLIPSOID, [0.35, 0.3, 0.28], [x, y, 0.22], (0.18, 0.4, 0.16, 1))
    # A bench and a pond with a stone kerb (the kerb is what stops a duck walking into it).
    _table(spec, "bench", -2.3, 0.0, 1.2, 0.4, 0.45, (0.55, 0.38, 0.22, 1), (0.2, 0.2, 0.22, 1), yaw=math.pi / 2)
    _decal(spec.worldbody, "pond", CYL, [0.7, 0.001], [-0.2, -3.6, 0.001], (0.2, 0.45, 0.65, 0.9))
    for k in range(16):
        a = 2 * math.pi * k / 16
        _geom(spec.worldbody, f"kerb_{k}", BOX, [0.15, 0.06, 0.05],
              [-0.2 + 0.78 * math.cos(a), -3.6 + 0.78 * math.sin(a), 0.05], (0.55, 0.53, 0.5, 1),
              quat=_quat_z(a + math.pi / 2))
    # Gentle ramp, 5 degrees — terrain the policies were NOT validated on. Marked as such.
    slope = math.radians(5)
    _geom(spec.worldbody, "ramp_untested", BOX, [0.8, 0.5, 0.02], [3.4, -1.9, 0.8 * math.sin(slope) - 0.01],
          (0.55, 0.45, 0.35, 1), quat=_quat_y(-slope))
    # A fence around the 11 x 9 m plot.
    wood = (0.5, 0.36, 0.22, 1)
    for name, (a, b, c, d) in {"n": (-5, 4.5, 5.5, 4.5), "s": (-5, -4.5, 5.5, -4.5),
                               "e": (5.5, -4.5, 5.5, 4.5), "w": (-5, -4.5, -5, 4.5)}.items():
        _wall(spec, f"fence_{name}", a, b, c, d, 0.6, 0.04, wood)


# ── the arena ─────────────────────────────────────────────────────────────────

ARENA_GOALS = [Marker("goal_1", 2.0, 0.0), Marker("goal_2", 2.0, 1.5), Marker("goal_3", -1.5, 1.5)]


def build_arena(spec: mujoco.MjSpec, opts: dict) -> None:
    _tex(spec, "arena_t", (0.86, 0.87, 0.88), (0.82, 0.83, 0.84),
         mark=mujoco.mjtMark.mjMARK_EDGE, markrgb=(0.62, 0.64, 0.66), size=512)
    _mat(spec, "arena_floor", "arena_t", repeat=(4, 4), reflectance=0.04)  # 4 tiles/m → 0.125 m cells
    _floor(spec, "arena_floor")
    edge = (0.30, 0.33, 0.37, 1)
    for name, (a, b, c, d) in {"n": (-3, 3, 3, 3), "s": (-3, -3, 3, -3),
                               "e": (3, -3, 3, 3), "w": (-3, -3, -3, 3)}.items():
        _wall(spec, f"arena_wall_{name}", a, b, c, d, 0.3, 0.05, edge)
    _decal(spec.worldbody, "start_pad", CYL, [0.16, 0.001], [0, 0, 0.0012], (0.1, 0.75, 0.55, 0.85))
    for m in ARENA_GOALS:
        _decal(spec.worldbody, f"{m.name}_pad", CYL, [0.15, 0.001], [m.x, m.y, 0.0012], (0.95, 0.55, 0.1, 0.85))
        _decal(spec.worldbody, f"{m.name}_ring", CYL, [0.2, 0.0008], [m.x, m.y, 0.0006], (0.15, 0.15, 0.15, 0.9))

    layout = opts.get("layout", "open")
    cone = (0.95, 0.45, 0.08, 1)
    if layout == "slalom":
        # Poles on the start→goal_1 line, offset a little so the route has to weave.
        for i, x in enumerate([0.5, 1.0, 1.5]):
            _geom(spec.worldbody, f"cone_{i}", CYL, [0.05, 0.12], [x, -0.05 if i % 2 else 0.05, 0.12], cone)
    elif layout == "corridor":
        _wall(spec, "corridor_l", 0.4, 0.32, 1.7, 0.32, 0.25, 0.04, edge)
        _wall(spec, "corridor_r", 0.4, -0.32, 1.7, -0.32, 0.25, 0.04, edge)
    elif layout == "scatter":
        rng = random.Random(opts.get("seed", 3))
        keep_clear = [(0.0, 0.0)] + [(m.x, m.y) for m in ARENA_GOALS]
        placed = 0
        while placed < 8:
            x, y = rng.uniform(-2.5, 2.5), rng.uniform(-2.5, 2.5)
            if any(math.hypot(x - a, y - b) < 0.55 for a, b in keep_clear):
                continue
            s = rng.uniform(0.06, 0.14)
            _geom(spec.worldbody, f"block_{placed}", BOX, [s, s, s], [x, y, s], (0.3, 0.33, 0.38, 1),
                  quat=_quat_z(rng.uniform(0, math.pi)))
            placed += 1
    elif layout != "open":
        raise SystemExit(f"unknown arena layout {layout!r}: open, slalom, corridor, scatter")


# ── the hangar ────────────────────────────────────────────────────────────────


def _quat_y90():
    return _quat_y(math.pi / 2)


def build_hangar(spec: mujoco.MjSpec, opts: dict) -> None:
    """An orbital hangar deck. The duck stands on a raised pedestal (top at z = 0, the robot's own
    floor height) and the deck lies 5 cm below it — a real ledge: walk off it and you fall off it.
    The planet and stars behind the railing are a *backdrop* drawn by the viewer; nothing out
    there is part of the physics, and the railing keeps the duck from reaching the deck's edge."""
    deck = -0.05
    _tex(spec, "deck_t", (0.30, 0.32, 0.35), (0.25, 0.27, 0.30),
         mark=mujoco.mjtMark.mjMARK_EDGE, markrgb=(0.12, 0.13, 0.15), size=512)
    _mat(spec, "deck", "deck_t", repeat=(1, 1), reflectance=0.25)  # 0.5 m deck plates
    floor = spec.geom("floor")
    floor.material = "deck"
    floor.pos = [0.0, 0.0, deck]
    floor.size = [5.0, 4.8, 0.05]  # drawn size; MuJoCo planes are infinite for contacts
    _tex(spec, "pad_t", (0.52, 0.55, 0.59), (0.47, 0.50, 0.54), size=256)
    _mat(spec, "pad", "pad_t", repeat=(1, 1), reflectance=0.2)

    # The pedestal: a white disc on a blue-lit rim on a dark metal foot.
    _geom(spec.worldbody, "pedestal_foot", CYL, [0.98, 0.012], [0, 0, deck + 0.012], (0.16, 0.18, 0.22, 1))
    _geom(spec.worldbody, "pedestal_rim", CYL, [0.80, 0.019], [0, 0, -0.019 - 0.002], (0.45, 0.72, 0.95, 1))
    _geom(spec.worldbody, "pedestal_top", CYL, [0.76, 0.0125], [0, 0, -0.0125], (1, 1, 1, 1), material="pad")

    steel, dark, panel = (0.62, 0.65, 0.70, 1), (0.16, 0.17, 0.20, 1), (0.30, 0.32, 0.36, 1)
    # Port side: a bulkhead with three big coolant pipes and brackets.
    _wall(spec, "bulkhead_port", -5.0, 4.6, 5.0, 4.6, 3.0, 0.25, panel)
    for i, z in enumerate([0.55, 0.95, 1.35]):
        _geom(spec.worldbody, f"pipe_{i}", CYL, [0.13, 5.0], [0.0, 4.25, deck + z], (0.20, 0.22, 0.26, 1),
              quat=_quat_y90())
        for k, x in enumerate([-3.6, -1.2, 1.2, 3.6]):
            _geom(spec.worldbody, f"pipe_{i}_collar_{k}", CYL, [0.15, 0.05], [x, 4.25, deck + z], steel, quat=_quat_y90())
    for k, x in enumerate([-4.0, -1.6, 0.8, 3.2]):
        _geom(spec.worldbody, f"pipe_bracket_{k}", BOX, [0.04, 0.2, 0.75], [x, 4.4, deck + 0.95], dark)
    # Starboard side: consoles and wall panels.
    _wall(spec, "bulkhead_stbd", -5.0, -4.6, 5.0, -4.6, 3.0, 0.25, panel)
    for k, x in enumerate([-3.0, -1.0, 1.0, 3.0]):
        _geom(spec.worldbody, f"console_{k}", BOX, [0.45, 0.22, 0.42], [x, -4.2, deck + 0.42], dark)
        _geom(spec.worldbody, f"console_{k}_screen", BOX, [0.36, 0.01, 0.16], [x, -3.975, deck + 0.70],
              (0.10, 0.45, 0.55, 1), solid=False)
    # Aft: the railing at the deck's edge, with the view out to space beyond it.
    for k in range(10):
        y = -4.5 + 1.0 * k
        _geom(spec.worldbody, f"rail_post_{k}", BOX, [0.025, 0.025, 0.45], [-4.6, y, deck + 0.45], steel)
    _wall(spec, "rail_top", -4.6, -4.5, -4.6, 4.5, 0.04, 0.05, steel)
    spec.geom("rail_top").pos = [-4.6, 0.0, deck + 0.9]
    _wall(spec, "rail_mid", -4.6, -4.5, -4.6, 4.5, 0.03, 0.03, steel)
    spec.geom("rail_mid").pos = [-4.6, 0.0, deck + 0.45]
    _wall(spec, "rail_kick", -4.6, -4.5, -4.6, 4.5, 0.1, 0.04, dark)
    spec.geom("rail_kick").pos = [-4.6, 0.0, deck + 0.05]
    # Overhead gantry.
    for k, x in enumerate([-1.5, 1.5]):
        _geom(spec.worldbody, f"gantry_{k}", BOX, [0.12, 4.6, 0.12], [x, 0, deck + 2.8], dark)
    # Deck clutter: cargo crates and a charging cradle.
    for k, (x, y, sx, sy, sz) in enumerate([(1.9, 2.2, 0.3, 0.3, 0.3), (2.3, 1.7, 0.22, 0.22, 0.22),
                                              (1.6, -2.1, 0.35, 0.25, 0.25), (-1.6, 2.3, 0.25, 0.35, 0.4)]):
        _geom(spec.worldbody, f"crate_{k}", BOX, [sx, sy, sz], [x, y, deck + sz], (0.55, 0.42, 0.18, 1),
              quat=_quat_z(0.2 * k))
    _geom(spec.worldbody, "cradle", BOX, [0.18, 0.25, 0.04], [-1.4, -1.6, deck + 0.04], (0.15, 0.5, 0.45, 1))
    for k, (x, y) in enumerate([(1.2, 1.2), (1.2, -1.2), (-1.2, 1.2), (-1.2, -1.2)]):
        _decal(spec.worldbody, f"deck_light_{k}", CYL, [0.06, 0.001], [x, y, deck + 0.0011], (0.55, 0.8, 1.0, 1))


STAGE_BARRIER_R = 2.0  # inner face of the glass ring


def build_stage(spec: mujoco.MjSpec, opts: dict) -> None:
    """The scenery stage: the hangar's pedestal on an open floor inside a low glass ring.

    It exists so the viewer can put a photographed world around the duck (meadow, beach, city…)
    without a hangar's walls in the way. What the duck can touch is only what is here: the deck,
    the pedestal (the same real 5 cm ledge as the hangar's) and the ring, 14 cm of solid glass at
    2 m. The photographs, the ground they show beyond the ring and the background ducks are
    drawn by the viewer only."""
    deck = -0.05
    _tex(spec, "stage_t", (0.36, 0.38, 0.40), (0.33, 0.35, 0.37), size=256)
    _mat(spec, "stage", "stage_t", repeat=(1, 1), reflectance=0.1)
    floor = spec.geom("floor")
    floor.material = "stage"
    floor.pos = [0.0, 0.0, deck]
    floor.size = [STAGE_BARRIER_R + 0.6, STAGE_BARRIER_R + 0.6, 0.05]  # drawn size; contacts are infinite
    _tex(spec, "pad_t", (0.52, 0.55, 0.59), (0.47, 0.50, 0.54), size=256)
    _mat(spec, "pad", "pad_t", repeat=(1, 1), reflectance=0.2)
    _geom(spec.worldbody, "pedestal_foot", CYL, [0.98, 0.012], [0, 0, deck + 0.012], (0.16, 0.18, 0.22, 1))
    _geom(spec.worldbody, "pedestal_rim", CYL, [0.80, 0.019], [0, 0, -0.019 - 0.002], (0.45, 0.72, 0.95, 1))
    _geom(spec.worldbody, "pedestal_top", CYL, [0.76, 0.0125], [0, 0, -0.0125], (1, 1, 1, 1), material="pad")
    n, height, thick = 40, 0.14, 0.012
    r = STAGE_BARRIER_R + thick / 2
    half_len = r * math.tan(math.pi / n) + 0.002  # neighbours overlap a hair: no gap to squeeze through
    for k in range(n):
        a = 2 * math.pi * k / n
        _geom(spec.worldbody, f"barrier_{k}", BOX, [thick / 2, half_len, height / 2],
              [r * math.cos(a), r * math.sin(a), deck + height / 2], (0.78, 0.9, 1.0, 0.16), quat=_quat_z(a))
        _geom(spec.worldbody, f"barrier_cap_{k}", BOX, [thick / 2 + 0.004, half_len, 0.006],
              [r * math.cos(a), r * math.sin(a), deck + height + 0.006], (0.55, 0.82, 1.0, 1), quat=_quat_z(a))


ENVIRONMENTS: dict[str, EnvInfo] = {
    "stage": EnvInfo(
        "stage", "Scenery stage",
        "The hangar's pedestal (a real 5 cm ledge) on an open deck inside a 14 cm glass ring at 2 m. "
        "The photographed worlds around it and the ducks playing on the deck are viewer-only, not physics.",
        "scene_allcollisions.xml", build_stage,
        markers=[Marker("start", 0, 0, "start"), Marker("front", 0.45, 0.0, "goal"),
                 Marker("left_edge", 0.0, 0.45, "goal"), Marker("right_edge", 0.0, -0.45, "goal")],
        bounds=(-0.72, 0.72, -0.72, 0.72),
        look=dict(sky="#9cc7ee", ground="#3a3f44", sun=[2, -2, 6], backdrop="scenery", indoor=False,
                  showcase=True, play_area=[1.15, STAGE_BARRIER_R - 0.25]),
    ),
    "hangar": EnvInfo(
        "hangar", "Orbital hangar",
        "A hangar deck in orbit: the duck on a raised pedestal (a real 5 cm ledge), coolant pipes, consoles, "
        "crates and a railing. The planet beyond the railing is a viewer backdrop, not physics.",
        "scene_allcollisions.xml", build_hangar,
        markers=[Marker("start", 0, 0, "start"), Marker("front", 0.45, 0.0, "goal"),
                 Marker("left_edge", 0.0, 0.45, "goal"), Marker("right_edge", 0.0, -0.45, "goal")],
        bounds=(-0.72, 0.72, -0.72, 0.72),
        look=dict(sky="#05070d", ground="#2b3038", sun=[1.5, -2.5, 6], backdrop="space", indoor=False,
                  showcase=True, play_area=[1.15, 1.75]),  # clear deck between pedestal and cradle
    ),
    "bare": EnvInfo(
        "bare", "Bare floor (original)",
        "microduck_rl's default duck-body scene, verbatim: the groundcontact robot on a checker plane.",
        "scene.xml", original=True,
        markers=[Marker("start", 0, 0, "start")],
        look=dict(sky="#20303f", ground="#1b2836", sun=[2, -3, 6], fog=[8, 30]),
    ),
    "apartment": EnvInfo(
        "apartment", "Modern apartment (original)",
        "microduck_rl's scene_apartment.xml, verbatim: six rooms in 7x6 m, furniture, off-centre doorways.",
        "scene_apartment.xml", original=True,
        markers=[Marker("start", 0, 0, "start")],
        bounds=(-4.0, 4.0, -3.0, 3.0),
        look=dict(sky="#cfd8e0", ground="#8a7a66", sun=[3, -2, 6], fog=[10, 40], indoor=True),
    ),
    "lab": EnvInfo(
        "lab", "Robotics laboratory",
        "8x6 m lab: workbenches, rack, fenced 3.6x3 m test area with tick marks, calibration board, "
        "three pushable foam blocks and a 1 cm step plate.",
        "scene_allcollisions.xml", build_lab,
        markers=[Marker("start", 0, 0, "start"), Marker("calibration", 1.4, 0.9, "station"),
                 Marker("one_metre", 1.0, 0.0, "goal"), Marker("dock", -3.0, 1.2, "station")],
        bounds=(-4.0, 4.0, -3.0, 3.0),
        look=dict(sky="#dfe4e8", ground="#5d6266", sun=[1.5, -2, 5], fog=[12, 40], indoor=True),
    ),
    "garden": EnvInfo(
        "garden", "Outdoor garden",
        "11x9 m garden: paved path, trees, rocks, raised beds, bushes, bench, kerbed pond, and a 5-degree "
        "ramp the policies were never validated on.",
        "scene_allcollisions.xml", build_garden,
        markers=[Marker("start", 0, 0, "start"), Marker("path_end", 4.0, 0.0, "goal"),
                 Marker("north_path", 3.0, 3.2, "goal"), Marker("bench", -1.8, 0.0, "station")],
        bounds=(-5.0, 5.5, -4.5, 4.5),
        look=dict(sky="#9cc7ee", ground="#4d7a35", sun=[4, -3, 8], fog=[14, 60]),
    ),
    "arena": EnvInfo(
        "arena", "Training arena",
        "6x6 m walled arena with a start pad and three goals. Layouts: open, slalom, corridor, scatter.",
        "scene_allcollisions.xml", build_arena,
        markers=[Marker("start", 0, 0, "start")] + ARENA_GOALS,
        bounds=(-3.0, 3.0, -3.0, 3.0),
        look=dict(sky="#e8ecef", ground="#cfd3d6", sun=[2, -2, 7], fog=[12, 50], indoor=True),
        layouts=["open", "slalom", "corridor", "scatter"],
    ),
}


def scenes_dir(rl: Path) -> Path:
    return rl / "src" / "mjlab_microduck" / "robot" / "microduck"


def base_scene(rl: Path, env: str) -> Path:
    return scenes_dir(rl) / ENVIRONMENTS[env].base_scene


def build_spec(rl: Path, env: str, opts: dict | None = None) -> mujoco.MjSpec:
    """The environment as an uncompiled spec, ready for extra ducks to be attached."""
    info = ENVIRONMENTS[env]
    spec = mujoco.MjSpec.from_file(str(base_scene(rl, env)))
    if info.builder is not None:
        info.builder(spec, dict(opts or {}))
        # Keyframes describe the base model's qpos; scenery with free bodies changes nq. The
        # body server reads poses from the *base* scene file (see `xbody.py`), so the copies
        # carried into this spec are dropped rather than left to fail the compile.
        for key in list(spec.keys):
            spec.delete(key)
    return spec


def describe(env: str, opts: dict | None = None) -> dict:
    info = ENVIRONMENTS[env]
    return dict(
        name=info.name, title=info.title, description=info.description, original=info.original,
        base_scene=info.base_scene, bounds=list(info.bounds), look=info.look, layouts=info.layouts,
        layout=(opts or {}).get("layout", info.layouts[0]),
        markers=[dict(name=m.name, x=m.x, y=m.y, kind=m.kind) for m in info.markers],
    )
