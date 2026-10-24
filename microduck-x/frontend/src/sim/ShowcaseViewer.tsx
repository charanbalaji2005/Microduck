import { useEffect, useRef, useState, type RefObject } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { getBuffer, getJson } from "../lib/api";
import { live, useStore } from "../lib/store";
import type { ModelHeader } from "../lib/types";
import { mouthLevel, setMainDuck } from "../voice/events";
import { BeakRig } from "./beak";
import { ContactShadow, ROBOT_LAYER } from "./contactShadow";
import { buildBackdrop } from "./backdrop";
import { buildScene, mjQuat, type BuiltScene } from "./buildScene";
import type { DuckLook, PartName } from "./duckLook";
import { BackgroundDucks } from "./puppets";
import { loadPhotoScenery, sceneryById, type LoadedScenery, type Scenery } from "./scenery";

// The hangar view: the same compiled MuJoCo model and the same MuJoCo poses as the lab viewer,
// rendered for looks. Around it, viewer-only scenery — a photographed world lighting the duck
// (stage scene) or the space backdrop (hangar) — and the background ducks. Nothing here moves
// the robot; the duck is where MuJoCo says it is.

const DECK = -0.05; // the deck under the pedestal, in the hangar and stage scenes

export interface SceneryStatus { scenery: Scenery; loading: boolean; error?: string }

interface ViewState {
  renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera; controls: OrbitControls;
  composer: EffectComposer; bloomPass: UnrealBloomPass; key: THREE.SpotLight; built: BuiltScene | null;
  backdrop: ReturnType<typeof buildBackdrop> | null; customizing: boolean; bloom: boolean; framed: boolean;
  shift: number; shiftNow: number; shiftY: number; shiftYNow: number;
  quality: Quality;
  rig: { rim: THREE.DirectionalLight; fill: THREE.HemisphereLight; warm: THREE.PointLight; sun: THREE.DirectionalLight };
  pmrem: THREE.PMREMGenerator; roomEnv: THREE.Texture; photo: LoadedScenery | null; shown: string; seq: number;
  backdropKind: string; floor: { mesh: THREE.Mesh; original: THREE.Material; shadow: THREE.ShadowMaterial; space: THREE.Material } | null;
  rims: THREE.MeshStandardMaterial[]; cousins: BackgroundDucks | null;
  /** the lower beak, opened by the duck's voice (beak.ts); null when the model has none */
  beak: BeakRig | null;
  /** soft darkening where the feet meet the floor (contactShadow.ts) */
  contact: ContactShadow;
  /** what the contact shadow may land on: the scene's visible, opaque, static surfaces */
  surfaces: THREE.Object3D[];
  /** a camera move in progress (see `focusDuck`); any touch of the controls ends it */
  fly: Fly | null;
}

// Camera moves. The camera is the user's: a move only runs until they touch the controls, and it
// travels around the duck (radius, azimuth, elevation about the target) rather than through it.
interface Orbit { r: number; az: number; el: number }
interface Fly { from: Orbit; to: Orbit; fromTarget: THREE.Vector3; t0: number; dur: number }
/** Where the camera rests relative to the duck, in the duck's own frame (its face toward us). */
const REST = new THREE.Vector3(0.98, -0.3, 0.12);
const ease = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
function orbitOf(v: THREE.Vector3): Orbit {
  return { r: v.length(), az: Math.atan2(v.y, v.x), el: Math.atan2(v.z, Math.hypot(v.x, v.y)) };
}
function fromOrbit(o: Orbit, out: THREE.Vector3) {
  const flat = o.r * Math.cos(o.el);
  return out.set(flat * Math.cos(o.az), flat * Math.sin(o.az), o.r * Math.sin(o.el));
}

// Render quality, adapted to the device: a starting tier from what the device says about itself,
// then measured — if frames take too long, step down (pixel ratio, then bloom, then shadow detail);
// if there is headroom again, step back up. The models, materials and lights stay the same.
interface Quality { maxRatio: number; ratio: number; bloomOk: boolean; shadowSize: number; slow: number; fast: number; frames: number; spent: number }

function initialQuality(): Quality {
  const nav = navigator as Navigator & { deviceMemory?: number };
  const coarse = window.matchMedia("(pointer: coarse)").matches;
  const small = Math.min(window.screen.width, window.screen.height) < 768;
  const weak = (nav.hardwareConcurrency ?? 8) <= 4 || (nav.deviceMemory ?? 8) <= 4;
  const maxRatio = Math.min(window.devicePixelRatio || 1, coarse || small ? (weak ? 1.25 : 1.75) : 2);
  return { maxRatio, ratio: maxRatio, bloomOk: !(weak && coarse), shadowSize: coarse || small ? 1024 : 2048, slow: 0, fast: 0, frames: 0, spent: 0 };
}

function setFade(el: HTMLDivElement | null, v: number) { if (el) el.style.opacity = String(v); }

function clearBackdrop(st: ViewState) {
  if (st.backdrop) { st.scene.remove(st.backdrop.group); st.backdrop.dispose(); st.backdrop = null; }
  if (st.photo) { st.scene.remove(st.photo.group); st.photo.dispose(); st.photo = null; }
}

/** Looks for the stage's own geoms: a floor that can become a shadow catcher, a glass ring. */
function dressScene(st: ViewState) {
  st.floor = null;
  st.rims = [];
  st.built?.root.traverse((o) => {
    const m = o as THREE.Mesh;
    if (!m.isMesh) return;
    const mat = m.material as THREE.MeshStandardMaterial;
    if (m.name === "floor") {
      st.floor = {
        mesh: m, original: mat,
        shadow: new THREE.ShadowMaterial({ opacity: 0.45, depthWrite: false }),
        // the orbit platform: a dark glass deck that mirrors the Earth
        space: new THREE.MeshPhysicalMaterial({ color: "#0b0f16", roughness: 0.18, metalness: 0.6, clearcoat: 1, clearcoatRoughness: 0.08 }),
      };
    } else if (/^barrier_\d+$/.test(m.name)) {
      m.material = new THREE.MeshPhysicalMaterial({ color: "#dff2ff", transparent: true, opacity: 0.14, roughness: 0.05,
        metalness: 0, clearcoat: 1, depthWrite: false, side: THREE.DoubleSide });
      m.castShadow = false;
    } else if (/^barrier_cap_\d+$/.test(m.name) || m.name === "pedestal_rim") {
      mat.emissive = mat.color.clone();
      mat.emissiveIntensity = m.name === "pedestal_rim" ? 7 : 3.2; // above the bloom threshold: these glow
      st.rims.push(mat);
    }
  });
}

function accent(st: ViewState, s: Scenery) {
  for (const m of st.rims) { m.color.set(s.accent); m.emissive.set(s.accent); }
}

function applySpace(st: ViewState, layout: "hangar" | "orbit", s?: Scenery) {
  st.backdrop = buildBackdrop(layout);
  st.scene.add(st.backdrop.group);
  st.scene.background = new THREE.Color("#03050a");
  st.scene.fog = null;
  st.scene.environment = st.roomEnv;
  st.scene.environmentRotation.set(0, 0, 0);
  st.scene.environmentIntensity = 0.42;
  st.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  st.renderer.toneMappingExposure = 0.85;
  st.key.intensity = 10; st.key.castShadow = true;
  st.rig.rim.intensity = 1.6; st.rig.fill.intensity = 0.55; st.rig.warm.intensity = 0.6;
  st.rig.sun.intensity = 0; st.rig.sun.castShadow = false;
  st.bloomPass.strength = 0.5;
  if (st.floor) st.floor.mesh.material = layout === "orbit" ? st.floor.space : st.floor.original;
  if (s) accent(st, s);
}

function applyPhoto(st: ViewState, p: LoadedScenery) {
  const s = p.scenery;
  st.photo = p;
  st.scene.add(p.group);
  st.scene.background = new THREE.Color("#000000");
  st.scene.fog = null;
  st.scene.environment = p.envMap;
  st.scene.environmentRotation.copy(p.envRotation);
  st.scene.environmentIntensity = s.envIntensity;
  // the photographs are already tonemapped; Neutral keeps their colours where they were taken
  st.renderer.toneMapping = THREE.NeutralToneMapping;
  st.renderer.toneMappingExposure = s.exposure;
  st.key.intensity = 0; st.key.castShadow = false;
  st.rig.rim.intensity = 0; st.rig.warm.intensity = 0; st.rig.fill.intensity = 0.12;
  const sun = st.rig.sun;
  sun.color.set(s.night ? "#c9d4ff" : "#fff4e6");
  sun.intensity = s.sunIntensity * (0.4 + 0.6 * p.sunStrength);
  sun.castShadow = true;
  sun.position.copy(p.sunDir).multiplyScalar(6);
  st.bloomPass.strength = s.night ? 0.35 : 0.18;
  if (st.floor) {
    st.floor.shadow.opacity = s.night ? 0.3 : 0.32 + 0.25 * p.sunStrength;
    st.floor.mesh.material = st.floor.shadow;
  }
  accent(st, s);
}

const FRAME_DELAY_MS = 34;

export interface Anchors {
  bubble?: RefObject<HTMLDivElement>;
  ring?: RefObject<HTMLDivElement>;
  live?: RefObject<HTMLSpanElement>;
}

export default function ShowcaseViewer({
  look,
  customizing,
  anchors,
  bloom = true,
  shiftLeft = 0,
  shiftUp = 0,
  reducedMotion = false,
  onDuckClick,
  onCanvasClick,
  scenery = "meadow",
  cousins = 4,
  onScenery,
}: {
  look: DuckLook;
  customizing: boolean;
  anchors: Anchors;
  bloom?: boolean;
  /** pixels to move the duck left on screen (to make room for the chat panel) */
  shiftLeft?: number;
  /** pixels to move the duck up on screen (to keep it above a bottom sheet) */
  shiftUp?: number;
  /** prefers-reduced-motion: no automatic camera orbit */
  reducedMotion?: boolean;
  onDuckClick?: () => void;
  onCanvasClick?: () => void;
  /** which world surrounds the stage (scenery.ts); ignored by scenes without `backdrop: "scenery"` */
  scenery?: string;
  /** how many background ducks play on the deck */
  cousins?: number;
  onScenery?: (s: SceneryStatus) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [model, setModel] = useState<ModelHeader | null>(null);
  const modelVersion = useStore((s) => s.modelVersion);
  const onDuckClickRef = useRef(onDuckClick);
  const onCanvasClickRef = useRef(onCanvasClick);
  useEffect(() => {
    onDuckClickRef.current = onDuckClick;
    onCanvasClickRef.current = onCanvasClick;
  }, [onDuckClick, onCanvasClick]);

  const fade = useRef<HTMLDivElement>(null);
  const onSceneryRef = useRef(onScenery);
  onSceneryRef.current = onScenery;
  const t = useRef<ViewState | null>(null);
  const reducedMotionRef = useRef(reducedMotion);
  reducedMotionRef.current = reducedMotion;

  /** Swing the camera back to the duck's resting frame (double-click or double-tap, "camera reset"). */
  const focusDuck = () => {
    const st = t.current;
    const trunk = st?.built?.trunkBody != null ? st.built.bodies.get(st.built.trunkBody)!.position : null;
    if (!st || !trunk) return;
    const target = new THREE.Vector3(trunk.x, trunk.y, 0.165);
    const to = orbitOf(REST.clone().applyAxisAngle(new THREE.Vector3(0, 0, 1), live.frame?.ducks?.[0]?.yaw ?? 0));
    if (reducedMotionRef.current) {
      st.controls.target.copy(target);
      st.camera.position.copy(target).add(fromOrbit(to, new THREE.Vector3()));
      return;
    }
    const from = orbitOf(st.camera.position.clone().sub(st.controls.target));
    st.fly = { from, to, fromTarget: st.controls.target.clone(), t0: performance.now(), dur: 1400 };
  };
  const cameraSeq = useStore((s) => s.ui.cameraSeq);
  useEffect(() => { if (cameraSeq) focusDuck(); }, [cameraSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  // Swap the world around the stage: load first (the old one stays up), fade, swap, fade in.
  const showScenery = async (id: string) => {
    const st = t.current;
    if (!st || st.backdropKind !== "scenery") return;
    let s = sceneryById(id);
    if (s.kind === "hangar") s = sceneryById("orbit"); // the hangar is its own MuJoCo scene
    if (st.shown === s.id) return;
    const seq = ++st.seq;
    st.shown = s.id;
    onSceneryRef.current?.({ scenery: s, loading: true });
    let loaded: LoadedScenery | null = null;
    let error: string | undefined;
    if (s.kind === "photo") {
      try { loaded = await loadPhotoScenery(s, st.renderer, st.pmrem, DECK); }
      catch (e) { error = `scenery not downloaded (${String(e).slice(0, 80)}) — run microduck-x/scripts/x-scenery.sh`; }
    }
    if (seq !== st.seq || !t.current) { loaded?.dispose(); return; }
    const first = !st.photo && !st.backdrop;
    if (!first) { setFade(fade.current, 1); await new Promise((r) => setTimeout(r, 420)); }
    if (seq !== st.seq || !t.current) { loaded?.dispose(); return; }
    clearBackdrop(st);
    if (loaded) applyPhoto(st, loaded);
    else applySpace(st, "orbit", sceneryById("orbit"));
    setFade(fade.current, 0);
    onSceneryRef.current?.({ scenery: loaded ? s : sceneryById("orbit"), loading: false, error });
  };

  useEffect(() => {
    const el = host.current!;
    const quality = initialQuality();
    const renderer = new THREE.WebGLRenderer({ antialias: quality.maxRatio < 1.5, powerPreference: "high-performance" });
    renderer.setPixelRatio(quality.ratio);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.92;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#05070d");
    const pmrem = new THREE.PMREMGenerator(renderer);
    const envTex = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene.environment = envTex;
    scene.environmentIntensity = 0.42;

    const camera = new THREE.PerspectiveCamera(38, 1, 0.01, 4000);
    camera.up.set(0, 0, 1);
    camera.position.set(0.8, -0.2, 0.25);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.enableRotate = true;
    controls.enableZoom = true;
    controls.enablePan = true;
    controls.minDistance = 0.25;
    controls.maxDistance = 4;
    controls.maxPolarAngle = Math.PI * 0.53;
    controls.target.set(0, 0, 0.12);
    controls.autoRotateSpeed = 1.4;

    // Lighting: a soft key from above-front, a cool rim from behind, a warm low fill.
    const key = new THREE.SpotLight(0xfff4e8, 10, 8, 0.5, 0.8, 1.6);
    key.position.set(1.4, -0.9, 2.4);
    key.castShadow = true;
    key.shadow.mapSize.set(quality.shadowSize, quality.shadowSize);
    key.shadow.bias = -0.0002;
    key.shadow.normalBias = 0.004;
    key.shadow.radius = 4;
    key.shadow.camera.near = 0.5;
    key.shadow.camera.far = 6;
    scene.add(key, key.target);
    const rim = new THREE.DirectionalLight(0x8ec5ff, 1.6);
    rim.position.set(-2, 1.2, 1.4);
    scene.add(rim);
    const fill = new THREE.HemisphereLight(0xbcd4ff, 0x2a2420, 0.55);
    scene.add(fill);
    const warm = new THREE.PointLight(0xffc48a, 0.6, 3, 2);
    warm.position.set(0.9, 0.5, 0.35);
    scene.add(warm);
    // The photographed worlds' sun: aimed along the brightest point of each HDR (scenery.ts).
    const sun = new THREE.DirectionalLight(0xffffff, 0);
    sun.castShadow = false;
    sun.shadow.mapSize.set(quality.shadowSize * 2, quality.shadowSize * 2);
    Object.assign(sun.shadow.camera, { left: -2.4, right: 2.4, top: 2.4, bottom: -2.4, near: 0.1, far: 12 });
    sun.shadow.bias = -0.0003;
    sun.shadow.normalBias = 0.003;
    sun.shadow.radius = 3;
    scene.add(sun, sun.target);

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    // threshold above what lit white plastic reaches, so lamps and screens bloom and the shell does not
    const bloomPass = new UnrealBloomPass(new THREE.Vector2(512, 512), 0.5, 0.45, 2.2);
    composer.addPass(bloomPass);
    composer.addPass(new OutputPass());

    t.current = { renderer, scene, camera, controls, composer, bloomPass, key, built: null, backdrop: null,
                  customizing: false, bloom: true, framed: false, shift: 0, shiftNow: 0, shiftY: 0, shiftYNow: 0, quality,
                  rig: { rim, fill, warm, sun }, pmrem, roomEnv: envTex, photo: null, shown: "", seq: 0,
                  backdropKind: "", floor: null, rims: [], cousins: null, beak: null,
                  contact: new ContactShadow(), surfaces: [], fly: null };
    scene.add(t.current.contact.decal);
    // the user takes the camera back the moment they touch it
    controls.addEventListener("start", () => { if (t.current) t.current.fly = null; });
    (window as unknown as { __mdxShowcase?: unknown }).__mdxShowcase = t.current; // for debugging from devtools

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      composer.setSize(w, h);
      camera.aspect = w / h;
      if (camera.view && t.current) camera.setViewOffset(w, h, t.current.shiftNow, t.current.shiftYNow, w, h);
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    const a = new THREE.Vector3(), b = new THREE.Vector3(), qa = new THREE.Quaternion(), qb = new THREE.Quaternion();
    const proj = new THREE.Vector3();
    let lastTarget: THREE.Vector3 | null = null;
    let raf = 0;
    const clock = new THREE.Clock();

    const toScreen = (world: THREE.Vector3) => {
      proj.copy(world).project(camera);
      return { x: (proj.x * 0.5 + 0.5) * renderer.domElement.clientWidth, y: (-proj.y * 0.5 + 0.5) * renderer.domElement.clientHeight,
               visible: proj.z < 1 && Math.abs(proj.x) < 1.3 && Math.abs(proj.y) < 1.3 };
    };
    const place = (el2: HTMLElement | null | undefined, p: { x: number; y: number; visible: boolean }) => {
      if (!el2) return;
      const tr = `translate(${Math.round(p.x)}px, ${Math.round(p.y)}px)`;
      if (el2.style.transform !== tr) el2.style.transform = tr;
      el2.style.opacity = p.visible ? "1" : "0";
    };

    // Adapt the quality from measured frames (every ~2 s of rendering): the interval between frames
    // says whether the GPU keeps up; the time spent drawing says whether there is headroom.
    let work = 0;
    const adapt = (q: Quality, frameMs: number, drawMs: number) => {
      if (frameMs > 250) return; // a stall (tab switch, model load) is not the renderer's pace
      q.frames++; q.spent += frameMs; work += drawMs;
      if (q.frames < 120) return;
      const avg = q.spent / q.frames, draw = work / q.frames;
      q.frames = 0; q.spent = 0; work = 0;
      q.slow = avg > 26 ? q.slow + 1 : 0;              // below ~38 fps
      q.fast = avg < 18 && draw < 5 ? q.fast + 1 : 0;  // keeping vsync, with room to spare
      if (q.slow >= 2) {
        q.slow = 0;
        if (q.ratio > 1) q.ratio = Math.max(1, q.ratio - 0.25);
        else if (q.bloomOk) q.bloomOk = false;
        else if (q.shadowSize > 512) {
          q.shadowSize /= 2;
          for (const l of [key, sun]) { l.shadow.mapSize.set(q.shadowSize * (l === sun ? 2 : 1), q.shadowSize * (l === sun ? 2 : 1)); l.shadow.map?.dispose(); l.shadow.map = null; }
        }
        renderer.setPixelRatio(q.ratio);
        resize();
      } else if (q.fast >= 3 && q.ratio < q.maxRatio) {
        q.fast = 0;
        q.ratio = Math.min(q.maxRatio, q.ratio + 0.25);
        renderer.setPixelRatio(q.ratio);
        resize();
      }
    };

    const loop = () => {
      raf = requestAnimationFrame(loop);
      if (!t.current || document.hidden) return; // a hidden tab draws nothing
      try {
        const st = t.current;
      const now = performance.now();
      const dt = clock.getDelta();
      const built = st.built, f = live.frame, pf = live.prevFrame;
      if (built && f) {
        const ids = (built.root.userData.dynamic as number[]) ?? [];
        let alpha = 1;
        if (pf && live.frameAt > live.prevFrameAt) alpha = Math.min(1, Math.max(0, (now - FRAME_DELAY_MS - live.prevFrameAt) / (live.frameAt - live.prevFrameAt)));
        const usePrev = pf && pf.xpos.length === f.xpos.length && alpha < 1;
        for (let i = 0; i < ids.length; i++) {
          const g = built.bodies.get(ids[i]);
          if (!g || f.xpos.length < 3 * i + 3) continue;
          b.set(f.xpos[3 * i], f.xpos[3 * i + 1], f.xpos[3 * i + 2]);
          qb.copy(mjQuat(f.xquat, 4 * i));
          if (usePrev) {
            a.set(pf!.xpos[3 * i], pf!.xpos[3 * i + 1], pf!.xpos[3 * i + 2]);
            qa.copy(mjQuat(pf!.xquat, 4 * i));
            g.position.lerpVectors(a, b, alpha);
            g.quaternion.slerpQuaternions(qa, qb, alpha);
          } else { g.position.copy(b); g.quaternion.copy(qb); }
        }
      }
      // The beak follows what is being heard; it turns inside the head MuJoCo just placed.
      st.beak?.update(dt, mouthLevel());
      // Camera: orbit freely around the duck while it carries the orbit along with it.
      const trunk = built?.trunkBody != null ? built.bodies.get(built.trunkBody)!.position : null;
      if (trunk) {
        const target = new THREE.Vector3(trunk.x, trunk.y, 0.165);
        if (!st.framed) {
          const yaw = f?.ducks?.[0]?.yaw ?? 0;
          const off = REST.clone().applyAxisAngle(new THREE.Vector3(0, 0, 1), yaw);
          controls.target.copy(target);
          if (reducedMotionRef.current) camera.position.copy(target).add(off);
          else {
            // an establishing shot: from further out and higher, swinging round onto the duck
            const rest = orbitOf(off);
            const wide = { r: rest.r * 2.6, az: rest.az - 0.9, el: rest.el + 0.35 };
            camera.position.copy(target).add(fromOrbit(wide, new THREE.Vector3()));
            st.fly = { from: wide, to: rest, fromTarget: target.clone(), t0: now, dur: 2400 };
          }
          st.framed = true;
        } else if (st.fly) {
          const k = Math.min(1, (now - st.fly.t0) / st.fly.dur), e = ease(k);
          const { from, to } = st.fly;
          const daz = Math.atan2(Math.sin(to.az - from.az), Math.cos(to.az - from.az)); // the short way round
          const o = { r: from.r + (to.r - from.r) * e, az: from.az + daz * e, el: from.el + (to.el - from.el) * e };
          controls.target.lerpVectors(st.fly.fromTarget, target, e);
          camera.position.copy(controls.target).add(fromOrbit(o, a));
          if (k >= 1) st.fly = null;
        } else if (lastTarget) {
          const d = target.clone().sub(lastTarget).multiplyScalar(0.12);
          camera.position.add(d);
          controls.target.add(d);
        }
        lastTarget = lastTarget ? lastTarget.lerp(target, 0.12) : target.clone();
        st.contact.place(trunk, st.surfaces);
        st.key.target.position.copy(trunk);
        st.key.position.set(trunk.x + 1.4, trunk.y - 0.9, 2.4);
        if (st.photo) {
          st.rig.sun.target.position.set(0, 0, 0);
          st.rig.sun.position.copy(st.photo.sunDir).multiplyScalar(6);
        }
      }
      if (st.cousins) {
        st.cousins.observe(f);
        st.cousins.update(dt, trunk, camera);
      }
      controls.autoRotate = st.customizing && !reducedMotionRef.current;
      controls.update();
      // slide the image (not the camera) so the duck sits beside the chat panel
      // … and up, so it stays above a bottom sheet
      const moving = Math.abs(st.shift - st.shiftNow) > 0.5 || Math.abs(st.shiftY - st.shiftYNow) > 0.5;
      if (moving || ((st.shiftNow !== 0 || st.shiftYNow !== 0) && !camera.view)) {
        const k = reducedMotionRef.current ? 1 : 0.15;
        st.shiftNow += (st.shift - st.shiftNow) * k;
        st.shiftYNow += (st.shiftY - st.shiftYNow) * k;
        const w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight;
        if (Math.abs(st.shiftNow) < 0.5 && Math.abs(st.shiftYNow) < 0.5) camera.clearViewOffset();
        else camera.setViewOffset(w, h, st.shiftNow, st.shiftYNow, w, h);
      }
      st.backdrop?.tick(clock.elapsedTime);
      const drawStart = performance.now();
      if (built) st.contact.render(st.renderer, scene);
      if (st.bloom && st.quality.bloomOk) {
        try {
          st.composer.render();
        } catch {
          st.renderer.render(scene, camera);
        }
      } else {
        st.renderer.render(scene, camera);
      }
      adapt(st.quality, dt * 1000, performance.now() - drawStart);
      // anchors for the thought bubble and the buttons around the duck
      if (built && trunk) {
        const head = built.headBody != null ? built.bodies.get(built.headBody)!.getWorldPosition(new THREE.Vector3()) : trunk.clone();
        // The top of the head shell is ~0.08m above jaw_soft in world coordinates
        const headTop = head.clone().add(new THREE.Vector3(0, 0, 0.08));
        const hsTop = toScreen(headTop);
        const ts = toScreen(trunk);
        const duckH = Math.max(25, Math.hypot(hsTop.x - ts.x, hsTop.y - ts.y));

        // The comic bubble floats smoothly with its comic tail pointing to the duck head
        const bub = anchors.bubble?.current?.firstElementChild as HTMLElement | null | undefined;
        const bubH = bub ? bub.offsetHeight : 140;
        const bubW = bub ? bub.offsetWidth : 280;
        const viewW = renderer.domElement.clientWidth;
        const viewH = renderer.domElement.clientHeight;

        // Gap between duck head and bubble is proportional and stable across all zoom levels
        const desiredGap = Math.max(34, Math.min(56, duckH * 0.22));

        // Center area has open sky (header brand is left, actions are right)
        const isNearEdge = hsTop.x < 260 || hsTop.x > viewW - 240;
        const minTop = isNearEdge ? 72 : 18;

        let clampX = hsTop.x;
        let bubbleAnchorY = hsTop.y - desiredGap;

        // If duck head is zoomed very close to top of screen, place bubble to the side with clean clearance
        if (bubbleAnchorY - bubH < minTop) {
          if (hsTop.y < minTop + bubH + 30) {
            const placeLeft = hsTop.x > viewW / 2;
            const sideOffset = bubW / 2 + Math.max(28, duckH * 0.18);
            clampX = placeLeft ? hsTop.x - sideOffset : hsTop.x + sideOffset;
            bubbleAnchorY = Math.max(minTop + bubH, Math.min(viewH - 40, hsTop.y + bubH * 0.35));
          } else {
            bubbleAnchorY = minTop + bubH;
          }
        }

        // Clamp bubble horizontally within viewport margins
        clampX = Math.max(bubW / 2 + 16, Math.min(viewW - bubW / 2 - 16, clampX));

        place(anchors.bubble?.current, { x: clampX, y: bubbleAnchorY, visible: hsTop.visible });

        // Dynamically compute the comic tail angle and offset pointing to the duck head
        if (anchors.bubble?.current) {
          const dx = hsTop.x - clampX;
          const maxTailOffset = Math.max(10, bubW * 0.36);
          const clampedTailX = Math.max(-maxTailOffset, Math.min(maxTailOffset, dx));
          const flip = dx < 0 ? "scaleX(1)" : "scaleX(-1)";

          anchors.bubble.current.style.setProperty("--tail-x", `${Math.round(clampedTailX)}px`);
          anchors.bubble.current.style.setProperty("--tail-flip", flip);
        }

        if (anchors.ring?.current) {
          // Center action ring cleanly on duck's body center between trunk and head
          const duckCenter = trunk.clone().lerp(head, 0.45);
          const cs = toScreen(duckCenter);
          // Ring radius stays scaled and nicely bounded across all zoom levels
          const ringRadius = Math.max(72, Math.min(220, Math.round(duckH * 0.65 + 35)));
          const orbScale = Math.max(0.8, Math.min(1.15, duckH / 140));
          anchors.ring.current.style.setProperty("--d", `${ringRadius}px`);
          anchors.ring.current.style.setProperty("--orb-scale", `${orbScale}`);
          place(anchors.ring.current, cs);
        }
      }
      if (anchors.live?.current) {
        const age = live.frame ? now - live.frameAt : Infinity;
        const txt = !live.frame ? "live · MuJoCo" : age > 1000 ? `stale ${(age / 1000).toFixed(1)} s` : live.frame.mock ? "MOCK — not MuJoCo" : live.frame.paused ? "physics paused" : "live · MuJoCo";
        if (anchors.live.current.textContent !== txt) anchors.live.current.textContent = txt;
        anchors.live.current.dataset.state = txt.startsWith("live") ? "live" : "warn";
      }
    } catch (err) {
      console.error("Showcase loop error:", err);
    }
    };
    raf = requestAnimationFrame(loop);

    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    let downPos = { x: 0, y: 0, t: 0 };

    const getDuckMeshes = () => {
      const st = t.current;
      if (!st || !st.built) return [];
      return st.built.visualRobot.length > 0 ? st.built.visualRobot : [st.built.root];
    };

    const onPointerDown = (e: PointerEvent) => {
      downPos = { x: e.clientX, y: e.clientY, t: performance.now() };
    };

    const onPointerMove = (e: PointerEvent) => {
      const st = t.current;
      if (!st || !st.built || st.customizing) return;
      const rect = renderer.domElement.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(mouse, camera);
      const hits = raycaster.intersectObjects(getDuckMeshes(), true);
      renderer.domElement.style.cursor = hits.length > 0 ? "pointer" : "default";
    };

    const onPointerUp = (e: PointerEvent) => {
      const dist = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y);
      const elapsed = performance.now() - downPos.t;
      if (dist < 8 && elapsed < 500) {
        const st = t.current;
        if (!st || !st.built) return;
        const rect = renderer.domElement.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        mouse.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
        mouse.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(mouse, camera);
        const hits = raycaster.intersectObjects(getDuckMeshes(), true);
        if (hits.length > 0) {
          onDuckClickRef.current?.();
        } else {
          onCanvasClickRef.current?.();
        }
      }
    };

    const onDoubleClick = () => focusDuck();

    renderer.domElement.addEventListener("pointerdown", onPointerDown);
    renderer.domElement.addEventListener("dblclick", onDoubleClick);
    renderer.domElement.addEventListener("pointermove", onPointerMove);
    renderer.domElement.addEventListener("pointerup", onPointerUp);

    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      renderer.domElement.removeEventListener("dblclick", onDoubleClick);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("pointerup", onPointerUp);
      controls.dispose();
      t.current?.built?.dispose();
      t.current?.backdrop?.dispose();
      t.current?.photo?.dispose();
      t.current?.cousins?.dispose();
      t.current?.contact.dispose();
      envTex.dispose();
      pmrem.dispose();
      composer.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
      t.current = null;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let unmounted = false;
    (async () => {
      setLoading(true);
      try {
        const header = await getJson<ModelHeader>("/api/model");
        const [mesh, tex] = await Promise.all([getBuffer("/api/model/meshes.bin"), getBuffer("/api/model/textures.bin")]);
        for (let attempt = 0; attempt < 40 && !t.current && !unmounted; attempt++) {
          await new Promise((r) => setTimeout(r, 50));
        }
        if (unmounted || !t.current) return;
        const st = t.current;
        if (st.built) { st.beak?.close(); st.scene.remove(st.built.root); st.built.dispose(); }
        clearBackdrop(st);
        if (st.cousins) { st.scene.remove(st.cousins.group); st.cousins.dispose(); st.cousins = null; }
        const built = buildScene(header, mesh, tex, look);
        built.root.userData.dynamic = header.dynamic_bodies;
        built.markers.visible = false;
        st.scene.add(built.root);
        st.built = built;
        st.beak = BeakRig.find(header, built);
        for (const o of built.visualRobot) o.layers.enable(ROBOT_LAYER);
        const robot = new Set(built.visualRobot);
        st.surfaces = [];
        built.root.traverse((o) => {
          const m = o as THREE.Mesh;
          if (m.isMesh && m.visible && !robot.has(m) && !(m.material as THREE.Material).transparent) st.surfaces.push(m);
        });
        if (!st.beak && !header.mock) console.warn("no lower beak (jaw, jaw_soft) on the head body: the beak will not move");
        st.framed = false;
        setMainDuck(header.ducks[0]?.name ?? "duck-a");
        const lk = (header.env.look ?? {}) as { backdrop?: string; sky?: string; fog?: [number, number]; play_area?: [number, number] };
        st.backdropKind = lk.backdrop ?? "";
        st.shown = "";
        dressScene(st);
        if (lk.play_area && !header.mock) {
          st.cousins = new BackgroundDucks(header, built, lk.play_area, DECK);
          st.cousins.setCount(cousins);
          st.scene.add(st.cousins.group);
        }
        if (lk.backdrop === "space") {
          applySpace(st, "hangar", sceneryById("hangar"));
        } else if (lk.backdrop === "scenery") {
          void showScenery(scenery);
        } else {
          st.scene.background = new THREE.Color(lk.sky ?? "#20303f");
          st.scene.fog = lk.fog ? new THREE.Fog(lk.sky ?? "#20303f", lk.fog[0], lk.fog[1]) : null;
        }
        setModel(header);
        setError(null);
      } catch (e) {
        console.error("ShowcaseViewer error loading model:", e);
        setError(String(e));
      } finally {
        setLoading(false);
      }
    })();
    return () => { unmounted = true; };
  }, [modelVersion]); // eslint-disable-line react-hooks/exhaustive-deps-deps

  useEffect(() => { if (model) void showScenery(scenery); }, [scenery, model]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { t.current?.cousins?.setCount(cousins); }, [cousins, model]);

  // live recolouring
  useEffect(() => {
    const built = t.current?.built;
    if (!built) return;
    (Object.keys(look) as PartName[]).forEach((p) => built.parts.get(p)?.forEach((m) => m.color.set(look[p])));
  }, [look, model]);

  useEffect(() => {
    if (!t.current) return;
    t.current.customizing = customizing;
    t.current.bloom = bloom;
    t.current.shift = customizing ? 0 : shiftLeft;
    t.current.shiftY = customizing ? 0 : shiftUp;
    const c = t.current.controls;
    if (customizing) { c.minDistance = 0.3; } else { c.minDistance = 0.25; }
  }, [customizing, bloom, shiftLeft, shiftUp]);

  return (
    <div className="showcase-canvas" ref={host}>
      <div className="scenery-fade" ref={fade} />
      {loading &&<div className="showcase-msg">Loading the Microduck from MuJoCo…</div>}
      {error && <div className="showcase-msg error">Could not load the model: {error}</div>}
      {!model && !loading && !error && <div className="showcase-msg">Connecting to MuJoCo…</div>}
      {model?.mock && <div className="showcase-mock">MOCK MODE — not the Microduck. Run microduck-x/scripts/x-start.sh for the real robot.</div>}
    </div>
  );
}
