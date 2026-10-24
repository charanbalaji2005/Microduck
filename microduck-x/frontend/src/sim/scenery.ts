import * as THREE from "three";
import { GroundedSkybox } from "three/examples/jsm/objects/GroundedSkybox.js";
import { RGBELoader } from "three/examples/jsm/loaders/RGBELoader.js";

// The worlds around the stage. Each photographed one is a real place, captured as a 360° HDR
// panorama (Poly Haven, CC0): an 8K tonemapped JPG is projected onto the ground and sky around
// the pedestal, and the 1K HDR of the same shot lights the duck — so the light on the duck is the
// light of that place, and its shadow falls away from that photo's sun. All of it is drawn by the
// viewer; the physics is the stage scene's deck, pedestal and glass ring (sim/environments.py).
// Files come from microduck-x/scripts/x-scenery.sh into public/scenery/.

export type SceneryKind = "photo" | "space" | "hangar";

export interface Scenery {
  id: string;
  title: string;
  place: string;
  kind: SceneryKind;
  /** public/scenery/<file>.jpg + <file>_1k.hdr */
  file?: string;
  thumb: string;
  credit: string;
  /** Height of the photo's camera above its ground, m: sets the scale of the projected ground. */
  height: number;
  /** Which column of the panorama (0..1) sits behind the duck in the first view. */
  viewU: number;
  exposure: number;
  envIntensity: number;
  sunIntensity: number;
  /** UI and pedestal-rim colour. */
  accent: string;
  night?: boolean;
}

const PH = "Poly Haven · CC0";

export const SCENERIES: Scenery[] = [
  { id: "meadow", title: "Meadow sunrise", place: "Spruit, South Africa", kind: "photo", file: "spruit_sunrise", thumb: "thumbs/spruit_sunrise.png",
    credit: PH, height: 1.6, viewU: 0.55, exposure: 0.95, envIntensity: 0.9, sunIntensity: 3.2, accent: "#ffc45c" },
  { id: "beach", title: "Beach sunrise", place: "Umhlanga, South Africa", kind: "photo", file: "umhlanga_sunrise", thumb: "thumbs/umhlanga_sunrise.png",
    credit: PH, height: 1.6, viewU: 0.6, exposure: 1.0, envIntensity: 1.0, sunIntensity: 2.6, accent: "#ff9e6b" },
  { id: "snow", title: "Snowy park", place: "Winter park", kind: "photo", file: "snowy_park_01", thumb: "thumbs/snowy_park_01.png",
    credit: PH, height: 1.7, viewU: 0.5, exposure: 0.9, envIntensity: 1.0, sunIntensity: 1.0, accent: "#9fd4ff" },
  { id: "desert", title: "Desert reserve", place: "Goegap, Northern Cape", kind: "photo", file: "goegap", thumb: "thumbs/goegap.png",
    credit: PH, height: 1.6, viewU: 0.68, exposure: 0.85, envIntensity: 0.85, sunIntensity: 3.6, accent: "#f2b46b" },
  { id: "hills", title: "Cloudy hills", place: "Kloofendal, Johannesburg", kind: "photo", file: "kloofendal_48d_partly_cloudy", thumb: "thumbs/kloofendal_48d_partly_cloudy.png",
    credit: PH, height: 1.7, viewU: 0.5, exposure: 0.95, envIntensity: 0.95, sunIntensity: 3.0, accent: "#8fd27a" },
  { id: "city", title: "Shanghai nights", place: "The Bund, Shanghai", kind: "photo", file: "shanghai_bund", thumb: "thumbs/shanghai_bund.png",
    credit: PH, height: 1.7, viewU: 0.585, exposure: 1.1, envIntensity: 1.3, sunIntensity: 0.7, accent: "#ff5fd2", night: true },
  { id: "venice", title: "Venice sunset", place: "Venice, Italy", kind: "photo", file: "venice_sunset", thumb: "thumbs/venice_sunset.png",
    credit: PH, height: 1.6, viewU: 0.61, exposure: 1.0, envIntensity: 1.0, sunIntensity: 2.4, accent: "#ffb36b" },
  { id: "stars", title: "Milky Way savanna", place: "Dikhololo, South Africa", kind: "photo", file: "dikhololo_night", thumb: "thumbs/dikhololo_night.png",
    credit: PH, height: 1.6, viewU: 0.5, exposure: 1.25, envIntensity: 1.6, sunIntensity: 0.35, accent: "#9aa8ff", night: true },
  { id: "orbit", title: "Low Earth orbit", place: "400 km up", kind: "space", thumb: "thumbs/orbit.jpg",
    credit: "NASA Blue & Black Marble, LRO · ESO/S. Brunier (CC BY 4.0)", height: 1, viewU: 0, exposure: 0.92, envIntensity: 0.42,
    sunIntensity: 0, accent: "#6fb7ff" },
  { id: "hangar", title: "Orbital hangar", place: "the hangar scene", kind: "hangar", thumb: "thumbs/orbit.jpg",
    credit: "MuJoCo scene · NASA, ESO backdrop", height: 1, viewU: 0, exposure: 0.92, envIntensity: 0.42, sunIntensity: 0, accent: "#7cc4ff" },
];

export const sceneryById = (id: string) => SCENERIES.find((s) => s.id === id) ?? SCENERIES[0];
/** The worlds the stage can show (the hangar is its own MuJoCo scene). */
export const STAGE_SCENERIES = SCENERIES.filter((s) => s.kind !== "hangar");
export const sceneryUrl = (path: string) => `/scenery/${path}`;

/** Direction from the first camera position to the duck (ShowcaseViewer frames from +x, -y). */
const VIEW_AZIMUTH = Math.atan2(0.3, -0.98);

export interface LoadedScenery {
  scenery: Scenery;
  group: THREE.Group;
  envMap: THREE.Texture;
  /** World direction to the brightest point of the HDR (the sun, or the brightest lamp). */
  sunDir: THREE.Vector3;
  /** How dominant that point is: ~0 overcast/night, 1 a clear sun. */
  sunStrength: number;
  envRotation: THREE.Euler;
  dispose: () => void;
}

async function loadImageTexture(url: string, maxSize: number): Promise<THREE.Texture> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  const blob = await res.blob();
  let tex: THREE.Texture;
  if ("createImageBitmap" in window) {
    // decoded off the main thread; scaled down for GPUs that cannot take 8192 px
    const probe = await createImageBitmap(blob);
    const scale = Math.min(1, maxSize / Math.max(probe.width, probe.height));
    const bmp = scale < 1
      ? await createImageBitmap(blob, { resizeWidth: Math.round(probe.width * scale), resizeHeight: Math.round(probe.height * scale), resizeQuality: "high", imageOrientation: "flipY" })
      : await createImageBitmap(blob, { imageOrientation: "flipY" });
    probe.close();
    tex = new THREE.Texture(bmp);
    tex.flipY = false;
  } else {
    tex = await new THREE.TextureLoader().loadAsync(url);
  }
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.mapping = THREE.EquirectangularReflectionMapping;
  tex.needsUpdate = true;
  return tex;
}

/** The brightest 8×8-pixel block of an equirect HDR, as a Y-up direction (three's convention). */
function findSun(hdr: THREE.DataTexture): { dir: THREE.Vector3; strength: number } {
  const { data, width: W, height: H } = hdr.image as unknown as { data: Float32Array; width: number; height: number };
  const B = 8;
  let best = 0, bu = 0.5, bv = 0.75, total = 0, n = 0;
  for (let by = 0; by < H / 2; by += B) { // upper hemisphere only
    for (let bx = 0; bx < W; bx += B) {
      let s = 0;
      for (let y = by; y < by + B; y++) for (let x = bx; x < bx + B; x++) {
        const i = (y * W + x) * 4;
        s += 0.2126 * data[i] + 0.7152 * data[i + 1] + 0.0722 * data[i + 2];
      }
      total += s; n++;
      if (s > best) { best = s; bu = (bx + B / 2) / W; bv = 1 - (by + B / 2) / H; }
    }
  }
  const phi = (bv - 0.5) * Math.PI; // elevation
  const dir = new THREE.Vector3(-Math.cos(2 * Math.PI * bu) * Math.cos(phi), Math.sin(phi), -Math.sin(2 * Math.PI * bu) * Math.cos(phi));
  const ratio = best / Math.max(1e-6, total / n);
  return { dir, strength: Math.min(1, Math.max(0, Math.log10(ratio) / 3)) };
}

/**
 * Load a photographed world: the ground-projected panorama in a Z-up group, its environment
 * map for lighting, and the sun direction in the same (world, Z-up) frame.
 * `ground` is the height of the stage's deck.
 */
export async function loadPhotoScenery(s: Scenery, renderer: THREE.WebGLRenderer, pmrem: THREE.PMREMGenerator,
                                       ground: number): Promise<LoadedScenery> {
  const maxSize = Math.min(8192, renderer.capabilities.maxTextureSize);
  const hdrLoader = new RGBELoader().setDataType(THREE.FloatType);
  const [photo, hdr] = await Promise.all([
    loadImageTexture(sceneryUrl(`${s.file}.jpg`), maxSize),
    hdrLoader.loadAsync(sceneryUrl(`${s.file}_1k.hdr`)),
  ]);
  photo.anisotropy = renderer.capabilities.getMaxAnisotropy();
  photo.generateMipmaps = true;
  photo.minFilter = THREE.LinearMipmapLinearFilter;
  hdr.mapping = THREE.EquirectangularReflectionMapping;
  const sun = findSun(hdr);
  const envMap = pmrem.fromEquirectangular(hdr).texture;
  hdr.dispose();

  // Panorama column u lies at world azimuth π − 2πu once the Y-up sphere is turned Z-up; turn
  // it further so `viewU` is behind the duck.
  const yaw = VIEW_AZIMUTH - (Math.PI - 2 * Math.PI * s.viewU);
  const group = new THREE.Group();
  group.name = `scenery:${s.id} (viewer only — not physics)`;
  group.rotation.z = yaw;
  const sky = new GroundedSkybox(photo, s.height, 90, 160);
  sky.rotation.x = Math.PI / 2; // Y-up sphere → Z-up world
  sky.position.z = ground + s.height - 0.002;
  sky.renderOrder = -10;
  (sky.material as THREE.MeshBasicMaterial).fog = false;
  group.add(sky);
  renderer.initTexture(photo); // upload now, during the fade, not on the first visible frame

  // env lookup = (Rz(yaw)·Rx(90°))⁻¹ · world; three builds the inverse from the negated XYZ Euler
  const envRotation = new THREE.Euler(Math.PI / 2, 0, yaw, "XYZ");
  const sunDir = new THREE.Vector3(sun.dir.x, -sun.dir.z, sun.dir.y).applyAxisAngle(new THREE.Vector3(0, 0, 1), yaw);
  return {
    scenery: s, group, envMap, sunDir, sunStrength: sun.strength, envRotation,
    dispose: () => { photo.dispose(); envMap.dispose(); sky.geometry.dispose(); (sky.material as THREE.Material).dispose(); },
  };
}
