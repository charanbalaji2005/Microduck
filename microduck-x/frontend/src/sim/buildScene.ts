import * as THREE from "three";
import type { ModelGeom, ModelHeader } from "../lib/types";
import { DEFAULT_LOOK, robotMaterial, ruleFor, type DuckLook, type PartName } from "./duckLook";

// Builds a three.js scene from the *compiled* MuJoCo model the simulator serves: every geom,
// with MuJoCo's own sizes, local frames, materials, textures and (recentred) mesh data. Nothing
// here is authored by hand, so the picture is the physics model.

export const PLANE = 0, SPHERE = 2, CAPSULE = 3, ELLIPSOID = 4, CYLINDER = 5, BOX = 6, MESH = 7;
const DEFAULT_RGBA = [0.5, 0.5, 0.5, 1];

export function mjQuat(q: number[] | Float32Array, i = 0) {
  return new THREE.Quaternion(q[i + 1], q[i + 2], q[i + 3], q[i]); // MuJoCo w,x,y,z → three x,y,z,w
}

export interface BuiltScene {
  root: THREE.Group;
  bodies: Map<number, THREE.Group>;
  collision: THREE.Object3D[];
  visualRobot: THREE.Object3D[];
  jointAxes: THREE.Object3D[];
  markers: THREE.Group;
  trunkBody: number | null;
  headCamera: { body: number; pos: THREE.Vector3; quat: THREE.Quaternion; fovy: number } | null;
  stats: { geoms: number; triangles: number; meshes: number };
  /** The robot's materials, by paintable part, for live recolouring. */
  parts: Map<PartName, THREE.MeshPhysicalMaterial[]>;
  headBody: number | null;
  dispose: () => void;
}

function isDefault(rgba: number[]) {
  return rgba.every((v, i) => Math.abs(v - DEFAULT_RGBA[i]) < 1e-4);
}

function labelSprite(text: string, color = "#e7fff3") {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 64;
  const g = c.getContext("2d")!;
  g.fillStyle = "rgba(12,16,18,0.78)";
  g.beginPath(); g.roundRect(4, 8, 248, 48, 10); g.fill();
  g.font = "600 26px Inter, system-ui, sans-serif"; g.fillStyle = color; g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(text, 128, 33);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true, sizeAttenuation: false }));
  s.scale.set(0.1, 0.025, 1); // screen-space size: readable at any distance
  s.renderOrder = 10;
  return s;
}

export function buildScene(h: ModelHeader, meshBuf: ArrayBuffer, texBuf: ArrayBuffer, look: DuckLook = DEFAULT_LOOK): BuiltScene {
  const parts = new Map<PartName, THREE.MeshPhysicalMaterial[]>();
  const root = new THREE.Group();
  root.name = `mujoco:${h.env.name}`;
  const disposables: { dispose: () => void }[] = [];
  const bodies = new Map<number, THREE.Group>();
  const collision: THREE.Object3D[] = [];
  const visualRobot: THREE.Object3D[] = [];
  const jointAxes: THREE.Object3D[] = [];
  let triangles = 0;

  for (const b of h.bodies) {
    const g = new THREE.Group();
    g.name = b.name;
    g.position.set(b.pos[0], b.pos[1], b.pos[2]);
    g.quaternion.copy(mjQuat(b.quat));
    g.matrixAutoUpdate = b.dynamic; // static scenery: compute once
    g.updateMatrix();
    bodies.set(b.id, g);
    root.add(g);
  }

  // Meshes exactly as compiled (one geometry per mesh, shared by every geom that uses it).
  const meshGeo = new Map<number, THREE.BufferGeometry>();
  const smoothGeo = new Map<number, THREE.BufferGeometry>();
  const geoFor = (id: number, smooth = false) => {
    if (smooth) {
      let sg = smoothGeo.get(id);
      if (sg) return sg;
      const m = h.meshes.find((x) => x.id === id)!;
      const indexed = new THREE.BufferGeometry();
      indexed.setAttribute("position", new THREE.BufferAttribute(new Float32Array(meshBuf, m.vert_offset, m.vert_count * 3).slice(), 3));
      indexed.setIndex(new THREE.BufferAttribute(new Uint32Array(meshBuf, m.face_offset, m.face_count * 3).slice(), 1));
      // Fast, smooth vertex normals: rounded shells read as moulded plastic without freezing the main thread.
      indexed.computeVertexNormals();
      sg = indexed;
      sg.computeBoundingSphere();
      smoothGeo.set(id, sg);
      disposables.push(sg);
      return sg;
    }
    let geo = meshGeo.get(id);
    if (geo) return geo;
    const m = h.meshes.find((x) => x.id === id)!;
    const verts = new Float32Array(meshBuf, m.vert_offset, m.vert_count * 3);
    const faces = new Uint32Array(meshBuf, m.face_offset, m.face_count * 3);
    const indexed = new THREE.BufferGeometry();
    indexed.setAttribute("position", new THREE.BufferAttribute(verts.slice(), 3));
    indexed.setIndex(new THREE.BufferAttribute(faces.slice(), 1));
    geo = indexed.toNonIndexed(); // flat shading suits CAD parts
    indexed.dispose();
    geo.computeVertexNormals();
    geo.computeBoundingSphere();
    meshGeo.set(id, geo);
    disposables.push(geo);
    return geo;
  };

  const textures = new Map<number, THREE.DataTexture>();
  for (const t of h.textures) {
    const data = new Uint8Array(texBuf, t.offset, t.length).slice();
    const tex = new THREE.DataTexture(data, t.width, t.height, THREE.RGBAFormat);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
    tex.magFilter = THREE.LinearFilter;
    tex.minFilter = THREE.LinearMipmapLinearFilter;
    tex.generateMipmaps = true;
    tex.anisotropy = 8;
    tex.needsUpdate = true;
    textures.set(t.id, tex);
    disposables.push(tex);
  }

  const [bx0, bx1, by0, by1] = h.env.bounds;
  const planeSpan = Math.max(bx1 - bx0, by1 - by0) * 3 + 10;

  const materialFor = (g: ModelGeom, extent: [number, number]) => {
    const mat = g.material >= 0 ? h.materials[g.material] : null;
    const rgba = mat && isDefault(g.rgba) ? mat.rgba : g.rgba;
    const color = new THREE.Color().setRGB(rgba[0], rgba[1], rgba[2], THREE.SRGBColorSpace);
    const isRobot = h.bodies[g.body].duck !== null;
    const m = new THREE.MeshStandardMaterial({
      color, transparent: rgba[3] < 0.999, opacity: rgba[3],
      roughness: isRobot ? 0.5 : mat ? Math.max(0.25, 0.9 - (mat.reflectance ?? 0) * 3 - (mat.specular ?? 0) * 0.3) : 0.85,
      metalness: isRobot ? 0.05 : 0.0,
      depthWrite: rgba[3] >= 0.999,
    });
    if (mat && mat.texture >= 0 && textures.has(mat.texture)) {
      const tex = textures.get(mat.texture)!.clone();
      tex.needsUpdate = true;
      const [rx, ry] = mat.texrepeat;
      if (mat.texuniform) tex.repeat.set(rx * extent[0], ry * extent[1]);
      else tex.repeat.set(rx, ry);
      m.map = tex;
      disposables.push(tex);
    }
    disposables.push(m);
    return m;
  };

  for (const g of h.geoms) {
    if (g.group > 3 || g.rgba[3] === 0) continue;
    const s = g.size;
    let geo: THREE.BufferGeometry | null = null;
    let extent: [number, number] = [1, 1];
    switch (g.type) {
      case PLANE: {
        const w = s[0] > 0 ? 2 * s[0] : planeSpan, d = s[1] > 0 ? 2 * s[1] : planeSpan;
        geo = new THREE.PlaneGeometry(w, d); extent = [w, d]; break;
      }
      case SPHERE: geo = new THREE.SphereGeometry(s[0], 24, 16); extent = [2 * s[0], 2 * s[0]]; break;
      case ELLIPSOID: geo = new THREE.SphereGeometry(1, 24, 16); geo.scale(s[0], s[1], s[2]); extent = [2 * s[0], 2 * s[1]]; break;
      case CAPSULE: geo = new THREE.CapsuleGeometry(s[0], 2 * s[1], 8, 16); geo.rotateX(Math.PI / 2); extent = [2 * s[0], 2 * s[1]]; break;
      case CYLINDER: geo = new THREE.CylinderGeometry(s[0], s[0], 2 * s[1], 32); geo.rotateX(Math.PI / 2); extent = [2 * s[0], 2 * s[0]]; break;
      case BOX: geo = new THREE.BoxGeometry(2 * s[0], 2 * s[1], 2 * s[2]); extent = [2 * Math.max(s[0], s[1]), 2 * Math.max(s[1], s[2])]; break;
      case MESH: if (g.mesh >= 0) geo = geoFor(g.mesh, g.group !== 3 && h.bodies[g.body].duck !== null); break;
    }
    if (!geo) continue;
    if (g.type !== MESH) disposables.push(geo);
    const isRobot = h.bodies[g.body].duck !== null;
    let obj: THREE.Mesh;
    if (g.group === 3) {
      // Collision geometry: hidden unless the overlay is on. Drawn as an orange wire cage.
      const mat = new THREE.MeshBasicMaterial({ color: 0xff8a3d, wireframe: true, transparent: true, opacity: 0.35, depthWrite: false });
      disposables.push(mat);
      obj = new THREE.Mesh(geo, mat);
      obj.visible = false;
      collision.push(obj);
    } else {
      if (isRobot && g.type === MESH) {
        const meshName = h.meshes.find((x) => x.id === g.mesh)?.name ?? "";
        const rule = ruleFor(meshName);
        const mat = robotMaterial(rule, look);
        disposables.push(mat);
        if (rule.part) parts.set(rule.part, [...(parts.get(rule.part) ?? []), mat]);
        obj = new THREE.Mesh(geo, mat);
      } else {
        const mat = materialFor(g, extent);
        // Lights in the scenery glow: the hangar's pedestal rim, screens and deck lamps.
        if (/^(pedestal_rim|console_\d+_screen|decal_deck_light_\d+)$/.test(g.name)) {
          mat.emissive = mat.color.clone();
          mat.emissiveIntensity = g.name === "pedestal_rim" ? 3.2 : 2.2;
        }
        obj = new THREE.Mesh(geo, mat);
      }
      obj.castShadow = g.type !== PLANE && g.rgba[3] > 0.5 && !g.name.startsWith("decal_");
      obj.receiveShadow = true;
      if (isRobot) visualRobot.push(obj);
      if (!g.collides && g.name.startsWith("decal_")) obj.renderOrder = 1;
    }
    obj.name = g.name || `geom${g.id}`;
    obj.position.set(g.pos[0], g.pos[1], g.pos[2]);
    obj.quaternion.copy(mjQuat(g.quat));
    obj.userData = { geom: g.id, collides: g.collides, group: g.group };
    bodies.get(g.body)!.add(obj);
    triangles += (geo.index ? geo.index.count : geo.attributes.position.count) / 3;
  }

  // Joint axes (hinges) of the robot, for the joint overlay.
  for (const j of h.joints) {
    if (j.type !== 3 || h.bodies[j.body].duck === null) continue;
    const dir = new THREE.Vector3(...j.axis).normalize();
    const arrow = new THREE.ArrowHelper(dir, new THREE.Vector3(...j.pos), 0.05, 0x5ef2b4, 0.012, 0.008);
    arrow.visible = false;
    bodies.get(j.body)!.add(arrow);
    jointAxes.push(arrow);
  }

  // Named markers from the environment description (start, goals, stations).
  const markers = new THREE.Group();
  for (const m of h.env.markers) {
    const sprite = labelSprite(m.name.replace(/_/g, " "), m.kind === "start" ? "#7ef0c0" : m.kind === "goal" ? "#ffc27a" : "#cfe3ff");
    sprite.position.set(m.x, m.y, 0.34);
    const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.003, 0.003, 0.3, 6), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.35 }));
    pole.rotateX(Math.PI / 2); pole.position.set(m.x, m.y, 0.15);
    markers.add(sprite, pole);
    disposables.push(sprite.material.map!, sprite.material, pole.geometry, pole.material as THREE.Material);
  }
  root.add(markers);

  const cam = h.cameras.find((c) => c.name.endsWith("head_camera"));
  const duck = h.ducks[0];
  return {
    root, bodies, collision, visualRobot, jointAxes, markers,
    trunkBody: duck ? duck.trunk_body : null,
    headCamera: cam ? { body: cam.body, pos: new THREE.Vector3(...cam.pos), quat: mjQuat(cam.quat), fovy: cam.fovy } : null,
    stats: { geoms: h.geoms.length, triangles: Math.round(triangles), meshes: h.meshes.length },
    parts,
    headBody: (() => { const b = h.bodies.find((x) => x.name === `${h.ducks[0]?.prefix ?? ""}jaw_soft`); return b ? b.id : null; })(),
    dispose: () => disposables.forEach((d) => d.dispose()),
  };
}
