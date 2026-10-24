import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { SSAOPass } from "three/examples/jsm/postprocessing/SSAOPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { getBuffer, getJson } from "../lib/api";
import { getState, live, setUi, subscribe, useStore } from "../lib/store";
import type { CameraMode, ModelHeader } from "../lib/types";
import { buildScene, mjQuat, type BuiltScene } from "./buildScene";
import { loadLook } from "./duckLook";

// The digital-twin view. The scene is built from the simulator's compiled model and every
// moving body is placed from MuJoCo's own poses each frame. Interpolation between frames is
// visual smoothing only; nothing here ever writes back to the simulation.

const FRAME_DELAY_MS = 34; // render one frame behind, so there is always a pair to interpolate

export default function Viewer({ compact = false }: { compact?: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const perf = useRef<HTMLDivElement>(null);
  const [model, setModel] = useState<ModelHeader | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const modelVersion = useStore((s) => s.modelVersion);
  const simConnected = useStore((s) => !!s.status?.sim.connected);
  const ui = useStore((s) => s.ui);
  const three = useRef<{
    renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera; controls: OrbitControls;
    sun: THREE.DirectionalLight; hemi: THREE.HemisphereLight; grid: THREE.GridHelper; axes: THREE.AxesHelper;
    heading: THREE.ArrowHelper; trail: THREE.Line; contacts: THREE.InstancedMesh; path: THREE.Line;
    occupancy: THREE.Mesh | null; composer: EffectComposer; ssao: SSAOPass; built: BuiltScene | null;
  } | null>(null);

  // ── renderer, once ───────────────────────────────────────────────────────
  useEffect(() => {
    const el = host.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    el.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#1a2027");
    const camera = new THREE.PerspectiveCamera(45, 1, 0.01, 300);
    camera.up.set(0, 0, 1);
    camera.position.set(-0.8, -0.9, 0.55);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0, 0.1);
    controls.enableDamping = true;
    controls.dampingFactor = 0.12;
    controls.maxPolarAngle = Math.PI * 0.495;
    controls.minDistance = 0.12;
    controls.maxDistance = 40;

    const hemi = new THREE.HemisphereLight(0xdfe9ff, 0x3a3328, 0.9);
    hemi.position.set(0, 0, 1);
    scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xfff4e6, 2.2);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    sun.shadow.bias = -0.0004;
    sun.shadow.normalBias = 0.01;
    const sc = sun.shadow.camera as THREE.OrthographicCamera;
    sc.left = -2.5; sc.right = 2.5; sc.top = 2.5; sc.bottom = -2.5; sc.near = 0.1; sc.far = 30;
    scene.add(sun, sun.target);

    const grid = new THREE.GridHelper(20, 80, 0x2f6b55, 0x26303a);
    grid.rotateX(Math.PI / 2);
    grid.position.z = 0.0015;
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.35;
    (grid.material as THREE.Material).depthWrite = false;
    scene.add(grid);
    const axes = new THREE.AxesHelper(0.4);
    axes.position.z = 0.002;
    scene.add(axes);
    const heading = new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(), 0.22, 0x34d399, 0.05, 0.03);
    scene.add(heading);
    const trail = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({ color: 0x34d399, transparent: true, opacity: 0.8 }));
    trail.frustumCulled = false;
    scene.add(trail);
    const contacts = new THREE.InstancedMesh(new THREE.SphereGeometry(0.008, 10, 8), new THREE.MeshBasicMaterial({ color: 0xffd166 }), 96);
    contacts.count = 0;
    contacts.frustumCulled = false;
    scene.add(contacts);
    const path = new THREE.Line(new THREE.BufferGeometry(), new THREE.LineDashedMaterial({ color: 0xffb347, dashSize: 0.06, gapSize: 0.04 }));
    path.frustumCulled = false;
    scene.add(path);

    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    const ssao = new SSAOPass(scene, camera, 256, 256);
    ssao.kernelRadius = 0.06; ssao.minDistance = 0.0005; ssao.maxDistance = 0.05;
    composer.addPass(ssao);
    composer.addPass(new OutputPass());

    three.current = { renderer, scene, camera, controls, sun, hemi, grid, axes, heading, trail, contacts, path, occupancy: null, composer, ssao, built: null };

    const resize = () => {
      const w = el.clientWidth, h = el.clientHeight;
      if (!w || !h) return;
      renderer.setSize(w, h, false);
      composer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(el);
    resize();

    // ── per-frame state ────────────────────────────────────────────────────
    const tmpA = new THREE.Vector3(), tmpB = new THREE.Vector3(), qa = new THREE.Quaternion(), qb = new THREE.Quaternion();
    const camWorld = new THREE.Matrix4(), bodyM = new THREE.Matrix4(), camLocal = new THREE.Matrix4();
    const dummy = new THREE.Object3D();
    let lastTarget = new THREE.Vector3(0, 0, 0.1);
    let transition: { from: THREE.Vector3; fromT: THREE.Vector3; to: () => [THREE.Vector3, THREE.Vector3]; t0: number } | null = null;
    let mode: CameraMode = getState().ui.cameraMode;
    let cameraSeq = getState().ui.cameraSeq;
    let fpsFrames = 0, fpsT0 = performance.now(), fps = 0;
    let raf = 0;
    let trailLen = 0;

    const trunkPos = () => {
      const b = three.current?.built;
      if (b && b.trunkBody !== null) return b.bodies.get(b.trunkBody)!.position;
      return null;
    };
    const startTransition = (to: () => [THREE.Vector3, THREE.Vector3]) => {
      transition = { from: camera.position.clone(), fromT: controls.target.clone(), to, t0: performance.now() };
    };
    const applyMode = (m: CameraMode, reset = false) => {
      mode = m;
      controls.enabled = m !== "first_person";
      const p = trunkPos() ?? new THREE.Vector3(0, 0, 0.1);
      const target = new THREE.Vector3(p.x, p.y, 0.1);
      if (m === "top") startTransition(() => { const q = trunkPos() ?? target; return [new THREE.Vector3(q.x, q.y - 0.01, 3.2), new THREE.Vector3(q.x, q.y, 0)]; });
      else if (m === "orbit" || m === "follow") {
        if (reset || camera.position.z > 2.5 || camera.position.distanceTo(controls.target) < 0.05)
          startTransition(() => { const q = trunkPos() ?? target; return [new THREE.Vector3(q.x - 0.8, q.y - 0.9, 0.55), new THREE.Vector3(q.x, q.y, 0.1)]; });
      }
      if (m !== "first_person") { camera.fov = getState().ui.fov; camera.updateProjectionMatrix(); }
      lastTarget = target.clone();
    };
    applyMode(mode);

    const unsub = subscribe(() => {
      const u = getState().ui;
      if (u.cameraMode !== mode) applyMode(u.cameraMode);
      if (u.cameraSeq !== cameraSeq) { cameraSeq = u.cameraSeq; applyMode(mode === "first_person" ? "follow" : mode, true); if (mode === "first_person") setUi({ cameraMode: "follow" }); }
      controls.rotateSpeed = u.sensitivity; controls.zoomSpeed = u.sensitivity; controls.panSpeed = u.sensitivity;
    });

    const loop = () => {
      raf = requestAnimationFrame(loop);
      const t = three.current!;
      const b = t.built;
      const now = performance.now();
      const f = live.frame, pf = live.prevFrame;
      // ── place dynamic bodies from MuJoCo poses (interpolated for display) ──
      if (b && f) {
        const ids = (b.root.userData.dynamic as number[]) ?? [];
        let alpha = 1;
        if (pf && live.frameAt > live.prevFrameAt) alpha = Math.min(1, Math.max(0, (now - FRAME_DELAY_MS - live.prevFrameAt) / (live.frameAt - live.prevFrameAt)));
        const usePrev = pf && pf.xpos.length === f.xpos.length && alpha < 1;
        for (let i = 0; i < ids.length; i++) {
          const g = b.bodies.get(ids[i]);
          if (!g || f.xpos.length < 3 * i + 3) continue;
          tmpB.set(f.xpos[3 * i], f.xpos[3 * i + 1], f.xpos[3 * i + 2]);
          qb.copy(mjQuat(f.xquat, 4 * i));
          if (usePrev) {
            tmpA.set(pf!.xpos[3 * i], pf!.xpos[3 * i + 1], pf!.xpos[3 * i + 2]);
            qa.copy(mjQuat(pf!.xquat, 4 * i));
            g.position.lerpVectors(tmpA, tmpB, alpha);
            g.quaternion.slerpQuaternions(qa, qb, alpha);
          } else { g.position.copy(tmpB); g.quaternion.copy(qb); }
        }
        // heading arrow on the trunk
        const d = f.ducks[0];
        if (d && b.trunkBody !== null) {
          const tp = b.bodies.get(b.trunkBody)!.position;
          t.heading.position.set(tp.x, tp.y, 0.01);
          t.heading.setDirection(new THREE.Vector3(Math.cos(d.yaw), Math.sin(d.yaw), 0));
        }
        // contacts
        const ov = getState().ui.overlays;
        if (ov.contacts) {
          const n = Math.min(96, f.contacts.length);
          for (let i = 0; i < n; i++) {
            const c = f.contacts[i];
            dummy.position.set(c[0], c[1], c[2]);
            const s = 0.6 + Math.min(2.5, c[5] / 3);
            dummy.scale.set(s, s, s);
            dummy.updateMatrix();
            t.contacts.setMatrixAt(i, dummy.matrix);
          }
          t.contacts.count = n;
          t.contacts.instanceMatrix.needsUpdate = true;
        } else t.contacts.count = 0;
      }
      // ── trail ──
      if (live.trail.length !== trailLen && getState().ui.overlays.trail) {
        trailLen = live.trail.length;
        t.trail.geometry.dispose();
        t.trail.geometry = new THREE.BufferGeometry().setFromPoints(live.trail.map((p) => new THREE.Vector3(p[0], p[1], p[2])));
      }
      // ── camera ──
      const tp = trunkPos();
      if (mode === "first_person" && b?.headCamera) {
        const hc = b.headCamera;
        const body = b.bodies.get(hc.body);
        if (body) {
          body.updateMatrixWorld();
          bodyM.copy(body.matrixWorld);
          camLocal.compose(hc.pos, hc.quat, new THREE.Vector3(1, 1, 1));
          camWorld.multiplyMatrices(bodyM, camLocal);
          camWorld.decompose(camera.position, camera.quaternion, tmpA);
          if (Math.abs(camera.fov - hc.fovy) > 0.01) { camera.fov = hc.fovy; camera.updateProjectionMatrix(); }
        }
      } else {
        if ((mode === "follow" || mode === "top") && tp) {
          const target = new THREE.Vector3(tp.x, tp.y, mode === "top" ? 0 : 0.1);
          const delta = target.clone().sub(lastTarget);
          camera.position.add(delta);
          controls.target.add(delta);
          lastTarget = target;
        }
        if (transition) {
          const k = Math.min(1, (now - transition.t0) / 600);
          const e = k * k * (3 - 2 * k);
          const [toP, toT] = transition.to();
          camera.position.lerpVectors(transition.from, toP, e);
          controls.target.lerpVectors(transition.fromT, toT, e);
          if (k >= 1) { transition = null; if (tp) lastTarget = new THREE.Vector3(tp.x, tp.y, mode === "top" ? 0 : 0.1); }
        }
        controls.update();
      }
      // shadows follow the robot
      if (tp) {
        const look = (b?.root.userData.look ?? {}) as { sun?: number[] };
        const sunDir = new THREE.Vector3(...((look.sun as [number, number, number]) ?? [2, -3, 6])).normalize();
        t.sun.position.set(tp.x + sunDir.x * 6, tp.y + sunDir.y * 6, sunDir.z * 6);
        t.sun.target.position.set(tp.x, tp.y, 0);
      }
      const u = getState().ui;
      if (u.ao) t.composer.render(); else t.renderer.render(t.scene, camera);
      // ── perf + liveness, written straight to the DOM (no React churn at 60 Hz) ──
      fpsFrames++;
      if (now - fpsT0 > 500) {
        fps = (fpsFrames * 1000) / (now - fpsT0); fpsFrames = 0; fpsT0 = now;
        const age = live.frame ? now - live.frameAt : Infinity;
        const st = getState();
        if (perf.current) perf.current.innerHTML =
          `<span>render ${fps.toFixed(0)} fps</span><span>twin ${st.status?.sim.rate_hz?.toFixed(0) ?? "–"} Hz</span>` +
          `<span>robotd ${st.status?.robotd.rate_hz?.toFixed(0) ?? "–"} Hz · loop ${st.status?.robotd.loop_hz?.toFixed(1) ?? "–"} Hz</span>` +
          `<span>rtf ${live.frame?.rtf?.toFixed(2) ?? "–"}×</span><span>ws ${st.latencyMs ?? "–"} ms</span>` +
          `<span>age ${isFinite(age) ? age.toFixed(0) : "∞"} ms</span><span>tris ${b ? (b.stats.triangles / 1000).toFixed(0) + "k" : "–"}</span>`;
        if (overlay.current) {
          const stale = !live.frame || age > 1000;
          overlay.current.dataset.state = !st.status?.sim.connected ? "offline" : stale ? "stale" : live.frame?.paused ? "paused" : "live";
          overlay.current.textContent = !st.status?.sim.connected ? "NOT LIVE — no simulator connection" : stale ? `STALE — last frame ${(age / 1000).toFixed(1)} s ago`
            : live.frame?.paused ? `PAUSED · sim t ${live.frame.t.toFixed(2)} s` : st.status?.mock ? `MOCK · not MuJoCo · t ${live.frame!.t.toFixed(2)} s` : `LIVE · MuJoCo ground truth · sim t ${live.frame!.t.toFixed(2)} s`;
        }
      }
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      unsub();
      ro.disconnect();
      controls.dispose();
      three.current?.built?.dispose();
      renderer.dispose();
      el.removeChild(renderer.domElement);
      three.current = null;
    };
  }, []);

  // ── (re)load the model when the environment changes ──────────────────────
  useEffect(() => {
    if (!simConnected) return;
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const header = await getJson<ModelHeader>("/api/model");
        const [mesh, tex] = await Promise.all([getBuffer("/api/model/meshes.bin"), getBuffer("/api/model/textures.bin")]);
        if (cancelled || !three.current) return;
        const t = three.current;
        if (t.built) { t.scene.remove(t.built.root); t.built.dispose(); }
        const built = buildScene(header, mesh, tex, loadLook());
        built.root.userData.dynamic = header.dynamic_bodies;
        built.root.userData.look = header.env.look;
        t.scene.add(built.root);
        t.built = built;
        const look = header.env.look ?? {};
        t.scene.background = new THREE.Color(look.sky ?? "#20303f");
        t.hemi.color.set(look.sky ?? "#dfe9ff");
        t.hemi.groundColor.set(look.ground ?? "#3a3328");
        t.scene.fog = look.fog ? new THREE.Fog(look.sky ?? "#20303f", look.fog[0], look.fog[1]) : null;
        setModel(header);
        setLoadError(null);
      } catch (e) {
        if (!cancelled) setLoadError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [modelVersion, simConnected]);

  // ── overlays and settings ─────────────────────────────────────────────────
  useEffect(() => {
    const t = three.current;
    if (!t) return;
    const o = ui.overlays;
    t.grid.visible = o.grid; t.axes.visible = o.axes; t.heading.visible = o.heading; t.trail.visible = o.trail;
    t.contacts.visible = o.contacts; t.path.visible = o.path;
    t.built?.collision.forEach((c) => (c.visible = o.collision));
    t.built?.jointAxes.forEach((c) => (c.visible = o.jointAxes));
    if (t.built) t.built.markers.visible = o.markers;
    t.renderer.shadowMap.enabled = ui.shadows;
    t.sun.castShadow = ui.shadows;
    if (ui.cameraMode !== "first_person") { t.camera.fov = ui.fov; t.camera.updateProjectionMatrix(); }
  }, [ui, model]);

  // planned path and occupancy overlay
  const plan = useStore((s) => s.plan);
  const occupancy = useStore((s) => s.occupancy);
  useEffect(() => {
    const t = three.current;
    if (!t) return;
    t.path.geometry.dispose();
    t.path.geometry = new THREE.BufferGeometry().setFromPoints((plan?.path ?? []).map(([x, y]) => new THREE.Vector3(x, y, 0.012)));
    t.path.computeLineDistances();
  }, [plan]);
  useEffect(() => {
    const t = three.current;
    if (!t) return;
    if (t.occupancy) { t.scene.remove(t.occupancy); t.occupancy.geometry.dispose(); (t.occupancy.material as THREE.MeshBasicMaterial).map?.dispose(); t.occupancy = null; }
    if (!occupancy || !ui.overlays.occupancy) return;
    const { bounds, nx, ny, blocked_bits } = occupancy;
    const bits = Uint8Array.from(atob(blocked_bits), (c) => c.charCodeAt(0));
    const data = new Uint8Array(nx * ny * 4);
    for (let i = 0; i < nx * ny; i++) {
      const on = (bits[i >> 3] >> (7 - (i & 7))) & 1;
      data.set(on ? [255, 90, 60, 120] : [0, 0, 0, 0], i * 4);
    }
    const tex = new THREE.DataTexture(data, nx, ny, THREE.RGBAFormat);
    tex.flipY = true; tex.needsUpdate = true; tex.magFilter = THREE.NearestFilter;
    const mesh = new THREE.Mesh(new THREE.PlaneGeometry(bounds[1] - bounds[0], bounds[3] - bounds[2]),
      new THREE.MeshBasicMaterial({ map: tex, transparent: true, depthWrite: false }));
    mesh.position.set((bounds[0] + bounds[1]) / 2, (bounds[2] + bounds[3]) / 2, 0.006);
    t.scene.add(mesh);
    t.occupancy = mesh;
  }, [occupancy, ui.overlays.occupancy]);

  const fullscreen = () => host.current?.parentElement?.requestFullscreen?.();
  const env = model?.env;
  return (
    <div className={`viewer ${compact ? "compact" : ""}`}>
      <div className="viewer-canvas" ref={host} />
      <div className="viewer-live" ref={overlay} data-state="offline">NOT LIVE</div>
      {model?.mock && <div className="viewer-mock">MOCK MODE (MDX_MOCK=1) — a UI test stand-in, not the Microduck. Stop this and run microduck-x/scripts/x-start.sh for the real robot.</div>}
      {!compact && env && (
        <div className="viewer-env">
          <div className="env-title">{env.title}{env.layout && env.layouts.length > 1 ? ` · ${env.layout}` : ""}</div>
          <div className="env-sub">{model?.mock ? "mock world — no physics, no robot model" : `${env.original ? "original microduck_rl scene" : "Microduck X scene on the original robot model"} · ${String(model?.source?.base_scene ?? "")}`}</div>
        </div>
      )}
      {loading && <div className="viewer-msg">Loading the compiled MuJoCo model…</div>}
      {loadError && <div className="viewer-msg error">Model load failed: {loadError}</div>}
      {!simConnected && !loading && <div className="viewer-msg">Waiting for the simulator state port. Start it with <code>scripts/x-start.sh</code>.</div>}
      {!compact && <div className="viewer-perf mono" ref={perf} />}
      {!compact && (
        <div className="viewer-tools">
          {(["follow", "orbit", "top", "first_person"] as CameraMode[]).map((m) => (
            <button key={m} className={ui.cameraMode === m ? "on" : ""} onClick={() => setUi({ cameraMode: m })} title={`camera: ${m}`}>
              {m === "first_person" ? "head cam" : m}
            </button>
          ))}
          <button onClick={() => setUi({ cameraSeq: ui.cameraSeq + 1 })} title="reset camera">reset</button>
          <button onClick={fullscreen} title="fullscreen">⤢</button>
        </div>
      )}
    </div>
  );
}
