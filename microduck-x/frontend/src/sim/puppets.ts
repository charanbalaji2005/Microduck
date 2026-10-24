import * as THREE from "three";
import type { ModelHeader, SimFrame } from "../lib/types";
import { play, type Tag } from "../voice/duckVoice";
import { mjQuat, type BuiltScene } from "./buildScene";
import { ruleFor, type DuckLook, type PartName } from "./duckLook";

// The ducks playing on the deck behind the real one. They are *viewer animations*, not physics:
// no MuJoCo body, no robotd, no policy — the footer says so, and so does the duck if you ask.
// Their bodies are the real Microduck's compiled meshes, posed as the real duck stands (copied
// from a live MuJoCo frame once it is up on both feet), simplified by vertex clustering so six of
// them cost less than one full-detail duck. Each has its own identity and therefore its own voice
// (duck-b, duck-c… — the sim's naming), and plays: waddles, hops, spins, pecks, chases.

export const COUSINS = [
  { name: "Pip", id: "duck-b", look: { shell: "#ffd84a", face: "#f6f1e4", trim: "#2f6bff", beak: "#ff8a1f", eye: "#1b1d22", feet: "#2f6bff" } },
  { name: "Mochi", id: "duck-c", look: { shell: "#ff9ec7", face: "#fff3f8", trim: "#ffffff", beak: "#ff7a59", eye: "#3a1f2b", feet: "#ff6aa8" } },
  { name: "Nori", id: "duck-d", look: { shell: "#2b3a34", face: "#a8c9b8", trim: "#54e0a0", beak: "#ffb347", eye: "#54e0a0", feet: "#1d2622" } },
  { name: "Bix", id: "duck-e", look: { shell: "#5ec8ff", face: "#e9f7ff", trim: "#ffcf3d", beak: "#ffcf3d", eye: "#ffcf3d", feet: "#155a9c" } },
  { name: "Ziggy", id: "duck-f", look: { shell: "#8e6cff", face: "#efe9ff", trim: "#ff5fd2", beak: "#ffa41b", eye: "#ff5fd2", feet: "#3d2a8f" } },
  { name: "Tofu", id: "duck-g", look: { shell: "#fbfaf6", face: "#d7d4cb", trim: "#e3443a", beak: "#ffa41b", eye: "#e3443a", feet: "#e3443a" } },
] as const satisfies readonly { name: string; id: string; look: DuckLook }[];
export const MAX_COUSINS = COUSINS.length;

type Seg = "trunk" | "head" | "legL" | "legR";
const POSE_KEY = "mdx-standing-pose-v1";
const CELL = 0.0014; // m — clustering grid; ~1.4 mm detail is plenty at two metres

interface Pose { names: string[]; m: number[][] } // body name → matrix in the duck's ground frame

/** Is the real duck standing well enough to be the model for its cousins? */
function standing(f: SimFrame) {
  const d = f.ducks?.[0];
  return !!d && d.trunk[2] > 0.105 && d.gravity[2] < -0.985 && d.feet.left && d.feet.right && Math.hypot(d.linvel[0], d.linvel[1]) < 0.03;
}

function capturePose(h: ModelHeader, f: SimFrame): Pose {
  const d = f.ducks[0];
  // the duck's ground frame: under the trunk, on the floor it stands on, turned by its yaw
  const frame = new THREE.Matrix4().compose(new THREE.Vector3(d.trunk[0], d.trunk[1], 0),
    new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), d.yaw), new THREE.Vector3(1, 1, 1)).invert();
  const names: string[] = [], m: number[][] = [];
  h.dynamic_bodies.forEach((id, i) => {
    const b = h.bodies[id];
    if (b.duck !== 0) return;
    const w = new THREE.Matrix4().compose(new THREE.Vector3(f.xpos[3 * i], f.xpos[3 * i + 1], f.xpos[3 * i + 2]),
      mjQuat(f.xquat, 4 * i), new THREE.Vector3(1, 1, 1));
    names.push(b.name);
    m.push(new THREE.Matrix4().multiplyMatrices(frame, w).toArray());
  });
  return { names, m };
}

function savedPose(): Pose | null {
  try { const p = JSON.parse(localStorage.getItem(POSE_KEY) || "null"); return p?.names?.length ? p : null; } catch { return null; }
}

/** Merge geometries (already in one frame) and simplify them on a grid. */
function clusterMerge(parts: { pos: ArrayLike<number>; index: ArrayLike<number> | null; mat: THREE.Matrix4 }[]) {
  const cells = new Map<number, number>();
  const sum: number[] = [], cnt: number[] = [], tris: number[] = [];
  const v = new THREE.Vector3();
  const vid = (x: number, y: number, z: number) => {
    const key = (Math.floor(x / CELL) + 2048) + (Math.floor(y / CELL) + 2048) * 4096 + (Math.floor(z / CELL) + 2048) * 16777216;
    let i = cells.get(key);
    if (i === undefined) { i = cnt.length; cells.set(key, i); sum.push(0, 0, 0); cnt.push(0); }
    sum[3 * i] += x; sum[3 * i + 1] += y; sum[3 * i + 2] += z; cnt[i]++;
    return i;
  };
  for (const p of parts) {
    const nv = p.pos.length / 3;
    const local = new Int32Array(nv);
    for (let i = 0; i < nv; i++) {
      v.set(p.pos[3 * i], p.pos[3 * i + 1], p.pos[3 * i + 2]).applyMatrix4(p.mat);
      local[i] = vid(v.x, v.y, v.z);
    }
    const nt = p.index ? p.index.length : nv;
    for (let k = 0; k + 2 < nt; k += 3) {
      const a = local[p.index ? p.index[k] : k], b = local[p.index ? p.index[k + 1] : k + 1], c = local[p.index ? p.index[k + 2] : k + 2];
      if (a !== b && b !== c && a !== c) tris.push(a, b, c);
    }
  }
  const pos = new Float32Array(cnt.length * 3);
  for (let i = 0; i < cnt.length; i++) for (let k = 0; k < 3; k++) pos[3 * i + k] = sum[3 * i + k] / cnt[i];
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(tris), 1));
  g.computeVertexNormals();
  g.computeBoundingSphere();
  return g;
}

interface Template {
  pieces: { seg: Seg; geo: THREE.BufferGeometry; material: THREE.MeshPhysicalMaterial; part: PartName | null }[];
  pivots: Record<Seg, THREE.Vector3>;
  triangles: number;
}

async function buildTemplate(h: ModelHeader, built: BuiltScene, pose: Pose): Promise<Template | null> {
  const byName = new Map(pose.names.map((n, i) => [n, new THREE.Matrix4().fromArray(pose.m[i])]));
  const duck = h.ducks[0];
  if (!duck) return null;
  const trunk = duck.trunk_body;
  const bodies = h.bodies.filter((b) => b.duck === 0);
  // segment of each body: the trunk's child chains are two legs and a neck
  const top = (id: number): number => { let b = h.bodies[id]; while (b.parent !== trunk && b.parent > 0 && b.id !== trunk) b = h.bodies[b.parent]; return b.id; };
  const roots = bodies.filter((b) => b.parent === trunk);
  const z = (id: number) => byName.get(h.bodies[id].name)?.elements[14] ?? 0;
  const y = (id: number) => byName.get(h.bodies[id].name)?.elements[13] ?? 0;
  const neck = roots.reduce((a, b) => (z(b.id) > z(a.id) ? b : a), roots[0]);
  const segOf = (id: number): Seg => {
    if (id === trunk) return "trunk";
    const r = top(id);
    if (r === neck?.id) return "head";
    return y(r) > 0 ? "legL" : "legR";
  };
  const pivots: Record<Seg, THREE.Vector3> = { trunk: new THREE.Vector3(), head: new THREE.Vector3(), legL: new THREE.Vector3(), legR: new THREE.Vector3() };
  for (const r of roots) {
    const m = byName.get(r.name);
    if (m) pivots[segOf(r.id)].setFromMatrixPosition(m);
  }
  const visual = new Set(built.visualRobot);
  const groups = new Map<string, { seg: Seg; material: THREE.MeshPhysicalMaterial; part: PartName | null; parts: { pos: ArrayLike<number>; index: ArrayLike<number> | null; mat: THREE.Matrix4 }[] }>();
  for (const b of bodies) {
    const bm = byName.get(b.name), g = built.bodies.get(b.id);
    if (!bm || !g) continue;
    const seg = segOf(b.id);
    const toSeg = new THREE.Matrix4().makeTranslation(-pivots[seg].x, -pivots[seg].y, -pivots[seg].z).multiply(bm);
    for (const child of g.children) {
      const mesh = child as THREE.Mesh;
      if (!visual.has(mesh) || !mesh.isMesh) continue;
      const geom = h.geoms[mesh.userData.geom as number];
      const meshName = geom ? h.meshes.find((x) => x.id === geom.mesh)?.name ?? "" : "";
      const rule = ruleFor(meshName);
      if (rule.finish === "pcb" || rule.finish === "metal") continue; // inside the shell: never seen from two metres
      const material = mesh.material as THREE.MeshPhysicalMaterial;
      const key = `${seg}|${rule.part ?? `#${material.color.getHexString()}|${rule.finish}`}`;
      let grp = groups.get(key);
      if (!grp) { grp = { seg, material, part: rule.part, parts: [] }; groups.set(key, grp); }
      mesh.updateMatrix();
      const pos = mesh.geometry.getAttribute("position").array;
      const local = (mesh.userData.rest as THREE.Matrix4 | undefined) ?? mesh.matrix; // a beak shut, even if mid-word
      grp.parts.push({ pos, index: mesh.geometry.index?.array ?? null, mat: toSeg.clone().multiply(local) });
    }
  }
  const pieces: Template["pieces"] = [];
  let triangles = 0;
  for (const grp of groups.values()) {
    await new Promise((r) => setTimeout(r, 0)); // a group at a time: never one long frame
    const geo = clusterMerge(grp.parts);
    triangles += (geo.index?.count ?? 0) / 3;
    pieces.push({ seg: grp.seg, geo, material: grp.material, part: grp.part });
  }
  return { pieces, pivots, triangles };
}

type Act = "walk" | "hop" | "spin" | "peck" | "look" | "dance" | "rest" | "chase";

interface Cousin {
  idx: number; root: THREE.Group; body: THREE.Group; segs: Record<Seg, THREE.Group>; materials: THREE.Material[];
  x: number; y: number; heading: number; act: Act; since: number; until: number; target: THREE.Vector2; chase: number;
  phase: number; spin0: number; nextSound: number;
}

const WEIGHTS: [Act, number][] = [["walk", 34], ["hop", 11], ["spin", 9], ["peck", 12], ["look", 12], ["dance", 8], ["rest", 7], ["chase", 7]];
const SOUND: Partial<Record<Act, Tag>> = { hop: "chirp", spin: "chirp", peck: "peck", look: "inquire", dance: "greet", rest: "coo", chase: "chirp" };

export class BackgroundDucks {
  readonly group = new THREE.Group();
  private template: Template | null = null;
  private building = false;
  private cousins: Cousin[] = [];
  private want = 0;
  private lastSound = 0;
  private disposed = false;

  constructor(private h: ModelHeader, private built: BuiltScene, private area: [number, number], private ground: number) {
    this.group.name = "background ducks (viewer animation — not physics)";
    const p = savedPose();
    if (p) void this.build(p);
  }

  get names() { return this.cousins.map((c) => COUSINS[c.idx].name); }
  get ready() { return !!this.template; }
  get triangles() { return this.template?.triangles ?? 0; }

  setCount(n: number) { this.want = Math.max(0, Math.min(MAX_COUSINS, Math.round(n))); this.sync(); }

  /** Offer a live frame: the first time the real duck stands well, its pose becomes the cousins'. */
  observe(f: SimFrame | null) {
    if (this.template || this.building || !f || !standing(f)) return;
    const pose = capturePose(this.h, f);
    try { localStorage.setItem(POSE_KEY, JSON.stringify(pose)); } catch { /* storage blocked */ }
    void this.build(pose);
  }

  private async build(pose: Pose) {
    this.building = true;
    try {
      const t = await buildTemplate(this.h, this.built, pose);
      if (this.disposed) { t?.pieces.forEach((p) => p.geo.dispose()); return; }
      this.template = t;
      this.sync();
    } finally { this.building = false; }
  }

  private sync() {
    if (!this.template) return;
    while (this.cousins.length > this.want) this.remove(this.cousins.pop()!);
    while (this.cousins.length < this.want) this.cousins.push(this.spawn(this.cousins.length));
  }

  private spawn(idx: number): Cousin {
    const t = this.template!;
    const root = new THREE.Group(), body = new THREE.Group();
    root.add(body);
    const segs = {} as Record<Seg, THREE.Group>;
    (["trunk", "head", "legL", "legR"] as Seg[]).forEach((s) => { const g = new THREE.Group(); g.position.copy(t.pivots[s]); body.add(g); segs[s] = g; });
    const look = COUSINS[idx].look as DuckLook;
    const materials: THREE.Material[] = [];
    const matFor = new Map<THREE.Material, THREE.MeshPhysicalMaterial>();
    for (const p of t.pieces) {
      let m = matFor.get(p.material);
      if (!m) { m = p.material.clone(); if (p.part) m.color.set(look[p.part]); matFor.set(p.material, m); materials.push(m); }
      const mesh = new THREE.Mesh(p.geo, m);
      mesh.castShadow = true; mesh.receiveShadow = true;
      segs[p.seg].add(mesh);
    }
    const a = (idx / MAX_COUSINS) * Math.PI * 2 + 0.6, r = (this.area[0] + this.area[1]) / 2;
    const c: Cousin = { idx, root, body, segs, materials, x: r * Math.cos(a), y: r * Math.sin(a), heading: a + Math.PI / 2,
      act: "rest", since: 0, until: 0, target: new THREE.Vector2(), chase: -1, phase: Math.random() * 6, spin0: 0, nextSound: performance.now() / 1000 + 4 + Math.random() * 8 };
    root.position.set(c.x, c.y, this.ground);
    this.group.add(root);
    return c;
  }

  private remove(c: Cousin) {
    this.group.remove(c.root);
    c.materials.forEach((m) => m.dispose());
  }

  private pickTarget(c: Cousin) {
    const [r0, r1] = this.area;
    const a = Math.atan2(c.y, c.x) + (Math.random() - 0.5) * 1.6;
    const r = r0 + 0.08 + Math.random() * (r1 - r0 - 0.16);
    c.target.set(r * Math.cos(a), r * Math.sin(a));
  }

  private choose(c: Cousin, now: number) {
    let roll = Math.random() * WEIGHTS.reduce((s, [, w]) => s + w, 0);
    let act: Act = "walk";
    for (const [a, w] of WEIGHTS) { if ((roll -= w) < 0) { act = a; break; } }
    if (act === "chase" && this.cousins.length < 2) act = "walk";
    c.act = act;
    c.since = now;
    c.spin0 = c.heading;
    const dur: Record<Act, [number, number]> = { walk: [3, 7], hop: [1.4, 2.4], spin: [1.3, 1.6], peck: [1.8, 3], look: [2, 3.5], dance: [2.5, 4], rest: [3, 6], chase: [3, 5] };
    const [lo, hi] = dur[act];
    c.until = now + lo + Math.random() * (hi - lo);
    if (act === "walk") this.pickTarget(c);
    if (act === "chase") { const others = this.cousins.filter((o) => o !== c); c.chase = others[Math.floor(Math.random() * others.length)].idx; }
  }

  /** Advance the play; `duck` is the real duck's trunk (they keep out of its way). */
  update(dt: number, duck: THREE.Vector3 | null, camera: THREE.Camera) {
    if (!this.cousins.length) return;
    const now = performance.now() / 1000;
    dt = Math.min(dt, 0.05);
    const [r0, r1] = this.area;
    for (const c of this.cousins) {
      if (now > c.until) this.choose(c, now);
      let speed = 0, wantHeading = c.heading, gait = 0;
      if (c.act === "walk" || c.act === "chase") {
        const tgt = c.act === "chase" ? this.cousins.find((o) => o.idx === c.chase) : null;
        const tx = tgt ? tgt.x : c.target.x, ty = tgt ? tgt.y : c.target.y;
        const dx = tx - c.x, dy = ty - c.y, dist = Math.hypot(dx, dy);
        if (dist < (tgt ? 0.3 : 0.06)) { if (!tgt) c.until = now; }
        else { wantHeading = Math.atan2(dy, dx); speed = c.act === "chase" ? 0.16 : 0.1; gait = c.act === "chase" ? 3.4 : 2.6; }
      }
      // keep apart, keep off the pedestal and inside the ring, keep away from the real duck
      let px = 0, py = 0;
      for (const o of this.cousins) {
        if (o === c) continue;
        const dx = c.x - o.x, dy = c.y - o.y, d = Math.hypot(dx, dy);
        if (d < 0.32 && d > 1e-4) { px += (dx / d) * (0.32 - d); py += (dy / d) * (0.32 - d); }
      }
      if (duck) { const dx = c.x - duck.x, dy = c.y - duck.y, d = Math.hypot(dx, dy); if (d < 0.45 && d > 1e-4) { px += (dx / d) * (0.45 - d) * 2; py += (dy / d) * (0.45 - d) * 2; } }
      const r = Math.hypot(c.x, c.y);
      if (r < r0) { px += (c.x / r) * (r0 - r) * 3; py += (c.y / r) * (r0 - r) * 3; }
      if (r > r1) { px -= (c.x / r) * (r - r1) * 3; py -= (c.y / r) * (r - r1) * 3; }
      if (speed > 0) {
        let dh = Math.atan2(Math.sin(wantHeading - c.heading), Math.cos(wantHeading - c.heading));
        dh = Math.max(-2.4 * dt, Math.min(2.4 * dt, dh));
        c.heading += dh;
        const fwd = speed * (1 - 0.6 * Math.min(1, Math.abs(dh) / (2.4 * dt)));  // slow down to turn
        c.x += Math.cos(c.heading) * fwd * dt; c.y += Math.sin(c.heading) * fwd * dt;
      }
      c.x += px * dt * 2; c.y += py * dt * 2;

      // body language
      const t = now - c.since;
      const legs = c.segs, hd = legs.head;
      let roll = 0, pitch = 0, lift = 0, swing = 0, headPitch = 0, headYaw = 0, yawOffset = 0;
      if (gait > 0) {
        c.phase += dt * gait * Math.PI * 2;
        swing = 0.34 * Math.sin(c.phase);
        roll = 0.08 * Math.sin(c.phase);
        lift = 0.004 * Math.abs(Math.cos(c.phase));
      }
      switch (c.act) {
        case "hop": { const p = (t * 2.6) % 1; lift = 0.045 * Math.sin(Math.PI * p) ** 0.8; pitch = -0.12 * Math.sin(Math.PI * p); swing = 0.25 * Math.sin(Math.PI * p); break; }
        case "spin": yawOffset = Math.min(1, t / (c.until - c.since)) * Math.PI * 2; lift = 0.01 * Math.abs(Math.sin(t * 9)); break;
        case "peck": headPitch = 0.55 * Math.max(0, Math.sin(t * 7)) ** 2; pitch = 0.1 * Math.max(0, Math.sin(t * 7)); break;
        case "look": {
          const cam = camera.position, want = Math.atan2(cam.y - c.y, cam.x - c.x) - c.heading;
          headYaw = Math.max(-0.7, Math.min(0.7, Math.atan2(Math.sin(want), Math.cos(want)))) * Math.min(1, t * 2);
          headPitch = -0.15 + 0.08 * Math.sin(t * 3); break;
        }
        case "dance": roll = 0.16 * Math.sin(t * 8); headPitch = 0.2 * Math.sin(t * 8 + 1); lift = 0.008 * Math.abs(Math.sin(t * 8)); yawOffset = 0.35 * Math.sin(t * 2); break;
        case "rest": headPitch = 0.05 * Math.sin(t * 1.2); headYaw = 0.25 * Math.sin(t * 0.5); break;
        default: headPitch = 0.04 * Math.sin(c.phase * 2);
      }
      if (c.act === "spin" && now > c.until - 0.05) c.heading = c.spin0 + Math.PI * 2;
      c.root.position.set(c.x, c.y, this.ground + lift);
      c.root.rotation.set(0, 0, c.heading + yawOffset);
      c.body.rotation.set(roll, pitch, 0);
      legs.legL.rotation.set(0, swing, 0);
      legs.legR.rotation.set(0, -swing, 0);
      hd.rotation.set(0, headPitch, headYaw);

      // a voice of its own, now and then, from where it stands
      const tag = SOUND[c.act];
      if (tag && now > c.nextSound && now - this.lastSound > 1.4) {
        c.nextSound = now + 6 + Math.random() * 10;
        this.lastSound = now;
        const p = new THREE.Vector3(c.x, c.y, 0.1).project(camera);
        const dist = camera.position.distanceTo(c.root.position);
        if (p.z < 1) play(COUSINS[c.idx].id, tag, undefined, { pan: p.x * 0.8, gain: Math.min(0.55, 0.45 / Math.max(0.5, dist)) });
      }
    }
  }

  dispose() {
    this.disposed = true;
    this.cousins.forEach((c) => this.remove(c));
    this.cousins = [];
    this.template?.pieces.forEach((p) => p.geo.dispose());
    this.template = null;
  }
}
