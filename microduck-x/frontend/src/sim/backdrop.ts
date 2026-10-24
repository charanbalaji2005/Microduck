import * as THREE from "three";
import { sceneryUrl } from "./scenery";

// The view out into space: the Milky Way, the Earth, the Moon. This is a *backdrop* — far away,
// drawn only by the viewer, and not part of the MuJoCo world (the hangar railing and the stage's
// glass ring keep the duck from ever reaching the edge). Real imagery when x-scenery.sh has
// fetched it — NASA Blue Marble, Black Marble and cloud cover, the LRO Moon (public domain) and
// ESO's Milky Way panorama (ESO/S. Brunier, CC BY 4.0) — and a procedural stand-in otherwise.

function rng(seed: number) {
  return () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
}

function valueNoise(seed: number) {
  const r = rng(seed);
  const p = new Float32Array(256 * 256).map(() => r());
  const at = (x: number, y: number) => p[((y & 255) << 8) | (x & 255)];
  return (x: number, y: number) => {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
    const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    const a = at(xi, yi), b = at(xi + 1, yi), c = at(xi, yi + 1), d = at(xi + 1, yi + 1);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
  };
}

function fbm(n: (x: number, y: number) => number, x: number, y: number, oct = 5) {
  let s = 0, a = 0.5, f = 1;
  for (let i = 0; i < oct; i++) { s += a * n(x * f, y * f); a *= 0.5; f *= 2.03; }
  return s;
}

/** The fallback planet, when the NASA maps are not downloaded. */
function proceduralEarth() {
  const W = 512, H = 256;
  const land = document.createElement("canvas"); land.width = W; land.height = H;
  const cloud = document.createElement("canvas"); cloud.width = W; cloud.height = H;
  const gl = land.getContext("2d")!, gc = cloud.getContext("2d")!;
  const li = gl.createImageData(W, H), ci = gc.createImageData(W, H);
  const n1 = valueNoise(7), n2 = valueNoise(19), n3 = valueNoise(41);
  for (let y = 0; y < H; y++) {
    const lat = Math.abs(y / H - 0.5) * 2;
    for (let x = 0; x < W; x++) {
      const a = (x / W) * Math.PI * 2, cx = Math.cos(a) * 3 + 10, cy = Math.sin(a) * 3 + 10;
      const h = fbm(n1, cx + y / 90, cy + y / 70, 6) - lat * 0.18;
      const i = (y * W + x) * 4;
      if (h > 0.5) {
        const t = Math.min(1, (h - 0.5) * 5), dry = fbm(n3, cx * 2, cy * 2 + y / 40, 3);
        li.data[i] = 90 + 120 * t * dry + 40; li.data[i + 1] = 150 + 60 * dry; li.data[i + 2] = 70 + 30 * t;
      } else {
        const d = Math.max(0, (0.5 - h) * 2);
        li.data[i] = 25 + 30 * (1 - d); li.data[i + 1] = 90 + 70 * (1 - d); li.data[i + 2] = 170 + 60 * (1 - d);
      }
      if (lat > 0.86) li.data[i] = li.data[i + 1] = li.data[i + 2] = 235;
      li.data[i + 3] = 255;
      const c = fbm(n2, cx * 1.6 + y / 50, cy * 1.6, 5);
      ci.data[i] = ci.data[i + 1] = ci.data[i + 2] = 255 * Math.max(0, Math.min(1, (c - 0.52) * 3.2)); ci.data[i + 3] = 255;
    }
  }
  gl.putImageData(li, 0, 0); gc.putImageData(ci, 0, 0);
  const t1 = new THREE.CanvasTexture(land), t2 = new THREE.CanvasTexture(cloud);
  t1.colorSpace = THREE.SRGBColorSpace;
  return { day: t1, clouds: t2 };
}

function load(path: string, srgb = true) {
  return new THREE.TextureLoader().loadAsync(sceneryUrl(path)).then((t) => {
    if (srgb) t.colorSpace = THREE.SRGBColorSpace;
    t.anisotropy = 8;
    return t;
  });
}

const EARTH_VS = `varying vec2 vUv; varying vec3 vN; varying vec3 vView;
void main(){ vUv = uv; vec4 wp = modelMatrix * vec4(position, 1.0); vN = normalize(mat3(modelMatrix) * normal);
  vView = normalize(cameraPosition - wp.xyz); gl_Position = projectionMatrix * viewMatrix * wp; }`;
// Day side lit by the sun, city lights on the night side, a blue limb where the air is.
const EARTH_FS = `uniform sampler2D day; uniform sampler2D night; uniform vec3 sunDir; uniform float hasNight;
varying vec2 vUv; varying vec3 vN; varying vec3 vView;
void main(){ float ndl = dot(normalize(vN), sunDir);
  vec3 d = texture2D(day, vUv).rgb * (max(ndl, 0.0) * 1.15 + 0.015);
  vec3 n = texture2D(night, vUv).rgb * smoothstep(0.08, -0.25, ndl) * 1.6 * hasNight;
  float rim = pow(1.0 - max(dot(normalize(vN), vView), 0.0), 3.0);
  vec3 air = vec3(0.32, 0.58, 1.0) * rim * (0.25 + 0.9 * smoothstep(-0.3, 0.4, ndl));
  gl_FragColor = vec4(d + n + air, 1.0);
  #include <colorspace_fragment>
}`;

export type BackdropLayout = "hangar" | "orbit";

export function buildBackdrop(layout: BackdropLayout = "hangar"): { group: THREE.Group; dispose: () => void; tick: (t: number) => void } {
  const group = new THREE.Group();
  group.name = "backdrop (viewer only — not physics)";
  const disposables: { dispose: () => void }[] = [];
  const sunDir = new THREE.Vector3(400, -500, 600).normalize();

  // stars: points until the Milky Way panorama arrives, then a faint layer over it
  const r = rng(3);
  const N = 4000, pos = new Float32Array(N * 3), col = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) {
    const u = r() * 2 - 1, th = r() * Math.PI * 2, s = Math.sqrt(1 - u * u), R = 1800;
    pos.set([R * s * Math.cos(th), R * s * Math.sin(th), R * u], i * 3);
    const b = 0.5 + r() * 0.5, tint = r();
    col.set([b * (tint > 0.8 ? 0.8 : 1), b * 0.95, b * (tint < 0.2 ? 0.8 : 1)], i * 3);
  }
  const sg = new THREE.BufferGeometry();
  sg.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  sg.setAttribute("color", new THREE.BufferAttribute(col, 3));
  const sm = new THREE.PointsMaterial({ size: 1.4, sizeAttenuation: false, vertexColors: true, fog: false, transparent: true });
  group.add(new THREE.Points(sg, sm));
  disposables.push(sg, sm);

  const skyMat = new THREE.MeshBasicMaterial({ side: THREE.BackSide, fog: false, depthWrite: false, color: 0xd8d8d8, transparent: true, opacity: 0 });
  const sky = new THREE.Mesh(new THREE.SphereGeometry(1950, 96, 48), skyMat);
  sky.rotation.set(Math.PI / 2 + 0.5, 0.3, 0.9); // tilt the galactic band across the view
  sky.renderOrder = -10;
  group.add(sky);
  disposables.push(sky.geometry, skyMat);

  // the Earth: beside the hangar's railing, or a vast limb below the orbit platform
  const R = layout === "orbit" ? 1350 : 420;
  const earthPos = layout === "orbit" ? new THREE.Vector3(-1440, 440, -1330) : new THREE.Vector3(-900, 260, -330);
  const { day: pDay, clouds: pClouds } = proceduralEarth();
  const blank = new THREE.DataTexture(new Uint8Array([0, 0, 0, 255]), 1, 1);
  blank.needsUpdate = true;
  const earthMat = new THREE.ShaderMaterial({
    uniforms: { day: { value: pDay }, night: { value: blank }, sunDir: { value: sunDir }, hasNight: { value: 0 } },
    vertexShader: EARTH_VS, fragmentShader: EARTH_FS, fog: false,
  });
  const earth = new THREE.Mesh(new THREE.SphereGeometry(R, 160, 96), earthMat);
  earth.position.copy(earthPos);
  earth.rotation.x = Math.PI / 2;
  const cloudMat = new THREE.MeshStandardMaterial({ color: 0xffffff, alphaMap: pClouds, transparent: true, depthWrite: false, roughness: 1, fog: false });
  const clouds = new THREE.Mesh(new THREE.SphereGeometry(R * 1.008, 160, 96), cloudMat);
  clouds.position.copy(earthPos);
  clouds.rotation.x = Math.PI / 2;
  const atmo = new THREE.Mesh(new THREE.SphereGeometry(R * 1.045, 96, 64), new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.BackSide, blending: THREE.AdditiveBlending, fog: false,
    uniforms: { c: { value: new THREE.Color("#4f9dff") }, sunDir: { value: sunDir } },
    vertexShader: "varying vec3 vN; varying vec3 vV; varying vec3 vW; void main(){ vec4 mv = modelViewMatrix*vec4(position,1.); vN = normalize(normalMatrix*normal); vW = normalize(mat3(modelMatrix)*normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }",
    fragmentShader: "uniform vec3 c; uniform vec3 sunDir; varying vec3 vN; varying vec3 vV; varying vec3 vW; void main(){ float f = pow(1.0-abs(dot(vN,vV)), 4.0); float lit = smoothstep(-0.4, 0.3, dot(-vW, sunDir)); gl_FragColor = vec4(c*f*1.4*lit, f*lit); }",
  }));
  atmo.position.copy(earthPos);

  const moonMat = new THREE.MeshStandardMaterial({ color: "#c9c9c4", roughness: 1, fog: false });
  const moon = new THREE.Mesh(new THREE.SphereGeometry(26, 64, 48), moonMat);
  moon.position.set(-760, -420, 260);
  moon.rotation.x = Math.PI / 2;
  // their own light, so the hangar's lights do not decide how a planet looks
  const sun = new THREE.DirectionalLight(0xfff3e0, 2.4);
  sun.position.copy(sunDir).multiplyScalar(800);
  sun.target = earth;
  group.add(earth, clouds, atmo, moon, sun);
  disposables.push(pDay, pClouds, blank, earth.geometry, earthMat, clouds.geometry, cloudMat, atmo.geometry,
    atmo.material as THREE.Material, moon.geometry, moonMat);

  let alive = true;
  const swap = (path: string, apply: (t: THREE.Texture) => void, srgb = true) =>
    load(path, srgb).then((t) => { if (alive) { apply(t); disposables.push(t); } else t.dispose(); }).catch(() => { /* not downloaded: keep the stand-in */ });
  swap("space/earth_day.jpg", (t) => { earthMat.uniforms.day.value = t; });
  swap("space/earth_night.jpg", (t) => { earthMat.uniforms.night.value = t; earthMat.uniforms.hasNight.value = 1; });
  swap("space/earth_clouds.jpg", (t) => { cloudMat.alphaMap = t; cloudMat.needsUpdate = true; }, false);
  swap("space/moon.jpg", (t) => { moonMat.map = t; moonMat.color.set("#ffffff"); moonMat.needsUpdate = true; });
  swap("space/milky_way.jpg", (t) => { skyMat.map = t; skyMat.opacity = 1; skyMat.needsUpdate = true; sm.opacity = 0.35; });

  return {
    group,
    dispose: () => { alive = false; disposables.forEach((d) => d.dispose()); },
    tick: (t) => { clouds.rotation.y = t * 0.0035; earth.rotation.y = t * 0.002; moon.rotation.y = t * 0.001; },
  };
}
