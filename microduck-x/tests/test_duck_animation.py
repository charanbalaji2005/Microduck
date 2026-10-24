"""The hangar duck's beak and legs, in the page's own code: frontend/src/sim/beak.ts driven by the
voice scheduler in voice/duckVoice.ts (under a fake AudioContext whose clock the test turns), and
the one locomotion controller in lib/locomotion.ts with the keyboard on top of it. Skipped unless
node and the dashboard's node_modules (with an esbuild for this OS) are installed."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

X = Path(__file__).resolve().parents[1]
FRONTEND = X / "frontend"
SRC = FRONTEND / "src"


def _runnable() -> bool:
    if not (shutil.which("node") and (FRONTEND / "node_modules" / "esbuild").exists()):
        return False
    probe = subprocess.run(["node", "-e", "require('esbuild').transformSync('1')"], cwd=FRONTEND, capture_output=True)
    return probe.returncode == 0  # node_modules from the other side of WSL has the other OS's esbuild


pytestmark = pytest.mark.skipif(not _runnable(), reason="needs node and the frontend's node_modules")

ENTRY = """
export * as THREE from "three";
export { BeakRig, HINGE, MAX_OPEN } from "{src}/sim/beak";
export { play, voiceLevel, stopVoice, isSpeaking, setVoiceMuted } from "{src}/voice/duckVoice";
export { Locomotion, SPEED, locomotion } from "{src}/lib/locomotion";
export { installKeyboard } from "{src}/lib/keyboard";
"""

HARNESS = r"""
// browser stand-ins: just enough for the modules to load and run
globalThis.location = { search: "", protocol: "http:", host: "test", origin: "http://test", pathname: "/", hash: "" };
globalThis.window = Object.assign(new EventTarget(), { setInterval, clearInterval, setTimeout, clearTimeout, innerWidth: 1400 });
globalThis.document = Object.assign(new EventTarget(), { hidden: false });
class N { connect(n) { return n; } disconnect() {} }
class FakeAudio {
  constructor() { this.currentTime = 0; this.state = "running"; this.outputLatency = 0; this.baseLatency = 0; this.destination = new N(); globalThis.__ctx = this; }
  createGain() { return Object.assign(new N(), { gain: { value: 1 } }); }
  createDynamicsCompressor() { return Object.assign(new N(), { threshold: { value: 0 }, ratio: { value: 0 } }); }
  createStereoPanner() { return Object.assign(new N(), { pan: { value: 0 } }); }
  createBufferSource() { return Object.assign(new N(), { buffer: null, onended: null, start() {}, stop() { this.stopped = true; } }); }
  createBuffer(ch, len, sr) { return { duration: len / sr, length: len, copyToChannel() {} }; }
  resume() {}
}
globalThis.AudioContext = FakeAudio;

const M = require(process.argv[2]);
const { THREE } = M;
const out = {};

// ── a head body as buildScene makes it, with the real model's beak geoms (compiled poses) ──
function scene() {
  const header = {
    ducks: [{ prefix: "" }],
    bodies: [{ id: 0, name: "world" }, { id: 1, name: "jaw_soft" }],
    meshes: [{ id: 0, name: "jaw" }, { id: 1, name: "jaw_soft" }, { id: 2, name: "soft_mouth_top" }, { id: 3, name: "top_head_shell" }],
    geoms: [0, 1, 2, 3].map((i) => ({ id: i, mesh: i })),
  };
  const head = new THREE.Group();
  const poses = [[-0.0045, 0, -0.018], [-0.0046, 0, -0.018], [-0.0042, 0, -0.0181], [0, 0, 0]];
  const meshes = poses.map((p, i) => {
    const m = new THREE.Mesh(new THREE.BufferGeometry());
    m.position.set(...p); m.quaternion.set(0.5, 0.5, 0.5, 0.5); m.userData.geom = i; head.add(m);
    return m;
  });
  return { header, built: { bodies: new Map([[1, head]]) }, meshes };
}
// where a point of a mesh (given in the head frame when shut) is now, in the head frame
function track(mesh, pHead) {
  const local = pHead.clone().applyMatrix4(mesh.userData.rest.clone().invert());
  mesh.updateMatrix();
  return local.applyMatrix4(mesh.matrix);
}

{ // 1. the beak follows the voice, and shuts when it stops
  const { header, built, meshes } = scene();
  const rig = M.BeakRig.find(header, built);
  const ctx = new FakeAudio(); // replaced by the module's own on first play
  out.found = !!rig;
  const upper0 = meshes[2].position.toArray().concat(meshes[2].quaternion.toArray());
  rig.update(1 / 60, 0); out.idle = rig.angle;
  const end = M.play("duck-a", "greet", 0);
  const c = globalThis.__ctx, dt = 1 / 60, angles = [];
  const tip = new THREE.Vector3(-0.015, 0, -0.076), tipY = [];
  let peakTipDrop = 0;
  for (let t = 0; t < end + 0.6; t += dt) {
    c.currentTime = t;
    rig.update(dt, M.voiceLevel("duck-a"));
    angles.push(rig.angle);
    peakTipDrop = Math.max(peakTipDrop, tip.x - track(meshes[0], tip).x);
  }
  out.duration = end;
  out.maxAngle = Math.max(...angles);
  out.endAngle = angles[angles.length - 1];
  out.peakTipDrop = peakTipDrop;
  out.distinctAngles = new Set(angles.map((a) => a.toFixed(3))).size;
  let peaks = 0; for (let i = 1; i + 1 < angles.length; i++) if (angles[i] > angles[i - 1] && angles[i] >= angles[i + 1] && angles[i] > 0.05) peaks++;
  out.peaks = peaks;
  out.upperMoved = meshes[2].position.toArray().concat(meshes[2].quaternion.toArray()).some((v, i) => Math.abs(v - upper0[i]) > 1e-12);
  out.shellMoved = meshes[3].position.length() > 0;
  out.speakingAfter = M.isSpeaking("duck-a");
}

{ // 2. stopped mid-word: silent at once, beak shut soon after
  const { header, built } = scene();
  const rig = M.BeakRig.find(header, built);
  const c = globalThis.__ctx; const t0 = c.currentTime + 1; c.currentTime = t0;
  M.play("duck-a", "coo", 1);
  let t = t0;
  for (; t < t0 + 0.4; t += 1 / 60) { c.currentTime = t; rig.update(1 / 60, M.voiceLevel("duck-a")); }
  out.openBeforeStop = rig.angle;
  M.stopVoice("duck-a");
  out.levelAfterStop = M.voiceLevel("duck-a");
  out.speakingAfterStop = M.isSpeaking("duck-a");
  for (let k = 0; k < 30; k++) { c.currentTime = (t += 1 / 60); rig.update(1 / 60, M.voiceLevel("duck-a")); }
  out.angleHalfSecondAfterStop = rig.angle;
  // muted: nothing heard, nothing moves
  M.setVoiceMuted(true); out.mutedPlay = M.play("duck-a", "greet", 2); M.setVoiceMuted(false);
  c.currentTime = (t += 0.1); out.mutedLevel = M.voiceLevel("duck-a");
}

{ // 3. frame-rate independent
  const a = M.BeakRig.find(scene().header, scene().built), b = M.BeakRig.find(scene().header, scene().built);
  for (let i = 0; i < 6; i++) a.update(1 / 30, 1);
  for (let i = 0; i < 29; i++) b.update(1 / 145, 1);
  out.fps30 = a.angle; out.fps145 = b.angle; out.maxOpen = M.MAX_OPEN;
}

{ // 4. the locomotion controller
  const sent = []; let cancels = 0, pumps = 0, stops = 0;
  const L = new M.Locomotion({ teleop: (t) => sent.push(t), cancel: () => cancels++, every: () => { pumps++; return () => stops++; } });
  const last = () => sent[sent.length - 1];
  L.press("forward", "key"); out.forward = last();
  L.press("forward", "pad"); L.release("forward", "key"); out.forwardStillHeldByPad = last();
  L.release("forward", "pad"); out.released = last();
  L.press("backward"); out.backward = last(); L.release("backward");
  L.press("left"); out.left = last(); L.release("left");
  L.press("right"); out.right = last(); L.release("right");
  L.press("forward"); L.press("left"); out.arc = last();
  L.setFast(true); out.fast = last(); L.setFast(false);
  L.stop(); out.stopped = last(); out.activeAfterStop = L.active().size;
  out.cancels = cancels; out.pumps = pumps; out.pumpStops = stops; out.speed = M.SPEED;
}

{ // 5. walking and talking at once: neither stops the other
  const sent = []; let cancels = 0;
  const L = new M.Locomotion({ teleop: (t) => sent.push(t), cancel: () => cancels++, every: () => () => {} });
  const c = globalThis.__ctx; c.currentTime += 2;
  const end = M.play("duck-a", "wheee", 0);
  L.press("forward");
  c.currentTime += 0.8;
  out.bothLevel = M.voiceLevel("duck-a"); out.bothTwist = sent[sent.length - 1];
  L.stop();
  out.speakingAfterLegsStop = M.isSpeaking("duck-a"); out.levelAfterLegsStop = M.voiceLevel("duck-a");
  L.press("backward"); const n = sent.length;
  M.stopVoice("duck-a");
  out.teleopsFromStopTalking = sent.length - n; out.legsAfterStopTalking = [...L.active()];
  L.stop();
}

{ // 6. the keyboard drives the same controller; Space stops it
  const off = M.installKeyboard();
  const key = (type, k, extra = {}) => { const e = new Event(type, { cancelable: true }); Object.assign(e, { key: k, shiftKey: false, repeat: false, ...extra }); window.dispatchEvent(e); return e; };
  const act = () => [...M.locomotion.active()].sort();
  key("keydown", "ArrowUp"); out.kUp = act();
  key("keydown", "a"); out.kUpA = act();
  key("keyup", "a"); key("keyup", "ArrowUp"); out.kNone = act();
  key("keydown", "s"); key("keydown", "ArrowRight"); out.kBackRight = act();
  out.spaceDefaultPrevented = key("keydown", " ").defaultPrevented; out.kAfterSpace = act();
  key("keydown", "d"); window.dispatchEvent(new Event("blur")); out.kAfterBlur = act();
  off();
  key("keydown", "w"); out.kAfterUninstall = act();
}

console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("anim")
    entry = tmp / "entry.ts"
    entry.write_text(ENTRY.replace("{src}", SRC.as_posix()), encoding="utf-8")
    bundle = tmp / "anim.cjs"
    build = ("require('esbuild').buildSync({entryPoints: [process.argv[1]], bundle: true, format: 'cjs', platform: 'node',"
             " outfile: process.argv[2], nodePaths: [process.argv[3]], logLevel: 'error'})")
    subprocess.run(["node", "-e", build, str(entry), str(bundle), str(FRONTEND / "node_modules")], cwd=FRONTEND, check=True)
    harness = tmp / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    run = subprocess.run(["node", str(harness), str(bundle)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_beak_is_found_on_the_real_head_body(result):
    assert result["found"]


def test_beak_opens_through_the_voice_and_closes_after(result):
    r = result
    assert r["idle"] == 0                                    # shut while nobody speaks
    assert r["maxAngle"] > 0.6 * r["maxOpen"]                # opens wide on the loud syllable
    assert r["peakTipDrop"] > 0.015                          # the tip of the lower beak drops (m, head frame)
    assert r["endAngle"] == 0 and not r["speakingAfter"]     # shut once the sound is over
    assert r["distinctAngles"] > 15 and r["peaks"] >= 2      # follows the sound's shape, not one fixed flap
    assert not r["upperMoved"] and not r["shellMoved"]       # only the lower beak moves


def test_stop_talking_shuts_the_beak(result):
    r = result
    assert r["openBeforeStop"] > 0.05
    assert r["levelAfterStop"] == 0 and not r["speakingAfterStop"]
    assert r["angleHalfSecondAfterStop"] == 0
    assert r["mutedPlay"] == 0 and r["mutedLevel"] == 0


def test_beak_motion_is_frame_rate_independent(result):
    assert result["fps30"] == pytest.approx(result["fps145"], rel=0.03)


def test_locomotion_forward_backward_turn_and_stop(result):
    r, s = result, result["speed"]
    assert r["forward"] == {"vx": s["forward"], "vy": 0, "vyaw": 0}
    assert r["forwardStillHeldByPad"]["vx"] == s["forward"]  # keys and pad share one controller
    assert r["released"] == {"vx": 0, "vy": 0, "vyaw": 0}
    assert r["backward"]["vx"] == -s["backward"]
    assert r["left"]["vyaw"] == s["turn"] and r["right"]["vyaw"] == -s["turn"]
    assert r["arc"]["vx"] > 0 and r["arc"]["vyaw"] > 0
    assert r["fast"]["vx"] > r["arc"]["vx"]
    assert r["stopped"] == {"vx": 0, "vy": 0, "vyaw": 0} and r["activeAfterStop"] == 0
    assert r["cancels"] == 1                                 # Space/■ also stops a chat-started walk
    assert r["pumps"] == r["pumpStops"]                      # no resend timer left running
    # what the controller asks for is what the gait steps for (backend Settings.gait_min_*)
    assert s["forward"] >= 0.28 and s["backward"] >= 0.38 and s["turn"] >= 0.85


def test_walking_and_talking_together(result):
    r = result
    assert r["bothLevel"] > 0 and r["bothTwist"]["vx"] > 0
    assert r["speakingAfterLegsStop"] and r["levelAfterLegsStop"] > 0   # stopping the legs leaves the voice
    assert r["teleopsFromStopTalking"] == 0 and r["legsAfterStopTalking"] == ["backward"]  # and vice versa


def test_keyboard_drives_the_same_controller(result):
    r = result
    assert r["kUp"] == ["forward"] and r["kUpA"] == ["forward", "left"] and r["kNone"] == []
    assert r["kBackRight"] == ["backward", "right"]
    assert r["kAfterSpace"] == [] and r["spaceDefaultPrevented"]
    assert r["kAfterBlur"] == []
    assert r["kAfterUninstall"] == []                          # listeners cleaned up
