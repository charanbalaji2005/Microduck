import * as THREE from "three";
import type { ModelHeader } from "../lib/types";
import type { BuiltScene } from "./buildScene";

// The beak. The real Microduck opens its lower jaw with a servo (`mouth`, joint 9 on the wire:
// −5° closed … +30° open, duck-control/src/model.rs), but the MuJoCo walking model has no mouth
// joint — the jaw is a fixed geom of the head body (`jaw_soft`), so no MuJoCo pose can open it,
// and the duck spoke with its beak shut. This rig is the hinge the CAD has: the `jaw` part and its
// soft pad `jaw_soft` turn about the mouth servo's horn. The upper beak (`soft_mouth_top`) and the
// rest of the head stay exactly where MuJoCo puts them, and the rig lives inside the head body, so
// head, legs and beak never drive the same thing: each moves on its own and they compose.

const HEAD_BODY = "jaw_soft";
const LOWER_BEAK = /^(jaw|jaw_soft)$/;
// Centre of the servo horn's screw ring on `jaw.stl`, in the head body's frame, read from the
// compiled model (stage scene, MuJoCo 3.10): 3.5 mm along +x (up, when the duck stands) and
// 17.5 mm along −z (forward). The shaft is the body's y axis; a positive turn swings the tip down.
export const HINGE = new THREE.Vector3(0.0035, 0, -0.0175);
const AXIS = new THREE.Vector3(0, 1, 0);
/** Fully open, radians above closed — the servo's travel. */
export const MAX_OPEN = (30 * Math.PI) / 180;
const ATTACK_S = 0.035; // opens about as fast as the servo can
const RELEASE_S = 0.08;
const GATE = 0.06; // quieter than this is a gap between syllables: shut

export class BeakRig {
  /** How far open, radians. */
  angle = 0;
  private turn = new THREE.Quaternion();
  private v = new THREE.Vector3();

  private constructor(private parts: { mesh: THREE.Mesh; p: THREE.Vector3; q: THREE.Quaternion }[]) {}

  /** The lower beak of duck 0 in a built scene, or null when the model has none (mock mode). */
  static find(h: ModelHeader, built: BuiltScene): BeakRig | null {
    const prefix = h.ducks[0]?.prefix ?? "";
    const body = h.bodies.find((b) => b.name === `${prefix}${HEAD_BODY}`);
    const g = body ? built.bodies.get(body.id) : undefined;
    if (!g) return null;
    const meshName = (o: THREE.Object3D) => {
      const geom = (o as THREE.Mesh).isMesh ? h.geoms.find((x) => x.id === o.userData.geom) : undefined;
      return geom ? h.meshes.find((x) => x.id === geom.mesh)?.name ?? "" : "";
    };
    const meshes = g.children.filter((o) => LOWER_BEAK.test(meshName(o))) as THREE.Mesh[];
    if (!meshes.length) return null;
    return new BeakRig(meshes.map((mesh) => {
      mesh.updateMatrix();
      mesh.userData.rest = mesh.matrix.clone(); // the closed pose, for anyone copying the geometry
      return { mesh, p: mesh.position.clone(), q: mesh.quaternion.clone() };
    }));
  }

  /** Follow the voice for `dt` seconds; `level` is how loud the duck is being heard, 0..1. */
  update(dt: number, level: number) {
    const want = level > GATE ? MAX_OPEN * Math.min(1, level ** 0.7) : 0;
    const tau = want > this.angle ? ATTACK_S : RELEASE_S;
    this.angle += (want - this.angle) * (1 - Math.exp(-Math.max(0, dt) / tau));
    if (want === 0 && this.angle < 1e-3) this.angle = 0;
    this.apply();
  }

  /** Shut at once (a model swap, a teardown). */
  close() {
    this.angle = 0;
    this.apply();
  }

  private apply() {
    this.turn.setFromAxisAngle(AXIS, this.angle);
    for (const { mesh, p, q } of this.parts) {
      mesh.quaternion.copy(this.turn).multiply(q);
      mesh.position.copy(this.v.copy(p).sub(HINGE).applyQuaternion(this.turn).add(HINGE));
    }
  }
}
