"""The original Microduck body server, plus what a web digital twin needs — and nothing that
changes what the daemon sees.

    python xbody.py --env lab --port 7801 --state-port 7811

**What is reused, unchanged.** `World`, `Body`, `Handler`, `Server`, `pose_table`, the camera and
the ToF from `microduck_rl`'s `mjlab_microduck.sim.body_server` are imported, not copied. The
robotd-facing TCP protocol on `--port` is therefore byte-for-byte the original one, so
`robotd --sim 127.0.0.1:7801` cannot tell this process from `duck-body`. Same timestep (5 ms),
same 20 ms batching, same real-time pacing, same keyframes, same "held until the daemon enables
torque" rule.

**What is added.**

1. *Scenic worlds* — `environments.py` builds them with `MjSpec` on top of the original scenes.
2. *A state port* (`--state-port`, localhost only) — newline JSON, one request per line:

   ``{"op": "hello"}``       → protocol, environment, ducks
   ``{"op": "model"}``       → a JSON header line, then the binary mesh and texture blobs it names
   ``{"op": "subscribe", "hz": 30}`` → a state frame per line until the client disconnects
   ``{"op": "pause"}`` / ``{"op": "resume"}``
   ``{"op": "reset", "keyframe": "SIT"}`` → every duck back to its spawn, scenery restored
   ``{"op": "push", "duck": 0, "force": [fx, fy, fz], "seconds": 0.2}`` → a shove on the trunk
   ``{"op": "stats"}``       → real-time factor, steps, paused

   Frames are *ground truth*: the pose of every moving body, the trunk's world velocity, joint
   positions/velocities/targets, actuator force, and every contact with its normal force. No robot
   can measure most of this — the dashboard labels it "simulator ground truth" and shows robotd's
   own belief (its `robot.state` stream) beside it.

**Pause is physics only.** robotd's loop is wall-clock and keeps ticking while the world is frozen;
it reads the same sensors over and over. The dashboard therefore zeroes motion intents before it
pauses and refuses movement while paused. Long pauses are not something a real robot ever sees.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import socketserver
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RL = Path(os.environ.get("DUCK_SIM_RL", Path.home() / "Pollen" / "microduck_rl")).expanduser()
if not (RL / "src" / "mjlab_microduck" / "sim" / "body_server.py").exists():
    raise SystemExit(f"no microduck_rl at {RL} (set DUCK_SIM_RL). It provides the body server and the model.")
sys.path.insert(0, str(RL / "src"))
sys.path.insert(0, str(HERE))

import mujoco  # noqa: E402
import numpy as np  # noqa: E402

from mjlab_microduck.sim import body_server as bs  # noqa: E402
from mjlab_microduck.sim.camera import FPS as CAMERA_FPS  # noqa: E402
from mjlab_microduck.sim.camera import Camera, FrameHandler, FrameServer  # noqa: E402

import environments as envs  # noqa: E402

PROTOCOL = "xbody/1"
LETTERS = "abcdefghijklmnop"
MAX_CONTACTS = 96
MAX_TEXTURE_PX = 256


# ── building the world ────────────────────────────────────────────────────────


def make_world(env: str, ducks: int, opts: dict) -> bs.World:
    """`bs.World` with the scene built by `environments` instead of read from one file.

    `World.__init__` calls the module-level `build_world`; it is swapped for the duration of the
    constructor and put back, so nothing else in `body_server` ever sees the difference.
    """
    info = envs.ENVIRONMENTS[env]

    def build_world(_scene: Path, count: int) -> mujoco.MjModel:
        if info.original and count == 1:
            return mujoco.MjModel.from_xml_path(str(envs.base_scene(RL, env)))
        spec = envs.build_spec(RL, env, opts)
        if info.original:
            for key in list(spec.keys):
                spec.delete(key)
        for index in range(1, count):  # body_server.build_world's own multi-duck recipe
            robot = mujoco.MjSpec.from_file(str(bs.ROBOT_ONLY))
            frame = spec.worldbody.add_frame(pos=[0.0, index * bs.SPACING, 0.0])
            spec.attach(robot, prefix=bs.duck_prefix(index), frame=frame)
        return spec.compile()

    original = bs.build_world
    bs.build_world = build_world
    try:
        return bs.World(envs.base_scene(RL, env), ducks)
    finally:
        bs.build_world = original


class Twin:
    """Everything the state port needs that `World` does not already hold."""

    def __init__(self, world: bs.World, env: str, opts: dict, pose, trunk_z: float):
        self.world = world
        self.env = env
        self.opts = opts
        self.pose = pose
        self.trunk_z = trunk_z
        self.paused = False
        self.steps = 0
        self.rtf = 0.0
        self.behind = 0
        self.pushes: list[tuple[int, np.ndarray, float]] = []  # (body id, force, until sim time)
        self.initial_qpos = None
        self.initial_qvel = None
        model = world.model
        self.duck_bodies: list[set[int]] = []
        self.trunk_ids: list[int] = []
        self.feet: list[dict[str, int]] = []
        for body in world.bodies:
            trunk = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, body.prefix + "trunk_base")
            self.trunk_ids.append(trunk)
            self.duck_bodies.append({b for b in range(model.nbody) if model.body_rootid[b] == trunk})
            self.feet.append({
                side: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, f"{body.prefix}ankle_{side}")
                for side in ("left", "right")
            })
        self.robot_bodies = set().union(*self.duck_bodies) if self.duck_bodies else set()
        # Bodies whose pose can change: anything under a joint, i.e. not welded to the world.
        self.dynamic = [b for b in range(1, model.nbody) if self._moves(b)]
        self._model_cache: tuple[bytes, bytes, bytes] | None = None

    def _moves(self, b: int) -> bool:
        model = self.world.model
        while b != 0:
            if model.body_jntnum[b] > 0 or model.body_mocapid[b] >= 0:
                return True
            b = model.body_parentid[b]
        return False

    def snapshot_initial(self) -> None:
        self.initial_qpos = self.world.data.qpos.copy()
        self.initial_qvel = self.world.data.qvel.copy()

    # ── admin ops ─────────────────────────────────────────────────────────

    def reset(self, keyframe: str | None) -> dict:
        pose, trunk_z = self.pose, self.trunk_z
        if keyframe:
            pose, trunk_z = bs.pose_table(envs.base_scene(RL, self.env), keyframe)
        world = self.world
        with world.lock:
            world.data.qpos[:] = self.initial_qpos
            world.data.qvel[:] = 0.0
            world.data.xfrc_applied[:] = 0.0
            world.data.act[:] = 0.0
            self.pushes.clear()
        for body in world.bodies:
            with world.lock:
                body.place(pose, trunk_z, offset_y=body.index * bs.SPACING)
        with world.lock:
            mujoco.mj_forward(world.model, world.data)
        return {"ok": True, "keyframe": keyframe or "spawn", "sim_time": float(world.data.time)}

    def push(self, duck: int, force, seconds: float) -> dict:
        if not 0 <= duck < len(self.trunk_ids):
            raise ValueError(f"no duck {duck}")
        f = np.asarray(force, dtype=float)
        if f.shape != (3,) or not np.all(np.isfinite(f)):
            raise ValueError("force must be three finite numbers, newtons, world frame")
        if np.linalg.norm(f) > 20.0:
            raise ValueError("refusing a push above 20 N — this is an 800 g robot")
        seconds = float(min(max(seconds, 0.01), 1.0))
        with self.world.lock:
            self.pushes.append((self.trunk_ids[duck], f, float(self.world.data.time) + seconds))
        return {"ok": True, "force": f.tolist(), "seconds": seconds}

    def apply_pushes(self) -> None:
        """Called under the world lock, before each step batch."""
        data = self.world.data
        data.xfrc_applied[:, :3] = 0.0
        keep = []
        for body, f, until in self.pushes:
            if data.time < until:
                data.xfrc_applied[body, :3] += f
                keep.append((body, f, until))
        self.pushes = keep

    # ── the model, for the viewer ─────────────────────────────────────────

    def model_payload(self) -> tuple[bytes, bytes, bytes]:
        if self._model_cache is None:
            self._model_cache = self._build_model_payload()
        return self._model_cache

    def _build_model_payload(self) -> tuple[bytes, bytes, bytes]:
        model = self.world.model
        name = lambda kind, i: mujoco.mj_id2name(model, kind, i) or ""  # noqa: E731
        with self.world.lock:
            xpos = self.world.data.xpos.copy()
            xquat = self.world.data.xquat.copy()

        bodies = []
        for b in range(model.nbody):
            duck = next((i for i, s in enumerate(self.duck_bodies) if b in s), None)
            bodies.append(dict(
                id=b, name=name(mujoco.mjtObj.mjOBJ_BODY, b), parent=int(model.body_parentid[b]),
                duck=duck, dynamic=b in self.dynamic,
                pos=xpos[b].round(6).tolist(), quat=xquat[b].round(6).tolist(),
            ))
        geoms = []
        for g in range(model.ngeom):
            geoms.append(dict(
                id=g, name=name(mujoco.mjtObj.mjOBJ_GEOM, g), body=int(model.geom_bodyid[g]),
                type=int(model.geom_type[g]), size=model.geom_size[g].round(6).tolist(),
                pos=model.geom_pos[g].round(6).tolist(), quat=model.geom_quat[g].round(6).tolist(),
                rgba=model.geom_rgba[g].round(4).tolist(), group=int(model.geom_group[g]),
                mesh=int(model.geom_dataid[g]) if model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH else -1,
                material=int(model.geom_matid[g]),
                collides=bool(model.geom_contype[g] or model.geom_conaffinity[g]),
            ))
        # Meshes exactly as MuJoCo compiled them (recentred, scaled) — not the STL files, whose
        # frames differ from the geom frames above.
        meshes, vert_chunks, face_chunks = [], [], []
        voff = foff = 0
        for m in range(model.nmesh):
            va, vn = int(model.mesh_vertadr[m]), int(model.mesh_vertnum[m])
            fa, fn = int(model.mesh_faceadr[m]), int(model.mesh_facenum[m])
            vert_chunks.append(model.mesh_vert[va:va + vn].astype("<f4").tobytes())
            face_chunks.append(model.mesh_face[fa:fa + fn].astype("<u4").tobytes())
            meshes.append(dict(id=m, name=name(mujoco.mjtObj.mjOBJ_MESH, m),
                               vert_offset=voff, vert_count=vn, face_offset=foff, face_count=fn))
            voff += vn * 12
            foff += fn * 12
        mesh_blob = b"".join(vert_chunks) + b"".join(face_chunks)
        for m in meshes:
            m["face_offset"] += voff

        materials = []
        for m in range(model.nmat):
            tex = int(model.mat_texid[m, int(mujoco.mjtTextureRole.mjTEXROLE_RGB)])
            materials.append(dict(
                id=m, name=name(mujoco.mjtObj.mjOBJ_MATERIAL, m), rgba=model.mat_rgba[m].round(4).tolist(),
                texture=tex, texrepeat=model.mat_texrepeat[m].round(4).tolist(),
                texuniform=bool(model.mat_texuniform[m]), reflectance=float(model.mat_reflectance[m]),
                specular=float(model.mat_specular[m]), shininess=float(model.mat_shininess[m]),
            ))
        # 2D textures, box-filtered down to at most 256 px so a 1024² checker costs 192 KB, not 3 MB.
        textures, tex_chunks, toff = [], [], 0
        for t in range(model.ntex):
            if model.tex_type[t] != mujoco.mjtTexture.mjTEXTURE_2D:
                continue
            w, h, c = int(model.tex_width[t]), int(model.tex_height[t]), int(model.tex_nchannel[t])
            adr = int(model.tex_adr[t])
            img = model.tex_data[adr:adr + w * h * c].reshape(h, w, c)
            step = max(1, max(w, h) // MAX_TEXTURE_PX)
            if step > 1:
                hh, ww = (h // step) * step, (w // step) * step
                img = img[:hh, :ww].reshape(hh // step, step, ww // step, step, c).mean(axis=(1, 3))
            img = np.ascontiguousarray(img.astype(np.uint8))
            if c == 3:
                img = np.concatenate([img, np.full(img.shape[:2] + (1,), 255, np.uint8)], axis=2)
            data = img.tobytes()
            textures.append(dict(id=t, name=name(mujoco.mjtObj.mjOBJ_TEXTURE, t), width=img.shape[1],
                                 height=img.shape[0], offset=toff, length=len(data)))
            tex_chunks.append(data)
            toff += len(data)
        tex_blob = b"".join(tex_chunks)

        joints = []
        for j in range(model.njnt):
            joints.append(dict(name=name(mujoco.mjtObj.mjOBJ_JOINT, j), body=int(model.jnt_bodyid[j]),
                               type=int(model.jnt_type[j]), pos=model.jnt_pos[j].round(6).tolist(),
                               axis=model.jnt_axis[j].round(6).tolist(),
                               range=model.jnt_range[j].round(6).tolist(), limited=bool(model.jnt_limited[j])))
        cameras = [dict(name=name(mujoco.mjtObj.mjOBJ_CAMERA, c), body=int(model.cam_bodyid[c]),
                        pos=model.cam_pos[c].round(6).tolist(), quat=model.cam_quat[c].round(6).tolist(),
                        fovy=float(model.cam_fovy[c])) for c in range(model.ncam)]
        ducks = [dict(index=b.index, name=f"duck-{LETTERS[b.index]}", prefix=b.prefix,
                      trunk_body=self.trunk_ids[b.index], feet=self.feet[b.index],
                      robotd_port=None) for b in self.world.bodies]
        header = dict(
            protocol=PROTOCOL, env=envs.describe(self.env, self.opts), timestep=float(model.opt.timestep),
            joint_names=list(bs.JOINT_NAMES), dynamic_bodies=self.dynamic,
            bodies=bodies, geoms=geoms, meshes=meshes, materials=materials, textures=textures,
            joints=joints, cameras=cameras, ducks=ducks,
            mesh_bytes=len(mesh_blob), texture_bytes=len(tex_blob),
            source=dict(rl=str(RL), base_scene=envs.ENVIRONMENTS[self.env].base_scene,
                        mujoco=mujoco.__version__),
        )
        return json.dumps(header, separators=(",", ":")).encode(), mesh_blob, tex_blob

    # ── one state frame ───────────────────────────────────────────────────

    def frame(self, seq: int) -> dict:
        world, model = self.world, self.world.model
        data = world.data
        with world.lock:
            t = float(data.time)
            xpos = data.xpos[self.dynamic].copy()
            xquat = data.xquat[self.dynamic].copy()
            ducks = []
            for body in world.bodies:
                tr, dof = body.trunk, body.trunk_dof
                quat = data.qpos[tr + 3:tr + 7].copy()
                ducks.append(dict(
                    trunk=data.qpos[tr:tr + 3].copy(), quat=quat,
                    linvel=data.qvel[dof:dof + 3].copy(), angvel=data.qvel[dof + 3:dof + 6].copy(),
                    q=data.qpos[body.qpos_adr].copy(), qd=data.qvel[body.qvel_adr].copy(),
                    ctrl=data.ctrl[body.actuator_slice].copy(), force=data.actuator_force[body.actuator_slice].copy(),
                    torque_on=bool(body.torque_on), released=bool(body.released), kp=float(body.kp),
                    to_wire=list(body.to_wire),
                ))
            contacts = []
            f6 = np.zeros(6)
            for i in range(min(data.ncon, MAX_CONTACTS)):
                c = data.contact[i]
                mujoco.mj_contactForce(model, data, i, f6)
                contacts.append((c.pos.copy(), int(c.geom1), int(c.geom2), float(f6[0])))

        out_ducks = []
        for d, duck in enumerate(ducks):
            wire = lambda arr: _to_wire(arr, duck["to_wire"])  # noqa: E731
            feet = {side: False for side in ("left", "right")}
            touching = 0
            for _, g1, g2, fn in contacts:
                b1, b2 = int(model.geom_bodyid[g1]), int(model.geom_bodyid[g2])
                mine1, mine2 = b1 in self.duck_bodies[d], b2 in self.duck_bodies[d]
                if mine1 == mine2:
                    continue
                touching += 1
                for side, foot in self.feet[d].items():
                    if foot >= 0 and (_is_under(model, b1 if mine1 else b2, foot)):
                        feet[side] = feet[side] or fn > 0.05
            out_ducks.append(dict(
                trunk=_r(duck["trunk"]), quat=_r(duck["quat"]),
                gravity=_r(bs.gravity_in_trunk(duck["quat"])), yaw=round(_yaw(duck["quat"]), 5),
                linvel=_r(duck["linvel"]), angvel=_r(duck["angvel"]),
                q=wire(duck["q"]), qd=wire(duck["qd"]), ctrl=wire(duck["ctrl"]), force=wire(duck["force"]),
                torque_on=duck["torque_on"], released=duck["released"], kp=duck["kp"],
                feet=feet, external_contacts=touching,
            ))
        return dict(
            seq=seq, t=round(t, 5), wall=time.time(), paused=self.paused, rtf=round(self.rtf, 3),
            xpos=_r(xpos.ravel()), xquat=_r(xquat.ravel()), ducks=out_ducks,
            contacts=[[*_r(p, 4), g1, g2, round(fn, 3)] for p, g1, g2, fn in contacts],
        )

    def stats(self) -> dict:
        return dict(env=self.env, opts=self.opts, paused=self.paused, rtf=round(self.rtf, 3),
                    steps=self.steps, behind=self.behind, sim_time=float(self.world.data.time),
                    ducks=len(self.world.bodies))


def _r(arr, nd: int = 5) -> list[float]:
    return [round(float(v), nd) for v in np.asarray(arr).ravel()]


def _to_wire(values, to_wire) -> list[float | None]:
    """Model order → `JOINT_NAMES` order. `None` for `mouth`, which the model does not have."""
    out: list[float | None] = [None] * len(bs.JOINT_NAMES)
    for slot, wire_index in enumerate(to_wire):
        out[wire_index] = round(float(values[slot]), 5)
    return out


def _yaw(q) -> float:
    w, x, y, z = (float(v) for v in q)
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _is_under(model, body: int, ancestor: int) -> bool:
    while body > 0:
        if body == ancestor:
            return True
        body = int(model.body_parentid[body])
    return False


# ── the state port ────────────────────────────────────────────────────────────


class StateHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        twin: Twin = self.server.twin
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        for raw in self.rfile:
            try:
                request = json.loads(raw)
                op = request.get("op")
                if op == "subscribe":
                    self.stream(twin, float(request.get("hz", 30)))
                    return
                if op == "model":
                    header, mesh, tex = twin.model_payload()
                    self.wfile.write(header + b"\n" + mesh + tex)
                    self.wfile.flush()
                    continue
                answer = self.dispatch(twin, op, request)
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as error:  # a bad request must not take the simulator down
                answer = {"error": str(error)}
            self.wfile.write((json.dumps(answer) + "\n").encode())
            self.wfile.flush()

    def dispatch(self, twin: Twin, op, request: dict) -> dict:
        if op == "hello":
            return dict(protocol=PROTOCOL, env=twin.env, opts=twin.opts, ducks=len(twin.world.bodies),
                        robot_protocol=bs.PROTOCOL)
        if op == "pause":
            twin.paused = True
            return {"ok": True, "paused": True}
        if op == "resume":
            twin.paused = False
            return {"ok": True, "paused": False}
        if op == "reset":
            return twin.reset(request.get("keyframe"))
        if op == "push":
            return twin.push(int(request.get("duck", 0)), request.get("force", [0, 0, 0]),
                             float(request.get("seconds", 0.2)))
        if op == "stats":
            return twin.stats()
        if op == "environments":
            return {"current": twin.env, "opts": twin.opts,
                    "environments": [envs.describe(name) for name in envs.ENVIRONMENTS]}
        raise ValueError(f"unknown op {op!r}")

    def stream(self, twin: Twin, hz: float) -> None:
        period = 1.0 / min(max(hz, 1.0), 60.0)
        seq = 0
        next_at = time.perf_counter()
        while True:
            seq += 1
            line = json.dumps(twin.frame(seq), separators=(",", ":")) + "\n"
            try:
                self.wfile.write(line.encode())
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return
            next_at += period
            delay = next_at - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_at = time.perf_counter()


# ── stepping ──────────────────────────────────────────────────────────────────


def run(twin: Twin, viewer_on: bool) -> None:
    """`body_server.run`, with pause, shoves, and a measured real-time factor."""
    world = twin.world
    viewer = None
    if viewer_on:
        try:
            import mujoco.viewer

            viewer = mujoco.viewer.launch_passive(world.model, world.data, show_left_ui=False, show_right_ui=False)
        except Exception as error:
            print(f"== no native viewer ({error}); running headless", flush=True)
    dt = world.model.opt.timestep
    batch = max(1, round(0.020 / dt))
    period = batch * dt
    passes_per_frame = max(1, round((1.0 / 30.0) / period))
    eyes = [b for b in world.bodies if b.camera is not None]
    passes_per_eye = max(1, round((1.0 / CAMERA_FPS) / period))
    step = 0
    next_step = time.perf_counter()
    window_wall, window_sim = time.perf_counter(), float(world.data.time)
    try:
        while True:
            if not twin.paused:
                with world.lock:
                    twin.apply_pushes()
                world.step(batch)
                twin.steps += batch
            if viewer is not None and not viewer.is_running():
                break
            next_step += period
            slack = next_step - time.perf_counter()
            if slack > 0.002:
                time.sleep(slack)
            elif slack < -0.25:
                twin.behind += 1
                print(f"== behind real time by {-slack:.2f}s (x{twin.behind}) — fewer ducks, or no cameras", flush=True)
                next_step = time.perf_counter()
            step += 1
            now = time.perf_counter()
            if now - window_wall >= 1.0:
                sim_now = float(world.data.time)
                twin.rtf = (sim_now - window_sim) / (now - window_wall)
                window_wall, window_sim = now, sim_now
            if viewer is not None and step % passes_per_frame == 0:
                viewer.sync()
            if eyes and not twin.paused and step % passes_per_eye == 0:
                for body in eyes:
                    body.camera.render(world)
    except KeyboardInterrupt:
        pass
    finally:
        if viewer is not None:
            viewer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Microduck X body: original duck-body + digital-twin state port")
    parser.add_argument("--env", default="bare", choices=sorted(envs.ENVIRONMENTS))
    parser.add_argument("--layout", default=None, help="arena obstacle layout")
    parser.add_argument("--seed", type=int, default=None, help="seed for scattered scenery")
    parser.add_argument("--ducks", type=int, default=1)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7801, help="robotd's port (the original protocol)")
    parser.add_argument("--state-port", type=int, default=7811, help="the viewer's port (localhost only)")
    parser.add_argument("--keyframe", default="SIT")
    parser.add_argument("--viewer", action="store_true", help="also open MuJoCo's own window (WSLg)")
    parser.add_argument("--limp", action="store_true")
    parser.add_argument("--cameras", default="")
    parser.add_argument("--frame-port", type=int, default=7901)
    parser.add_argument("--camera-fps", type=int, default=CAMERA_FPS)
    args = parser.parse_args()
    if args.ducks < 1:
        raise SystemExit("--ducks needs at least one duck")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("== warning: robotd port bound beyond localhost; the state port stays on 127.0.0.1", flush=True)

    opts = {}
    if args.layout:
        opts["layout"] = args.layout
    if args.seed is not None:
        opts["seed"] = args.seed

    world = make_world(args.env, args.ducks, opts)
    pose, trunk_z = bs.pose_table(envs.base_scene(RL, args.env), args.keyframe)
    wanted = set()
    if args.cameras.strip() == "all":
        wanted = set(range(args.ducks))
    elif args.cameras.strip():
        wanted = {ord(c.strip()) - ord("a") for c in args.cameras.split(",") if c.strip()}
    for index in range(args.ducks):
        body = bs.Body(world, index, limp=args.limp)
        body.place(pose, trunk_z, offset_y=index * bs.SPACING)
        world.bodies.append(body)
        mujoco.mj_forward(world.model, world.data)
        server = bs.Server((args.host, args.port + index), bs.Handler)
        server.body = body
        threading.Thread(target=server.serve_forever, daemon=True).start()
        if index in wanted:
            body.camera = Camera(world.model, f"{body.prefix}head_camera")
            frames = FrameServer((args.host, args.frame_port + index), FrameHandler)
            frames.camera = body.camera
            frames.fps = args.camera_fps
            threading.Thread(target=frames.serve_forever, daemon=True).start()

    twin = Twin(world, args.env, opts, pose, trunk_z)
    twin.snapshot_initial()
    state = socketserver.ThreadingTCPServer(("127.0.0.1", args.state_port), StateHandler, bind_and_activate=False)
    state.allow_reuse_address = True
    state.daemon_threads = True
    state.server_bind()
    state.server_activate()
    state.twin = twin
    threading.Thread(target=state.serve_forever, daemon=True).start()

    m = world.model
    print(f"== microduck-x body: env={args.env} {opts or ''} ducks={args.ducks} keyframe={args.keyframe} "
          f"bodies={m.nbody} geoms={m.ngeom} mujoco={mujoco.__version__}", flush=True)
    for index in range(args.ducks):
        print(f"==   duck {index}: robotd --sim {args.host}:{args.port + index}", flush=True)
    print(f"==   state port 127.0.0.1:{args.state_port}", flush=True)
    run(twin, viewer_on=args.viewer)


if __name__ == "__main__":
    main()
