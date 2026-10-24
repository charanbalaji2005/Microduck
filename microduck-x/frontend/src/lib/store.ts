import { useSyncExternalStore } from "react";
import type { BusEvent, CameraMode, Execution, RobotState, SimFrame, Status } from "./types";

// One small store. High-rate data (30 Hz frames) lives in `live` and is read by the 3D viewer
// directly; React panels re-render on `tick`, bumped at most 10 times a second.

export interface VoiceEntry {
  id: string; t: number; transcript: string; engine?: string; confidence?: number | null; commands: string[];
  executions: { id: string; action: string; status: string }[]; reply?: string; needsConfirmation?: boolean;
  errors: string[]; unmatched: string[]; notes: string[]; ignored?: string; source: "voice" | "text";
}

export interface ChatMsg {
  id: string; who: "duck" | "user"; text: string; babble?: string; mood?: string; t: number;
  confirm?: string; // a transcript waiting for "yes, run it"
  session?: string;  // the chat session a brain reply belongs to
  pending?: boolean; // the brain is still writing it (chat.thinking / chat.delta)
  sources?: { title: string; url: string }[]; // only from a lookup that actually ran
  actions?: { action: string; id?: string; status?: string }[]; // what a brain reply started
  retry?: string;    // a failed question that can be asked again
  error?: string;
  brain?: boolean;   // written by the Groq brain, not the persona
  structured?: unknown;  // brain.py's metadata (kind, code, maths); checked before use (chatContent.structuredOf)
  followups?: string[];  // questions the model suggests asking next
  question?: string;     // what was asked — "regenerate" asks it again
  feedback?: "up" | "down" | null;
}

export interface UiState {
  page: string;
  cameraMode: CameraMode;
  cameraSeq: number; // bumps on "reset" so the viewer can react
  panels: Record<string, boolean>;
  overlays: { grid: boolean; axes: boolean; heading: boolean; trail: boolean; contacts: boolean; collision: boolean; jointAxes: boolean; markers: boolean; path: boolean; occupancy: boolean };
  sensitivity: number;
  fov: number;
  shadows: boolean;
  ao: boolean;
  tts: "browser" | "espeak" | "off";
  handsFree: boolean;
  muted: boolean;
  /** speak each reply in the duck's voice as it arrives */
  autoSpeak: boolean;
  /** the duck's voice, 0..1 */
  volume: number;
  bloom: boolean;
  chatOpen: boolean;
  /** the world around the stage (sim/scenery.ts) */
  scenery: string;
  /** change the scenery by itself every `sceneryEvery` seconds */
  sceneryAuto: boolean;
  sceneryEvery: number;
  /** background ducks playing on the deck (viewer animation) */
  cousins: number;
  /** the duck talks by itself after a quiet spell */
  idleTalk: boolean;
  /** show the duck-speak strip (babble) above brain answers */
  duckSpeak: boolean;
}

export interface HistoryPoint {
  t: number; simT: number | null; vFwd: number | null; wz: number | null; tilt: number | null; roll: number | null; pitch: number | null;
  z: number | null; q: (number | null)[]; qd: (number | null)[]; loopHz: number | null; rtf: number | null; reqVx: number | null; appVx: number | null;
  reqVyaw: number | null; footL: number; footR: number;
}

interface State {
  status: Status | null;
  wsConnected: boolean;
  latencyMs: number | null;
  events: BusEvent[];
  commands: Record<string, Execution>;
  commandOrder: string[];
  ui: UiState;
  tick: number;
  modelVersion: number;
  plan: { goal: string; path: [number, number][] } | null;
  occupancy: { bounds: number[]; resolution: number; nx: number; ny: number; blocked_bits: string } | null;
  voice: VoiceEntry[];
  teleopRefused: string[] | null;
  chat: ChatMsg[];
  bubble: ChatMsg | null;
  /** chat before this time (s) is not shown again: the user cleared it */
  chatClearedAt: number;
  /** the duck's voice could not play: shown, and recoverable with a click */
  voiceError: string | null;
}

const UI_KEY = "mdx-ui";
const defaultUi: UiState = {
  page: "hangar", cameraMode: "follow", cameraSeq: 0,
  panels: { joints: true, velocities: true, telemetry: true, contacts: false, logs: true, experiments: false },
  overlays: { grid: true, axes: true, heading: true, trail: true, contacts: true, collision: false, jointAxes: false, markers: true, path: true, occupancy: false },
  sensitivity: 1, fov: 45, shadows: true, ao: false, tts: "browser", handsFree: false,
  muted: false, autoSpeak: true, volume: 0.8, bloom: true, chatOpen: true,
  scenery: "meadow", sceneryAuto: true, sceneryEvery: 120, cousins: 4, idleTalk: true, duckSpeak: true,
};

function loadUi(): UiState {
  try {
    const raw = localStorage.getItem(UI_KEY);
    if (raw) return { ...defaultUi, ...JSON.parse(raw), cameraSeq: 0 };
  } catch { /* private window, blocked storage */ }
  return defaultUi;
}

let state: State = {
  status: null, wsConnected: false, latencyMs: null, events: [], commands: {}, commandOrder: [],
  ui: loadUi(), tick: 0, modelVersion: 0, plan: null, occupancy: null, voice: [], teleopRefused: null,
  chat: [], bubble: null, chatClearedAt: loadClearedAt(), voiceError: null,
};

function loadClearedAt() {
  try { return Number(sessionStorage.getItem("mdx-chat-cleared")) || 0; } catch { return 0; }
}
export function clearChatView() {
  const t = Date.now() / 1000;
  try { sessionStorage.setItem("mdx-chat-cleared", String(t)); } catch { /* ignore */ }
  setState({ chat: [], bubble: null, chatClearedAt: t });
}

export const live = {
  frame: null as SimFrame | null,
  prevFrame: null as SimFrame | null,
  frameAt: 0,
  prevFrameAt: 0,
  robot: null as RobotState | null,
  robotAt: 0,
  simRate: 0,
  history: [] as HistoryPoint[],
  trail: [] as [number, number, number][],
};

const listeners = new Set<() => void>();
export function getState() { return state; }
export function setState(patch: Partial<State> | ((s: State) => Partial<State>)) {
  const p = typeof patch === "function" ? patch(state) : patch;
  state = { ...state, ...p };
  if (p.ui) {
    try { localStorage.setItem(UI_KEY, JSON.stringify(state.ui)); } catch { /* ignore */ }
  }
  listeners.forEach((l) => l());
}
export function setUi(patch: Partial<UiState>) { setState((s) => ({ ui: { ...s.ui, ...patch } })); }
export function subscribe(l: () => void) { listeners.add(l); return () => listeners.delete(l); }
export function useStore<T>(selector: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state));
}

let lastTick = 0;
export function bumpTick() {
  const now = performance.now();
  if (now - lastTick > 100) { lastTick = now; setState((s) => ({ tick: s.tick + 1 })); }
}

export function upsertCommand(ex: Partial<Execution> & { id: string }) {
  setState((s) => {
    const prev = s.commands[ex.id];
    const merged = { ...(prev ?? {}), ...ex } as Execution;
    const order = prev ? s.commandOrder : [ex.id, ...s.commandOrder].slice(0, 300);
    return { commands: { ...s.commands, [ex.id]: merged }, commandOrder: order };
  });
}

// ── telemetry history, sampled at 10 Hz client-side from the live streams ────

function tiltOf(g?: number[]) {
  if (!g) return null;
  return (Math.acos(Math.max(-1, Math.min(1, -g[2]))) * 180) / Math.PI;
}

let lastSample = 0;
export function sampleHistory() {
  const now = performance.now();
  if (now - lastSample < 100) return;
  lastSample = now;
  const f = live.frame, r = live.robot;
  const d = f?.ducks?.[0];
  let vFwd = null, wz = null, roll = null, pitch = null;
  if (d) {
    const c = Math.cos(d.yaw), s = Math.sin(d.yaw);
    vFwd = c * d.linvel[0] + s * d.linvel[1];
    wz = d.angvel[2];
    const [gx, gy, gz] = d.gravity;
    roll = (Math.atan2(-gy, -gz) * 180) / Math.PI;
    pitch = (Math.atan2(gx, Math.hypot(gy, gz)) * 180) / Math.PI;
  }
  live.history.push({
    t: Date.now() / 1000, simT: f?.t ?? null, vFwd, wz, tilt: d ? tiltOf(d.gravity) : null, roll, pitch, z: d ? d.trunk[2] : null,
    q: d ? d.q : [], qd: d ? d.qd : [], loopHz: r?.loop?.hz ?? null, rtf: f?.rtf ?? null,
    reqVx: r?.move?.requested?.[0] ?? null, appVx: r?.move?.applied?.[0] ?? null, reqVyaw: r?.move?.requested?.[2] ?? null,
    footL: d?.feet.left ? 1 : 0, footR: d?.feet.right ? 1 : 0,
  });
  if (live.history.length > 1200) live.history.splice(0, live.history.length - 1200);
  if (d) {
    const last = live.trail[live.trail.length - 1];
    const p: [number, number, number] = [d.trunk[0], d.trunk[1], 0.004];
    if (!last || Math.hypot(last[0] - p[0], last[1] - p[1]) > 0.01) live.trail.push(p);
    if (live.trail.length > 2000) live.trail.splice(0, live.trail.length - 2000);
  }
}

export const JOINT_LABELS = [
  "L hip yaw", "L hip roll", "L hip pitch", "L knee", "L ankle", "neck pitch", "head pitch", "head yaw", "head roll", "mouth",
  "R hip yaw", "R hip roll", "R hip pitch", "R knee", "R ankle",
];
