import { command, estop } from "./api";
import { locomotion, type Drive } from "./locomotion";
import { getState, setUi } from "./store";
import type { CameraMode } from "./types";

// Keyboard teleop and shortcuts. Held keys drive the one locomotion controller (locomotion.ts),
// which resends the twist at 10 Hz; the backend has its own 350 ms deadman on top of robotd's
// 500 ms one, so a lost tab stops the robot. Space stops at once — keys, pad and any chat
// command walking the duck. (Holding Space to talk is the hangar page's, after this stop.)

const KEYS: Record<string, Drive> = {
  w: "forward", arrowup: "forward", s: "backward", arrowdown: "backward",
  a: "left", arrowleft: "left", d: "right", arrowright: "right", q: "strafeLeft", e: "strafeRight",
};

function typing(e: KeyboardEvent) {
  const t = e.target as HTMLElement;
  return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
}

export function installKeyboard() {
  const down = (e: KeyboardEvent) => {
    if (typing(e)) return;
    const k = e.key.toLowerCase();
    if (k === "escape") { estop(); e.preventDefault(); return; }
    locomotion.setFast(e.shiftKey);
    if (k === " ") { if (!e.repeat) locomotion.stop(); e.preventDefault(); return; }
    if (KEYS[k]) { if (!e.repeat) locomotion.press(KEYS[k], "key"); e.preventDefault(); return; }
    if (e.repeat) return;
    const cams: Record<string, CameraMode> = { "1": "follow", "2": "orbit", "3": "top", "4": "first_person" };
    if (cams[k]) { setUi({ cameraMode: cams[k] }); return; }
    if (k === "p") { command({ action: getState().status?.sim.paused ? "resume" : "pause" }, "keyboard"); return; }
  };
  const up = (e: KeyboardEvent) => {
    const k = e.key.toLowerCase();
    locomotion.setFast(e.shiftKey);
    if (KEYS[k]) locomotion.release(KEYS[k], "key");
  };
  // a key released in another window never arrives here: let go of everything the keys held
  const letGo = () => locomotion.releaseSource("key");
  const hidden = () => { if (document.hidden) letGo(); };
  window.addEventListener("keydown", down);
  window.addEventListener("keyup", up);
  window.addEventListener("blur", letGo);
  document.addEventListener("visibilitychange", hidden);
  return () => {
    window.removeEventListener("keydown", down);
    window.removeEventListener("keyup", up);
    window.removeEventListener("blur", letGo);
    document.removeEventListener("visibilitychange", hidden);
    letGo();
  };
}
