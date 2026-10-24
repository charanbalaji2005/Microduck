"""Planning around the scene's real collision geometry (a Microduck X extension).

The original robot software has no navigation: robotd follows a velocity twist and that is all.
This module plans a 2-D path on an occupancy grid rasterised from the *compiled MuJoCo model* —
every collidable, non-robot geom whose vertical extent overlaps the duck's height band — and the
executor walks it with a heading controller on top of `robot.move`.

What this is not: proof that a legged robot can execute the path. A collision-free line on a
grid says nothing about footholds, slopes or the policy's tracking error. Execution is therefore
closed-loop and monitored (progress watchdog, fall gate, timeouts), and a failure is reported as a
failure, never retried silently.

Pose source: simulator ground truth (stated in every result). robotd's own contact odometry is
reported beside it so the gap between the two is visible.
"""
from __future__ import annotations

import heapq
import math
import struct
from dataclasses import dataclass

import numpy as np

PLANE, HFIELD, SPHERE, CAPSULE, ELLIPSOID, CYLINDER, BOX, MESH = 0, 1, 2, 3, 4, 5, 6, 7

ROBOT_RADIUS = 0.12  # m — half the duck's stance plus margin
BAND = (0.015, 0.35)  # m — what a 25 cm duck can bump into; a 1 cm plate is walkable
RESOLUTION = 0.05


def quat_to_mat(q) -> np.ndarray:
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def mesh_extents(header: dict, mesh_blob: bytes) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    out = {}
    for m in header.get("meshes", []):
        n = m["vert_count"]
        verts = np.frombuffer(mesh_blob, dtype="<f4", count=n * 3, offset=m["vert_offset"]).reshape(n, 3)
        out[m["id"]] = (verts.min(axis=0), verts.max(axis=0))
    return out


@dataclass
class Obstacle:
    name: str
    polygon: np.ndarray  # (k, 2) convex hull in world XY
    zmin: float
    zmax: float


def _hull(points: np.ndarray) -> np.ndarray:
    pts = sorted(set(map(tuple, np.round(points, 5))))
    if len(pts) <= 2:
        return np.array(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1])


def obstacles_from_model(header: dict, frame: dict | None, mesh_blob: bytes | None = None) -> list[Obstacle]:
    bodies = {b["id"]: b for b in header["bodies"]}
    pose = {b["id"]: (np.array(b["pos"]), np.array(b["quat"])) for b in header["bodies"]}
    if frame:
        ids = header["dynamic_bodies"]
        xp = np.array(frame["xpos"]).reshape(-1, 3)
        xq = np.array(frame["xquat"]).reshape(-1, 4)
        for i, b in enumerate(ids):
            pose[b] = (xp[i], xq[i])
    extents = mesh_extents(header, mesh_blob) if mesh_blob else {}
    out = []
    for g in header["geoms"]:
        body = bodies[g["body"]]
        if body["duck"] is not None or not g["collides"] or g["type"] in (PLANE, HFIELD):
            continue
        s = g["size"]
        t = g["type"]
        center = np.zeros(3)
        if t == BOX or t == ELLIPSOID:
            half = np.array(s[:3])
        elif t == SPHERE:
            half = np.array([s[0]] * 3)
        elif t == CYLINDER:
            half = np.array([s[0], s[0], s[1]])
        elif t == CAPSULE:
            half = np.array([s[0], s[0], s[1] + s[0]])
        elif t == MESH and g["mesh"] in extents:
            lo, hi = extents[g["mesh"]]
            half, center = (hi - lo) / 2, (hi + lo) / 2
        else:
            continue
        bpos, bquat = pose[g["body"]]
        R_b = quat_to_mat(bquat)
        R_g = quat_to_mat(g["quat"])
        R = R_b @ R_g
        origin = bpos + R_b @ np.array(g["pos"])
        corners = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]) * half + center
        world = origin + corners @ R.T
        zmin, zmax = world[:, 2].min(), world[:, 2].max()
        if zmax < BAND[0] or zmin > BAND[1]:
            continue
        out.append(Obstacle(g["name"] or f"geom{g['id']}", _hull(world[:, :2]), float(zmin), float(zmax)))
    return out


class Grid:
    def __init__(self, bounds, obstacles: list[Obstacle], resolution=RESOLUTION, radius=ROBOT_RADIUS):
        self.x0, self.x1, self.y0, self.y1 = bounds
        self.res = resolution
        self.nx = int(math.ceil((self.x1 - self.x0) / resolution))
        self.ny = int(math.ceil((self.y1 - self.y0) / resolution))
        occ = np.zeros((self.nx, self.ny), dtype=bool)
        xs = self.x0 + (np.arange(self.nx) + 0.5) * resolution
        ys = self.y0 + (np.arange(self.ny) + 0.5) * resolution
        for ob in obstacles:
            poly = ob.polygon
            if len(poly) < 3:
                continue
            lo, hi = poly.min(axis=0), poly.max(axis=0)
            ix = np.where((xs >= lo[0] - resolution) & (xs <= hi[0] + resolution))[0]
            iy = np.where((ys >= lo[1] - resolution) & (ys <= hi[1] + resolution))[0]
            if not len(ix) or not len(iy):
                continue
            px, py = np.meshgrid(xs[ix], ys[iy], indexing="ij")
            inside = np.ones(px.shape, dtype=bool)
            for k in range(len(poly)):
                a, b = poly[k], poly[(k + 1) % len(poly)]
                inside &= (b[0] - a[0]) * (py - a[1]) - (b[1] - a[1]) * (px - a[0]) >= -1e-9
            occ[np.ix_(ix, iy)] |= inside
        self.occupied = occ
        r = int(math.ceil(radius / resolution))
        inflated = occ.copy()
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                if dx * dx + dy * dy <= r * r and (dx or dy):
                    inflated |= np.roll(np.roll(occ, dx, axis=0), dy, axis=1) & _shift_mask(occ.shape, dx, dy)
        # The bounds are walls too.
        inflated[:r, :] = inflated[-r:, :] = True
        inflated[:, :r] = inflated[:, -r:] = True
        self.blocked = inflated

    def cell(self, x: float, y: float) -> tuple[int, int]:
        return (min(self.nx - 1, max(0, int((x - self.x0) / self.res))),
                min(self.ny - 1, max(0, int((y - self.y0) / self.res))))

    def center(self, c: tuple[int, int]) -> tuple[float, float]:
        return self.x0 + (c[0] + 0.5) * self.res, self.y0 + (c[1] + 0.5) * self.res

    def free(self, c) -> bool:
        return 0 <= c[0] < self.nx and 0 <= c[1] < self.ny and not self.blocked[c]

    def nearest_free(self, c, max_cells: int) -> tuple[int, int] | None:
        if self.free(c):
            return c
        best = None
        for r in range(1, max_cells + 1):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    n = (c[0] + dx, c[1] + dy)
                    if self.free(n):
                        d = dx * dx + dy * dy
                        if best is None or d < best[0]:
                            best = (d, n)
            if best:
                return best[1]
        return None

    def line_free(self, a, b) -> bool:
        (x0, y0), (x1, y1) = a, b
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(steps + 1):
            c = (round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps))
            if not self.free(c):
                return False
        return True

    def to_json(self) -> dict:
        packed = np.packbits(self.blocked.T[::-1].ravel()).tobytes()  # row 0 = max y, for drawing
        import base64

        return dict(bounds=[self.x0, self.x1, self.y0, self.y1], resolution=self.res, nx=self.nx, ny=self.ny,
                    blocked_bits=base64.b64encode(packed).decode())


def _shift_mask(shape, dx, dy) -> np.ndarray:
    """np.roll wraps around; this masks out the wrapped band."""
    m = np.ones(shape, dtype=bool)
    if dx > 0:
        m[:dx, :] = False
    elif dx < 0:
        m[dx:, :] = False
    if dy > 0:
        m[:, :dy] = False
    elif dy < 0:
        m[:, dy:] = False
    return m


class PlanError(RuntimeError):
    pass


def plan(grid: Grid, start: tuple[float, float], goal: tuple[float, float]) -> list[tuple[float, float]]:
    s = grid.cell(*start)
    g = grid.cell(*goal)
    if not grid.free(s):
        # The duck is standing where the inflated map says it cannot: near a wall. Let it leave.
        s2 = grid.nearest_free(s, 6)
        if s2 is None:
            raise PlanError("the robot is boxed in by obstacles on the map")
        s = s2
    if not grid.free(g):
        g2 = grid.nearest_free(g, 6)
        if g2 is None:
            raise PlanError("the goal is inside an obstacle (with the robot's radius added)")
        g = g2
    openq = [(0.0, s)]
    came: dict = {s: None}
    cost = {s: 0.0}
    hx = lambda c: math.hypot(c[0] - g[0], c[1] - g[1])  # noqa: E731
    while openq:
        _, cur = heapq.heappop(openq)
        if cur == g:
            break
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
            n = (cur[0] + dx, cur[1] + dy)
            if not grid.free(n):
                continue
            if dx and dy and not (grid.free((cur[0] + dx, cur[1])) and grid.free((cur[0], cur[1] + dy))):
                continue
            c = cost[cur] + (1.4142 if dx and dy else 1.0)
            if c < cost.get(n, math.inf):
                cost[n] = c
                came[n] = cur
                heapq.heappush(openq, (c + hx(n), n))
    if g not in came:
        raise PlanError("no collision-free path to the goal on the map")
    cells = []
    c = g
    while c is not None:
        cells.append(c)
        c = came[c]
    cells.reverse()
    # Shortcut: keep only the cells where line-of-sight breaks.
    keep = [cells[0]]
    i = 0
    while i < len(cells) - 1:
        j = len(cells) - 1
        while j > i + 1 and not grid.line_free(cells[i], cells[j]):
            j -= 1
        keep.append(cells[j])
        i = j
    points = [grid.center(c) for c in keep[1:]]
    points[-1] = goal if grid.free(grid.cell(*goal)) else points[-1]
    return points


def wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def heading_command(pose, target, speed, final: bool) -> tuple[float, float, float]:
    """(vx, vyaw, distance) for a unicycle heading controller. Turn in place when far off-heading."""
    x, y, yaw = pose
    dx, dy = target[0] - x, target[1] - y
    dist = math.hypot(dx, dy)
    err = wrap(math.atan2(dy, dx) - yaw)
    vyaw = max(-0.8, min(0.8, 1.6 * err))
    if abs(err) > 0.45:
        return 0.0, vyaw if abs(vyaw) > 0.3 else math.copysign(0.3, vyaw), dist
    vx = min(speed, 0.6 * dist + 0.06) if final else speed
    return vx * max(0.3, math.cos(err)), vyaw, dist
