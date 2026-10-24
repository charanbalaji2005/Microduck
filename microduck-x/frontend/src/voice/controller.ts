import { useSyncExternalStore } from "react";
import { postAudio, postChatAudio, sendText } from "../lib/api";
import { getState, setState, setUi, type VoiceEntry } from "../lib/store";
import { Mic, speak } from "./recorder";

// The voice pipeline's front half: microphone → clip → /api/voice → transcript, parsed commands,
// execution records. Everything after the clip is decided by the backend's allowlist.

interface MicState { open: boolean; recording: boolean; busy: boolean; level: number; floor: number; speaking: boolean; error: string | null }
let mic: Mic | null = null;
let ms: MicState = { open: false, recording: false, busy: false, level: 0, floor: 0, speaking: false, error: null };
const subs = new Set<() => void>();
const setMs = (p: Partial<MicState>) => { ms = { ...ms, ...p }; subs.forEach((s) => s()); };
export const useMic = () => useSyncExternalStore((l) => { subs.add(l); return () => subs.delete(l); }, () => ms);

let lastLevel = 0;
async function ensureMic() {
  if (mic?.ctx) return mic;
  mic = new Mic({
    onLevel: (rms, floor, speaking) => {
      const now = performance.now();
      if (now - lastLevel > 66) { lastLevel = now; setMs({ level: rms, floor, speaking }); }
    },
    onUtterance: (wav) => void submit(wav, true),
  });
  try {
    await mic.open();
    mic.handsFree = getState().ui.handsFree;
    setMs({ open: true, error: null });
  } catch (e) {
    setMs({ open: false, error: `microphone unavailable: ${e}` });
    mic = null;
    throw e;
  }
  return mic;
}

export async function toggleMic() {
  if (ms.open) { mic?.close(); mic = null; setMs({ open: false, recording: false, speaking: false, level: 0 }); setUi({ handsFree: false }); return; }
  await ensureMic().catch(() => undefined);
}

export async function setHandsFree(on: boolean) {
  setUi({ handsFree: on });
  if (on) await ensureMic().catch(() => undefined);
  if (mic) mic.handsFree = on;
}

export async function pttDown() {
  const m = await ensureMic().catch(() => null);
  if (!m || ms.recording) return;
  m.startPtt();
  setMs({ recording: true });
}

export async function pttUp(mode: "command" | "chat" = "command") {
  if (!mic || !ms.recording) return;
  const wav = mic.stopPtt();
  setMs({ recording: false });
  if (!wav || wav.size <= 3200) return;
  if (mode === "chat") await submitChat(wav);
  else await submit(wav, false);
}

async function submitChat(wav: Blob) {
  setMs({ busy: true });
  try {
    const r = await postChatAudio(wav);
    if (r.needs_confirmation && r.transcript?.text) {
      const t = r.transcript.text as string;
      setState((s) => ({ chat: [...s.chat, { id: `c${Date.now()}`, who: "duck", t: Date.now() / 1000, mood: "curious",
        text: `Did you say “${t}”?`, confirm: t }] }));
    } else if (!r.transcript?.text) {
      setState((s) => ({ chat: [...s.chat, { id: `c${Date.now()}`, who: "duck", t: Date.now() / 1000, mood: "curious", text: "I didn't catch that." }] }));
    }
  } catch (e) {
    setState((s) => ({ chat: [...s.chat, { id: `c${Date.now()}`, who: "duck", t: Date.now() / 1000, mood: "sorry", text: `Voice failed: ${e}` }] }));
  } finally {
    setMs({ busy: false });
  }
}

function entryFrom(r: any, source: "voice" | "text", text?: string): VoiceEntry {
  return {
    id: Math.random().toString(36).slice(2), t: Date.now() / 1000,
    transcript: r.transcript?.text ?? text ?? "", engine: r.transcript?.engine, confidence: r.transcript?.confidence,
    commands: (r.commands ?? []).map((c: any) => c.action),
    executions: (r.executions ?? []).map((e: any) => ({ id: e.id, action: e.action, status: e.status })),
    reply: r.reply, needsConfirmation: r.needs_confirmation, errors: r.parse?.errors ?? [], unmatched: r.parse?.unmatched ?? [],
    notes: r.parse?.notes ?? [], ignored: r.ignored, source,
  };
}

function record(e: VoiceEntry) {
  setState((s) => ({ voice: [e, ...s.voice].slice(0, 100) }));
  if (e.reply) speak(e.reply, getState().ui.tts);
}

async function submit(wav: Blob, handsFree: boolean) {
  setMs({ busy: true });
  try {
    const r = await postAudio(wav, handsFree);
    if (handsFree && r.ignored && !r.transcript?.text) return;
    record(entryFrom(r, "voice"));
  } catch (e) {
    record({ id: Math.random().toString(36).slice(2), t: Date.now() / 1000, transcript: "", commands: [], executions: [], errors: [String(e)], unmatched: [], notes: [], source: "voice" });
  } finally {
    setMs({ busy: false });
  }
}

export async function submitText(text: string) {
  if (!text.trim()) return;
  try {
    const r = await sendText(text);
    record(entryFrom(r, "text", text));
  } catch (e) {
    record({ id: Math.random().toString(36).slice(2), t: Date.now() / 1000, transcript: text, commands: [], executions: [], errors: [String(e)], unmatched: [], notes: [], source: "text" });
  }
}

export async function confirm(entry: VoiceEntry) {
  setState((s) => ({ voice: s.voice.map((v) => (v.id === entry.id ? { ...v, needsConfirmation: false } : v)) }));
  const r = await sendText(entry.transcript, "voice");
  record(entryFrom(r, "voice", entry.transcript));
}

export function discard(entry: VoiceEntry) {
  setState((s) => ({ voice: s.voice.map((v) => (v.id === entry.id ? { ...v, needsConfirmation: false, ignored: "discarded" } : v)) }));
}
