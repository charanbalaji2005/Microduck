// Clipboard, downloads and reading aloud for chat replies — one place, so every button behaves
// the same: success is reported only after the browser says the copy happened.

export type CopyResult = "copied" | "copied-plain" | "failed";

function legacyCopy(text: string): boolean {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
  document.body.appendChild(ta);
  const sel = document.getSelection();
  const kept = sel && sel.rangeCount ? sel.getRangeAt(0) : null;
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { ok = false; }
  ta.remove();
  if (kept && sel) { sel.removeAllRanges(); sel.addRange(kept); }
  return ok;
}

/** Copy text exactly (newlines and indentation kept). */
export async function copyText(text: string): Promise<CopyResult> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return "copied";
    }
  } catch { /* permission refused or not a secure context: try the old way */ }
  return legacyCopy(text) ? "copied" : "failed";
}

/** Copy with formatting (HTML) where the browser supports it, else as plain text. */
export async function copyRich(html: string, text: string): Promise<CopyResult> {
  try {
    if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
      await navigator.clipboard.write([new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([text], { type: "text/plain" }),
      })]);
      return "copied";
    }
  } catch { /* fall back to plain text below */ }
  const plain = await copyText(text);
  return plain === "copied" ? "copied-plain" : plain;
}

/** Save text as a file. The content is only ever a download, never opened or run. */
export function downloadText(name: string, text: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([text], { type: `${type};charset=utf-8` }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name.replace(/[\\/:*?"<>|]/g, "_") || "download.txt";
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ── reading aloud: the browser's own speech (words), separate from the duck's chirps ──

type Listener = (id: string | null, paused: boolean) => void;
let reading: string | null = null;
let paused = false;
const listeners = new Set<Listener>();
const notify = () => listeners.forEach((l) => l(reading, paused));

export const canReadAloud = () => typeof window !== "undefined" && "speechSynthesis" in window;

export function readAloud(id: string, text: string): boolean {
  if (!canReadAloud() || !text.trim()) return false;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text.slice(0, 6000));
  const local = speechSynthesis.getVoices().find((v) => v.localService && v.lang.startsWith("en"));
  if (local) u.voice = local;
  u.rate = 1.05;
  const done = () => { if (reading === id) { reading = null; paused = false; notify(); } };
  u.onend = done;
  u.onerror = done;
  reading = id;
  paused = false;
  notify();
  speechSynthesis.speak(u);
  return true;
}

export function stopReading() {
  if (canReadAloud()) speechSynthesis.cancel();
  if (reading) { reading = null; paused = false; notify(); }
}

/** Pause or resume what is being read aloud (the browser keeps its place). */
export function pauseReading(pause: boolean) {
  if (!canReadAloud() || !reading) return;
  if (pause) speechSynthesis.pause(); else speechSynthesis.resume();
  paused = pause;
  notify();
}

export function onReading(l: Listener) { listeners.add(l); return () => { listeners.delete(l); }; }
