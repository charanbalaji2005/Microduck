import { wsUrl } from "./api";
import { bumpTick, live, sampleHistory, setState, setUi, upsertCommand, getState } from "./store";
import type { BusEvent, Execution } from "./types";
import type { ChatMsg } from "./store";
import { sayReply, stopTalking } from "../voice/events";
import { voiceBlocked } from "../voice/duckVoice";
import { STAGE_SCENERIES } from "../sim/scenery";

export function nextScenery(current: string) {
  const i = STAGE_SCENERIES.findIndex((s) => s.id === current);
  return STAGE_SCENERIES[(i + 1) % STAGE_SCENERIES.length].id;
}

function chatFrom(e: BusEvent): ChatMsg | null {
  if (e.kind === "chat.user") return { id: `u${e.id}`, who: "user", text: e.data.text ?? e.message, t: e.t };
  if (e.kind === "chat.reply") {
    const d = e.data;
    return { id: d.id || `d${e.id}`, who: "duck", text: d.text ?? e.message, babble: d.babble, mood: d.mood, t: e.t,
      session: d.session, brain: !!(d.brain || d.session), sources: d.sources?.length ? d.sources : undefined,
      actions: d.actions?.length ? d.actions : undefined, error: d.error, retry: d.retry ?? undefined,
      structured: d.structured, question: typeof d.question === "string" ? d.question : undefined,
      followups: Array.isArray(d.followups) ? d.followups.filter((q: unknown) => typeof q === "string").slice(0, 3) : undefined };
  }
  return null;
}

/** A brain reply being written: "thinking…", then the words so far. Never logged or kept. */
function onTransient(e: BusEvent) {
  const d = e.data || {};
  if (!d.id || e.t < getState().chatClearedAt) return;
  if (e.kind === "chat.thinking") {
    setState((s) => s.chat.some((m) => m.id === d.id) ? {} : { chat: [...s.chat.slice(-199),
      { id: d.id, who: "duck", text: "", t: e.t, session: d.session, pending: true, brain: true }] });
  } else if (e.kind === "chat.delta") {
    setState((s) => ({ chat: s.chat.map((m) => m.id === d.id && m.pending ? { ...m, text: d.text ?? "" } : m) }));
  }
}

const VOICE_FAILED = "The duck's voice could not play.";
export const VOICE_BLOCKED = "Your browser is holding the duck's voice until you interact with the page.";

function voice(msg: ChatMsg, e: BusEvent) {
  const ui = getState().ui;
  if (ui.muted || !ui.autoSpeak || e.data.cancelled) return;
  const end = sayReply(msg.mood, msg.babble, msg.brain ? msg.text : undefined);
  const voiceError = end > 0 ? null : VOICE_FAILED;
  if (getState().voiceError !== voiceError) setState({ voiceError });
  // An AudioContext made without a click starts suspended; resume() is refused until one. Rather
  // than let the reply burst out late on the next click, drop it and say so (Retry voice replays it).
  if (end > 0) setTimeout(() => { if (voiceBlocked()) { stopTalking(); setState({ voiceError: VOICE_BLOCKED }); } }, 400);
}

// One WebSocket to the control service: frames, robot state, status and events in; teleop,
// pings and e-stop out. Reconnects forever with backoff.

let socket: WebSocket | null = null;
let backoff = 500;
let pingTimer: number | undefined;

export function send(msg: Record<string, unknown>) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
}

function onEvent(e: BusEvent) {
  if (e.transient) { onTransient(e); return; }
  setState((s) => ({ events: [...s.events.slice(-499), e] }));
  const msg = chatFrom(e);
  const prev = msg && getState().chat.find((m) => m.id === msg.id);
  // new, or the final form of a reply that was streaming in; a repeat is ignored
  if (msg && msg.t >= getState().chatClearedAt && (!prev || prev.pending)) {
    setState((s) => ({ chat: prev ? s.chat.map((m) => m.id === msg.id ? msg : m) : [...s.chat.slice(-199), msg],
      ...(msg.who === "duck" ? { bubble: msg } : {}) }));
    // "stop talking": cut the voice off (the legs are not touched) and say nothing more
    const hush = e.kind === "chat.reply" && e.data.ui?.voice === "stop";
    if (hush) stopTalking();
    else if (msg.who === "duck") voice(msg, e);
    // "show me the beach": the reply carries a viewer action, never a robot command
    const want = e.kind === "chat.reply" ? e.data.ui?.scenery : undefined;
    if (typeof want === "string" && Date.now() / 1000 - e.t < 30) setUi({ scenery: want === "next" ? nextScenery(getState().ui.scenery) : want });
  }
  const d = e.data || {};
  if (e.kind.startsWith("cmd.") && d.id) {
    const status = e.kind.slice(4);
    upsertCommand({ id: d.id, action: d.action ?? d.command?.action, command: d.command, source: d.source,
      status: status === "accepted" ? "queued" : status, ...(status !== "accepted" && status !== "started" ? { message: e.message.replace(/^[a-z_]+: /, ""), outcome: d.outcome, ended: e.t } : {}),
      ...(status === "accepted" ? { created: e.t, transcript: d.transcript ?? null } : {}) } as Partial<Execution> & { id: string });
  }
  if (e.kind === "ui.camera") {
    if (d.mode === "reset") setUi({ cameraSeq: getState().ui.cameraSeq + 1 });
    else setUi({ cameraMode: d.mode });
  }
  if (e.kind === "ui.panel") {
    const ui = getState().ui;
    const panel = d.panel as string;
    if (panel === "collision" || panel === "contacts") setUi({ overlays: { ...ui.overlays, [panel]: d.visible } });
    else if (["logs", "experiments", "voice", "policy", "navigation", "telemetry"].includes(panel) && d.visible) setUi({ page: panel === "voice" ? "voice" : panel });
    setUi({ panels: { ...getState().ui.panels, [panel]: d.visible } });
  }
  if (e.kind === "nav.plan") setState({ plan: { goal: d.goal, path: d.path } });
  if (e.kind === "sim.model_changed") {
    live.trail = [];
    setState((s) => ({ modelVersion: s.modelVersion + 1, plan: null, occupancy: null }));
  }
}

export function connect() {
  socket = new WebSocket(wsUrl());
  socket.onopen = () => {
    backoff = 500;
    setState({ wsConnected: true });
    pingTimer = window.setInterval(() => send({ type: "ping", t: performance.now() }), 2000);
  };
  socket.onclose = () => {
    setState({ wsConnected: false });
    window.clearInterval(pingTimer);
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 5000);
  };
  socket.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    switch (msg.type) {
      case "hello": {
        const cmds: Record<string, Execution> = {};
        const order: string[] = [];
        for (const c of [...msg.commands].reverse()) { cmds[c.id] = c; order.push(c.id); }
        const cleared = getState().chatClearedAt;
        const chat = (msg.events as BusEvent[]).map(chatFrom).filter((m): m is ChatMsg => !!m && m.t >= cleared);
        setState({ status: msg.status, events: msg.events, commands: cmds, commandOrder: order, chat,
                   bubble: [...chat].reverse().find((m) => m.who === "duck") ?? null });
        break;
      }
      case "sim":
        live.prevFrame = live.frame; live.prevFrameAt = live.frameAt;
        live.frame = msg.frame; live.frameAt = performance.now();
        sampleHistory(); bumpTick();
        break;
      case "robot":
        live.robot = msg.state; live.robotAt = performance.now();
        sampleHistory(); bumpTick();
        break;
      case "status": setState({ status: msg.status }); break;
      case "event": onEvent(msg.event); break;
      case "pong": setState({ latencyMs: Math.round(performance.now() - msg.t) }); break;
      case "teleop_refused": setState({ teleopRefused: msg.blockers }); setTimeout(() => setState({ teleopRefused: null }), 2500); break;
    }
  };
}
