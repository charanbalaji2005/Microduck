// Microphone capture in the browser, entirely local: raw PCM from an AudioWorklet, encoded here
// as 16 kHz mono 16-bit WAV, posted only to the local control service.
//
// Two modes share one pipeline:
//   push-to-talk — record while a key/button is held;
//   hands-free   — an energy voice-activity detector with an adaptive noise floor cuts
//                  utterances out of the stream (with 300 ms of pre-roll), and the backend
//                  requires the wake word ("Microduck …") before acting on them.
// The browser's own echo cancellation / noise suppression / AGC are switched on.

const WORKLET = `
class Tap extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0] && inputs[0][0]; if (ch) this.port.postMessage(ch.slice(0)); return true; }
}
registerProcessor("mdx-tap", Tap);`;

export interface MicCallbacks {
  onLevel?: (rms: number, floor: number, speaking: boolean) => void;
  onUtterance?: (wav: Blob, seconds: number) => void;
}

export class Mic {
  ctx: AudioContext | null = null;
  stream: MediaStream | null = null;
  node: AudioWorkletNode | null = null;
  rate = 48000;
  recording = false;
  handsFree = false;
  private chunks: Float32Array[] = [];
  private preroll: Float32Array[] = [];
  private floor = 0.004;
  private speaking = false;
  private speechMs = 0;
  private silenceMs = 0;
  constructor(private cb: MicCallbacks) {}

  async open() {
    if (this.ctx) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
    this.rate = this.ctx.sampleRate;
    const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "mdx-tap");
    this.node.port.onmessage = (e) => this.onBlock(e.data as Float32Array);
    src.connect(this.node);
  }

  close() {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.ctx?.close();
    this.ctx = null; this.stream = null; this.node = null;
    this.recording = false; this.speaking = false;
  }

  startPtt() { this.chunks = [...this.preroll]; this.recording = true; }
  stopPtt(): Blob | null {
    if (!this.recording) return null;
    this.recording = false;
    const wav = this.encode(this.chunks);
    this.chunks = [];
    return wav;
  }

  private onBlock(block: Float32Array) {
    let sum = 0;
    for (let i = 0; i < block.length; i++) sum += block[i] * block[i];
    const rms = Math.sqrt(sum / block.length);
    const ms = (block.length / this.rate) * 1000;
    if (!this.speaking) this.floor = this.floor * 0.995 + Math.min(rms, 0.05) * 0.005;
    this.preroll.push(block);
    while (this.preroll.length * ms > 300) this.preroll.shift();
    if (this.recording) this.chunks.push(block);
    if (this.handsFree && !this.recording) {
      const loud = rms > Math.max(0.012, this.floor * 3.5);
      if (loud) { this.speechMs += ms; this.silenceMs = 0; } else { this.silenceMs += ms; if (!this.speaking) this.speechMs = 0; }
      if (!this.speaking && this.speechMs > 120) { this.speaking = true; this.chunks = [...this.preroll]; }
      if (this.speaking) {
        this.chunks.push(block);
        const seconds = (this.chunks.length * ms) / 1000;
        if (this.silenceMs > 700 || seconds > 8) {
          this.speaking = false; this.speechMs = 0;
          const wav = this.encode(this.chunks); this.chunks = [];
          if (seconds > 0.5) this.cb.onUtterance?.(wav, seconds);
        }
      }
    }
    this.cb.onLevel?.(rms, this.floor, this.speaking || this.recording);
  }

  private encode(chunks: Float32Array[]): Blob {
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const ratio = this.rate / 16000;
    const n = Math.floor(total / ratio);
    const out = new Int16Array(n);
    // box-filter downsample to 16 kHz
    let off = 0;
    const flat = new Float32Array(total);
    for (const c of chunks) { flat.set(c, off); off += c.length; }
    for (let i = 0; i < n; i++) {
      const start = Math.floor(i * ratio), end = Math.min(total, Math.floor((i + 1) * ratio));
      let s = 0;
      for (let j = start; j < end; j++) s += flat[j];
      const v = s / Math.max(1, end - start);
      out[i] = Math.max(-32768, Math.min(32767, Math.round(v * 32767)));
    }
    const buf = new ArrayBuffer(44 + out.byteLength);
    const v = new DataView(buf);
    const w = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, "RIFF"); v.setUint32(4, 36 + out.byteLength, true); w(8, "WAVE"); w(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 16000, true);
    v.setUint32(28, 32000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, "data"); v.setUint32(40, out.byteLength, true);
    new Int16Array(buf, 44).set(out);
    return new Blob([buf], { type: "audio/wav" });
  }
}

export function speak(text: string, mode: "browser" | "espeak" | "off") {
  if (!text || mode === "off") return;
  if (mode === "espeak") {
    const a = new Audio(`/api/tts?text=${encodeURIComponent(text)}`);
    a.play().catch(() => speak(text, "browser"));
    return;
  }
  if ("speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(text);
    const local = speechSynthesis.getVoices().find((v) => v.localService && v.lang.startsWith("en"));
    if (local) u.voice = local; // prefer an on-device voice
    u.rate = 1.05;
    speechSynthesis.cancel();
    speechSynthesis.speak(u);
  }
}
