// REST helpers. Everything goes to the local control service; nothing else is contacted.

const token = new URLSearchParams(location.search).get("token");
const headers: Record<string, string> = token ? { "x-mdx-token": token } : {};

export async function getJson<T = any>(path: string): Promise<T> {
  const r = await fetch(path, { headers });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export async function postJson<T = any>(path: string, body: unknown = {}): Promise<T> {
  const r = await fetch(path, { method: "POST", headers: { ...headers, "content-type": "application/json" }, body: JSON.stringify(body) });
  const text = await r.text();
  let data: any = text;
  try { data = JSON.parse(text); } catch { /* not JSON */ }
  if (!r.ok) throw new Error(typeof data === "string" ? data : (data.detail ? JSON.stringify(data.detail) : text));
  return data;
}

export async function getBuffer(path: string): Promise<ArrayBuffer> {
  const r = await fetch(path, { headers });
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.arrayBuffer();
}

export async function postAudio(blob: Blob, handsFree: boolean): Promise<any> {
  const form = new FormData();
  form.append("audio", blob, "clip.wav");
  form.append("execute", "true");
  form.append("hands_free", handsFree ? "true" : "false");
  const r = await fetch("/api/voice", { method: "POST", headers, body: form });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ?? `${r.status}`);
  return data;
}

export function command(cmd: Record<string, unknown>, source = "ui") {
  return postJson("/api/command", { command: cmd, source });
}

export const estop = () => postJson("/api/estop");
export const clearEstop = () => postJson("/api/estop/clear");
export const cancelAll = () => postJson("/api/cancel");
export const sendText = (text: string, source = "text") => postJson("/api/text", { text, execute: true, source });
export const wsUrl = () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws${token ? `?token=${token}` : ""}`;

/** This tab's conversation with the brain (brain.py keeps its context per session). */
export const chatSession: string = (() => {
  const fresh = `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
  try {
    const kept = sessionStorage.getItem("mdx-chat-session");
    if (kept && /^[A-Za-z0-9_-]{1,64}$/.test(kept)) return kept;
    sessionStorage.setItem("mdx-chat-session", fresh);
  } catch { /* private window: a session for this page load */ }
  return fresh;
})();

export async function postChatAudio(blob: Blob): Promise<any> {
  const form = new FormData();
  form.append("audio", blob, "clip.wav");
  form.append("session", chatSession);
  const r = await fetch("/api/chat/voice", { method: "POST", headers, body: form });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ?? `${r.status}`);
  return data;
}

export const chat = (text: string, source = "chat", regenerate = false) =>
  postJson("/api/chat", { text, source, session: chatSession, ...(regenerate ? { regenerate: true } : {}) });
export const chatFeedback = (id: string, rating: "up" | "down" | null) => postJson("/api/chat/feedback", { id, rating, session: chatSession });
export const chatCancel = () => postJson("/api/chat/cancel", { session: chatSession });
export const chatClear = () => postJson("/api/chat/clear", { session: chatSession });

/** postJson's error as a sentence: the service's `detail`, without the JSON quoting. */
export function errorText(e: unknown) {
  const s = e instanceof Error ? e.message : String(e);
  try { const v = JSON.parse(s); return typeof v === "string" ? v : s; } catch { return s; }
}
