import * as THREE from "three";
import { FullScreenQuad } from "three/examples/jsm/postprocessing/Pass.js";
import { HorizontalBlurShader } from "three/examples/jsm/shaders/HorizontalBlurShader.js";
import { VerticalBlurShader } from "three/examples/jsm/shaders/VerticalBlurShader.js";

// The soft dark patch where a foot meets the floor — what grounds an object in a photograph, and
// what a shadow map at studio resolution smears away. The technique of three.js's
// `webgl_shadow_contact` example: an orthographic camera at floor level looks *up* through a thin
// slab, sees only the robot (one render layer), writes "how close to the floor" as darkness,
// and two blurs soften it onto a decal under the duck. A lifted foot fades out by itself, so a
// stepping gait reads as stepping. Display only; nothing here touches the model.

/** The render layer the robot's meshes are also on, so this camera sees nothing else. */
export const ROBOT_LAYER = 1;

export class ContactShadow {
  readonly decal: THREE.Mesh;
  private rt: THREE.WebGLRenderTarget;
  private rtBlur: THREE.WebGLRenderTarget;
  private cam: THREE.OrthographicCamera;
  private depth: THREE.MeshDepthMaterial;
  private hBlur: THREE.ShaderMaterial;
  private vBlur: THREE.ShaderMaterial;
  private quad: FullScreenQuad;
  private ground = 0;
  private sinceGround = Infinity;
  private ray = new THREE.Raycaster();

  /** `size`: side of the square under the duck (m); `height`: how far above the floor counts (m). */
  constructor(size = 0.6, height = 0.1, private res = 256, private blur = 5, opacity = 0.75) {
    const opts = { type: THREE.HalfFloatType };
    this.rt = new THREE.WebGLRenderTarget(res, res, opts);
    this.rt.texture.generateMipmaps = false;
    this.rtBlur = new THREE.WebGLRenderTarget(res, res, opts);
    this.rtBlur.texture.generateMipmaps = false;

    const geo = new THREE.PlaneGeometry(size, size);
    // seen from below the image is mirrored in x (see the camera below); un-mirror it in the UVs
    const uv = geo.getAttribute("uv") as THREE.BufferAttribute;
    for (let i = 0; i < uv.count; i++) uv.setX(i, 1 - uv.getX(i));
    // Blurred coverage is mostly faint away from the soles; 1 − e^(−k·a) lifts the soft fringe into a
    // visible falloff while the patch under the feet saturates instead of going opaque black.
    this.decal = new THREE.Mesh(geo, new THREE.ShaderMaterial({
      uniforms: { map: { value: this.rt.texture }, opacity: { value: opacity } },
      vertexShader: "varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
      fragmentShader: "uniform sampler2D map; uniform float opacity; varying vec2 vUv;\n"
        + "void main() { float a = max(texture2D(map, vUv).a, 0.0); gl_FragColor = vec4(0.0, 0.0, 0.0, opacity * (1.0 - exp(-3.0 * a))); }",
      transparent: true, depthWrite: false,
    }));
    this.decal.renderOrder = 1; // after the floor, before transparent glass
    this.decal.name = "contact_shadow";
    this.decal.raycast = () => undefined; // not something to click

    // Looking up +z (a turn of π about y), so image up stays world +y and image right is world −x.
    this.cam = new THREE.OrthographicCamera(-size / 2, size / 2, size / 2, -size / 2, 0, height);
    this.cam.rotation.set(0, Math.PI, 0);
    this.cam.layers.set(ROBOT_LAYER);

    this.depth = new THREE.MeshDepthMaterial();
    this.depth.depthTest = false;
    this.depth.depthWrite = false;
    this.depth.onBeforeCompile = (shader) => {
      shader.fragmentShader = shader.fragmentShader.replace(
        "gl_FragColor = vec4( vec3( 1.0 - fragCoordZ ), opacity );",
        "gl_FragColor = vec4( vec3( 0.0 ), ( 1.0 - fragCoordZ ) * 1.6 );");
    };
    this.hBlur = new THREE.ShaderMaterial(HorizontalBlurShader);
    this.vBlur = new THREE.ShaderMaterial(VerticalBlurShader);
    this.hBlur.depthTest = this.vBlur.depthTest = false;
    this.quad = new FullScreenQuad();
  }

  /** Put the patch under `at` (the duck's trunk), on whatever static surface is below it. */
  place(at: THREE.Vector3, surfaces: THREE.Object3D[]) {
    // the floor under the duck changes rarely (the pedestal's edge): look again every ~quarter second
    if (++this.sinceGround > 15) {
      this.sinceGround = 0;
      this.ray.set(new THREE.Vector3(at.x, at.y, at.z + 0.05), new THREE.Vector3(0, 0, -1));
      this.ray.far = 1;
      const hit = this.ray.intersectObjects(surfaces, false).find((h) => h.object.visible);
      this.ground = hit ? hit.point.z : 0;
    }
    this.decal.position.set(at.x, at.y, this.ground + 0.0008);
    this.cam.position.set(at.x, at.y, this.ground - 0.002);
    this.cam.updateMatrixWorld();
  }

  /** Draw the robot's footprint into the decal. Call once per frame, before the main render. */
  render(renderer: THREE.WebGLRenderer, scene: THREE.Scene) {
    const background = scene.background, override = scene.overrideMaterial, fog = scene.fog;
    const clearAlpha = renderer.getClearAlpha(), target = renderer.getRenderTarget();
    const autoShadows = renderer.shadowMap.autoUpdate;
    scene.background = null; scene.fog = null; scene.overrideMaterial = this.depth;
    renderer.shadowMap.autoUpdate = false; // the shadow maps are drawn once, by the main render
    renderer.setClearAlpha(0);
    this.decal.visible = false;

    renderer.setRenderTarget(this.rt);
    renderer.clear();
    renderer.render(scene, this.cam);
    this.blurPass(renderer, this.blur);
    this.blurPass(renderer, this.blur * 0.4); // a second, finer pass hides the first one's banding

    this.decal.visible = true;
    scene.background = background; scene.fog = fog; scene.overrideMaterial = override;
    renderer.shadowMap.autoUpdate = autoShadows;
    renderer.setClearAlpha(clearAlpha);
    renderer.setRenderTarget(target);
  }

  private blurPass(renderer: THREE.WebGLRenderer, amount: number) {
    this.hBlur.uniforms.tDiffuse.value = this.rt.texture;
    this.hBlur.uniforms.h.value = amount / this.res;
    this.quad.material = this.hBlur;
    renderer.setRenderTarget(this.rtBlur);
    this.quad.render(renderer);
    this.vBlur.uniforms.tDiffuse.value = this.rtBlur.texture;
    this.vBlur.uniforms.v.value = amount / this.res;
    this.quad.material = this.vBlur;
    renderer.setRenderTarget(this.rt);
    this.quad.render(renderer);
  }

  dispose() {
    this.rt.dispose(); this.rtBlur.dispose();
    this.decal.geometry.dispose(); (this.decal.material as THREE.Material).dispose();
    this.depth.dispose(); this.hBlur.dispose(); this.vBlur.dispose(); this.quad.dispose();
  }
}
