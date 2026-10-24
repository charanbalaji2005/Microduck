// The Microduck's own voice, in the browser.
//
// A line-for-line port of the robot's voice synth, `sounds/` at the repository root (itself a
// port of apirrone/microduck_sounds): the same xoshiro256++ stream, the same Personality traits
// drawn in the same order, the same seven recipes (alarm, greet, inquire, peck, chirp, coo,
// wheee). A duck's voice is derived from its identity exactly as on the robot —
// sha256(DUCK_IDENTITY), first four bytes — so `duck-a` here is the voice robotd's `duck-a`
// would play from its bank. Only playback is new: robotd plays a tag when something happens
// (greet on wake, chirp on a mode change, coo when petted, peck before power-off); here a chat
// reply's mood picks the tags. Checked against `sounds render` by
// `microduck-x/tests/test_duck_voice.py`.

export const SR = 48_000;
const TUNED_SR = 22_050;
const TAU = Math.PI * 2;
const f32 = Math.fround;

// ── rng.rs: xoshiro256++ seeded through splitmix64, on 32-bit halves ────────────────────────

class Rng {
  private s = new Uint32Array(8); // [hi0, lo0, hi1, lo1, …]
  private spare: number | null = null;

  constructor(seed: number) {
    let x = BigInt(seed >>> 0);
    const M = (1n << 64n) - 1n;
    for (let i = 0; i < 4; i++) {
      x = (x + 0x9e3779b97f4a7c15n) & M;
      let z = x;
      z = ((z ^ (z >> 30n)) * 0xbf58476d1ce4e5b9n) & M;
      z = ((z ^ (z >> 27n)) * 0x94d049bb133111ebn) & M;
      z = z ^ (z >> 31n);
      this.s[2 * i] = Number(z >> 32n);
      this.s[2 * i + 1] = Number(z & 0xffffffffn);
    }
  }

  /** The next u64 as [hi, lo]. */
  private next(): [number, number] {
    const s = this.s;
    // result = rotl(s0 + s3, 23) + s0
    let [h, l] = add(s[0], s[1], s[6], s[7]);
    [h, l] = rotl(h, l, 23);
    const res = add(h, l, s[0], s[1]);
    // t = s1 << 17
    const th = ((s[2] << 17) | (s[3] >>> 15)) >>> 0, tl = (s[3] << 17) >>> 0;
    s[4] ^= s[0]; s[5] ^= s[1];   // s2 ^= s0
    s[6] ^= s[2]; s[7] ^= s[3];   // s3 ^= s1
    s[2] ^= s[4]; s[3] ^= s[5];   // s1 ^= s2
    s[0] ^= s[6]; s[1] ^= s[7];   // s0 ^= s3
    s[4] ^= th; s[5] ^= tl;       // s2 ^= t
    [s[6], s[7]] = rotl(s[6], s[7], 45);
    return res;
  }

  random(): number {
    const [h, l] = this.next();
    return (h * 2097152 + (l >>> 11)) / 9007199254740992; // (u64 >> 11) / 2^53
  }
  uniform(lo: number, hi: number) { return lo + (hi - lo) * this.random(); }
  integers(lo: number, hi: number) { return lo + Math.floor(this.random() * (hi - lo)); }
  choice(xs: number[]) { return xs[this.integers(0, xs.length)]; }
  standardNormal(): number {
    if (this.spare !== null) { const z = this.spare; this.spare = null; return z; }
    const r = 1 - this.random();
    const theta = TAU * this.random();
    const mag = Math.sqrt(-2 * Math.log(r));
    this.spare = f32(mag * Math.sin(theta));
    return f32(mag * Math.cos(theta));
  }
}

function add(ah: number, al: number, bh: number, bl: number): [number, number] {
  const lo = al + bl;
  return [(ah + bh + (lo > 0xffffffff ? 1 : 0)) >>> 0, lo >>> 0];
}
function rotl(h: number, l: number, k: number): [number, number] {
  if (k >= 32) { [h, l] = [l, h]; k -= 32; }
  if (k === 0) return [h, l];
  return [((h << k) | (l >>> (32 - k))) >>> 0, ((l << k) | (h >>> (32 - k))) >>> 0];
}

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();
function crc32(s: string) {
  let c = 0xffffffff;
  for (let i = 0; i < s.length; i++) c = CRC_TABLE[(c ^ s.charCodeAt(i)) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

// ── lib.rs: the seed is sha256(identity), first 4 bytes big-endian ──────────────────────────

const K256 = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

/** sha256 of a UTF-8 string, first word only — all the seed needs. Works without crypto.subtle
 *  (which an http:// LAN address does not get). */
function sha256Word0(text: string): number {
  const msg = new TextEncoder().encode(text);
  const len = Math.ceil((msg.length + 9) / 64) * 64;
  const buf = new Uint8Array(len);
  buf.set(msg);
  buf[msg.length] = 0x80;
  const bits = msg.length * 8;
  const dv = new DataView(buf.buffer);
  dv.setUint32(len - 8, Math.floor(bits / 4294967296));
  dv.setUint32(len - 4, bits >>> 0);
  const H = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const w = new Uint32Array(64);
  const rr = (x: number, n: number) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < len; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + 4 * i);
    for (let i = 16; i < 64; i++) {
      const s0 = rr(w[i - 15], 7) ^ rr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rr(w[i - 2], 17) ^ rr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const t1 = (h + (rr(e, 6) ^ rr(e, 11) ^ rr(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i]) >>> 0;
      const t2 = ((rr(a, 2) ^ rr(a, 13) ^ rr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
      h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
    }
    H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
  }
  return H[0] >>> 0;
}

/** The voice seed of a duck identity (`DUCK_IDENTITY`, e.g. "duck-a"), as `sounds` derives it. */
export function seedFromId(id: string) { return sha256Word0(id); }

// ── personality.rs ──────────────────────────────────────────────────────────────────────────

export interface Personality {
  seed: number; pitch_center_hz: number; register: number; pitch_spread: number; glide_bias: number;
  brightness: number; tilt: number; nasal: number; harmonic_skew: number; formant_n: number; formant_gain: number;
  vibrato_rate_hz: number; vibrato_depth: number; jitter_depth: number; breath: number; quackiness: number;
  am_rate_hz: number; am_depth: number; warble_hz: number; warble_depth: number; attack_sharpness: number; speed: number;
}

export function personality(seed: number): Personality {
  const rng = new Rng(seed);
  const register = rng.choice([-1, 0, 0, 1]) + rng.uniform(-0.4, 0.4);
  const base = rng.uniform(160, 380);
  const pitch = Math.min(620, Math.max(110, base * 2 ** (register * 0.45)));
  return {
    seed: seed >>> 0, pitch_center_hz: pitch, register,
    pitch_spread: rng.uniform(0.4, 1.2), glide_bias: rng.uniform(-1, 1),
    brightness: rng.uniform(0.05, 0.55), tilt: rng.uniform(1.4, 2.8), nasal: rng.uniform(0.1, 1.0),
    harmonic_skew: rng.uniform(-1, 1), formant_n: rng.integers(1, 6), formant_gain: rng.uniform(0, 1.4),
    vibrato_rate_hz: rng.uniform(3.5, 9.5), vibrato_depth: rng.uniform(0, 0.7), jitter_depth: rng.uniform(0.03, 0.35),
    breath: rng.uniform(0, 0.3), quackiness: rng.uniform(0.2, 1.0), am_rate_hz: rng.uniform(18, 55),
    am_depth: rng.uniform(0.15, 0.7), warble_hz: rng.uniform(7, 18), warble_depth: rng.uniform(0, 1.4),
    attack_sharpness: rng.uniform(0, 1), speed: rng.uniform(0.82, 1.22),
  };
}

function variantRng(p: Personality, tag: string, variant: number) {
  // only the low 32 bits survive `as u32`, so 32-bit products are exact here
  const h = (Math.imul(p.seed, 1_000_003) ^ crc32(tag) ^ Math.imul(variant, 2_654_435_761 | 0)) >>> 0;
  return new Rng(h);
}

function harmonics(p: Personality): number[] {
  const N = 7, w: number[] = [];
  for (let n = 1; n <= N; n++) {
    const base = 1 / n ** p.tilt;
    const high = p.brightness * (n / N) ** 1.5;
    const nasal = p.nasal * (n === 2 || n === 3 ? 0.6 : 0);
    const skew = p.harmonic_skew >= 0 ? p.harmonic_skew * (n % 2 === 0 ? 0.4 : -0.2)
      : -p.harmonic_skew * (n % 2 === 0 ? -0.3 : 0.4);
    const formant = n === p.formant_n ? p.formant_gain : 0;
    w.push(Math.max(0, base + high + nasal + skew + formant * base * 1.5));
  }
  w[0] = Math.max(w[0], 0.7);
  return w;
}

// ── synth.rs ────────────────────────────────────────────────────────────────────────────────

type Buf = Float32Array;

function tAxis(dur: number): Buf {
  const n = Math.max(1, Math.round(dur * SR));
  const t = new Float32Array(n);
  for (let i = 0; i < n; i++) t[i] = i / SR;
  return t;
}

function lerp(t: Buf, pts: [number, number][]): Buf {
  const out = new Float32Array(t.length);
  for (let i = 0; i < t.length; i++) {
    const x = t[i];
    let v = pts[pts.length - 1][1];
    if (x <= pts[0][0]) v = pts[0][1];
    else {
      for (let k = 0; k + 1 < pts.length; k++) {
        const [x0, y0] = pts[k], [x1, y1] = pts[k + 1];
        if (x <= x1) { v = x1 <= x0 ? y1 : y0 + ((y1 - y0) * (x - x0)) / (x1 - x0); break; }
      }
    }
    out[i] = v;
  }
  return out;
}

function expdecay(t: Buf, attackS: number, decayS: number): Buf {
  const a0 = f32(Math.max(attackS, 1e-4)), d0 = f32(Math.max(decayS, 1e-4));
  const out = new Float32Array(t.length);
  for (let i = 0; i < t.length; i++) {
    const x = t[i];
    const a = Math.min(1, Math.max(0, f32(x / a0)));
    const d = f32(Math.exp(f32(-f32(Math.max(f32(x - a0), 0)) / d0)));
    out[i] = a * d;
  }
  return out;
}

function bell(t: Buf, attackS: number, releaseS: number): Buf {
  const total = t.length ? t[t.length - 1] : 0;
  const relStart = Math.max(total - releaseS, 0);
  const attack = Math.max(attackS, 1e-4), release = Math.max(releaseS, 1e-4);
  const out = new Float32Array(t.length);
  for (let i = 0; i < t.length; i++) {
    const x = t[i];
    let v = 1;
    if (x < attackS) v = x / attack;
    if (x > relStart) v = Math.max(1 - (x - relStart) / release, 0);
    out[i] = Math.min(1, Math.max(0, v));
  }
  return out;
}

function phaseFromFreq(freq: Buf): Buf {
  let acc = 0;
  const out = new Float32Array(freq.length);
  for (let i = 0; i < freq.length; i++) { acc += freq[i]; out[i] = (TAU * acc) / SR; }
  return out;
}

function harmonicOsc(phase: Buf, weights: number[]): Buf {
  const out = new Float32Array(phase.length);
  weights.forEach((w0, k) => {
    if (w0 === 0) return;
    const n = k + 1, w = f32(w0);
    for (let i = 0; i < phase.length; i++) out[i] = f32(out[i] + f32(w * f32(Math.sin(f32(n * phase[i])))));
  });
  return out;
}

function vibrato(t: Buf, rate: number, depth: number, phase: number): Buf {
  const out = new Float32Array(t.length);
  if (rate <= 0 || depth <= 0) return out.fill(1);
  for (let i = 0; i < t.length; i++) out[i] = 2 ** ((depth * Math.sin(TAU * rate * t[i] + phase)) / 12);
  return out;
}

function movingAverageSame(x: Buf, k: number): Buf {
  const n = x.length, out = new Float32Array(n), offset = (k - 1) >> 1, inv = f32(1 / k);
  let acc = 0, hi = 0;
  for (let i = 0; i < n; i++) {
    const wantHi = Math.min(i + offset + 1, n);
    while (hi < wantHi) { acc = f32(acc + x[hi]); hi++; }
    const lo = Math.max(0, i + offset + 1 - k);
    if (i > 0) {
      const prevLo = Math.max(0, i + offset - k);
      for (let j = prevLo; j < lo; j++) acc = f32(acc - x[j]);
    }
    out[i] = acc * inv;
  }
  return out;
}

function jitter(t: Buf, depth: number, rng: Rng): Buf {
  if (depth <= 0) return new Float32Array(t.length).fill(1);
  const raw = new Float32Array(t.length);
  for (let i = 0; i < raw.length; i++) raw[i] = rng.standardNormal();
  const window = Math.max(1, Math.round((64 * SR) / TUNED_SR));
  const sm = movingAverageSame(raw, window);
  for (let i = 0; i < sm.length; i++) sm[i] = 2 ** ((depth * sm[i]) / 12);
  return sm;
}

function pinkNoise(n: number, rng: Rng): Buf {
  const a = f32(0.985 ** (TUNED_SR / SR));
  let leak = 0;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) { leak = f32(f32(a * leak) + rng.standardNormal()); out[i] = leak; }
  let peak = 0;
  for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(out[i]));
  peak = f32(peak + f32(1e-9));
  for (let i = 0; i < n; i++) out[i] = out[i] / peak;
  return out;
}

function click(n: number, rng: Rng, length: number): Buf {
  const out = new Float32Array(n), l = Math.min(length, n);
  for (let i = 0; i < l; i++) {
    const fade = f32(1 - f32(i / l));
    out[i] = f32(f32(f32(rng.uniform(-1, 1)) * fade) * fade);
  }
  return out;
}

function normalise(x: Buf, peakDbfs: number) {
  let peak = 0;
  for (let i = 0; i < x.length; i++) peak = Math.max(peak, Math.abs(x[i]));
  peak = f32(peak + f32(1e-9));
  const gain = f32(f32(10 ** (peakDbfs / 20)) / peak);
  for (let i = 0; i < x.length; i++) x[i] = x[i] * gain;
}

function mul(a: Buf, b: Buf): Buf { const o = new Float32Array(a.length); for (let i = 0; i < a.length; i++) o[i] = a[i] * b[i]; return o; }
function concat(...parts: Buf[]): Buf {
  const o = new Float32Array(parts.reduce((s, p) => s + p.length, 0));
  let k = 0;
  for (const p of parts) { o.set(p, k); k += p.length; }
  return o;
}

// ── voices.rs ───────────────────────────────────────────────────────────────────────────────

function attack(p: Personality, dur: number, snappy: number) {
  const soft = 0.04 * dur, sharp = 0.003 * dur;
  return Math.max(soft + (sharp - soft) * p.attack_sharpness * snappy, 0.001);
}

function voice(p: Personality, t: Buf, freq: Buf, rng: Rng, amScale: number, breathScale: number): Buf {
  const vib = vibrato(t, p.vibrato_rate_hz, p.vibrato_depth, rng.uniform(0, TAU));
  const jit = jitter(t, p.jitter_depth, rng);
  const f = new Float32Array(t.length);
  for (let i = 0; i < f.length; i++) f[i] = f32(freq[i] * vib[i]) * jit[i];
  const body = harmonicOsc(phaseFromFreq(f), harmonics(p));
  const amD = p.am_depth * amScale * p.quackiness;
  if (amD > 0.01) {
    for (let i = 0; i < body.length; i++) body[i] = body[i] * f32(1 - amD * (0.5 + 0.5 * Math.sin(TAU * p.am_rate_hz * t[i])));
  }
  const breath = p.breath * breathScale;
  if (breath > 0) {
    const n = pinkNoise(t.length, rng), b = f32(breath);
    for (let i = 0; i < body.length; i++) body[i] = body[i] + f32(b * n[i]);
  }
  return body;
}

function alarm(p: Personality, variant: number): Buf {
  const rng = variantRng(p, "alarm", variant);
  const dur = (0.2 + 0.12 * rng.random()) / p.speed;
  const t = tAxis(dur);
  const f0 = p.pitch_center_hz * (1.25 + 0.35 * p.pitch_spread) * (0.94 + 0.12 * rng.random());
  const peakMul = 1.15 + 0.25 * p.pitch_spread + 0.1 * rng.random();
  const fallMul = 0.75 + 0.2 * (1 - p.pitch_spread);
  const freq = lerp(t, [[0, f0], [0.05 * dur, f0 * peakMul], [dur, f0 * fallMul]]);
  const env = expdecay(t, attack(p, dur, 1), dur * (0.4 + 0.2 * rng.random()));
  const sig = mul(voice(p, t, freq, rng, 0.5, 1), env);
  const crackle = f32(0.04 + 0.1 * p.brightness);
  for (let i = 0; i < sig.length; i++) sig[i] = sig[i] + f32(f32(crackle * rng.standardNormal()) * env[i]);
  normalise(sig, -3);
  return sig;
}

function greetSyllable(p: Personality, rng: Rng, durScale: number, f0Scale: number): Buf {
  const dur = ((0.32 + 0.25 * rng.random()) * durScale) / p.speed;
  const t = tAxis(dur);
  const f0 = p.pitch_center_hz * (0.9 + 0.15 * rng.random()) * f0Scale;
  const bias = p.glide_bias, bend = 0.1 + 0.15 * p.pitch_spread;
  const start = f0 * (1 - bias * bend * 0.5), mid = f0 * (1 + bias * bend);
  const end = f0 * (1 - bias * bend * 0.3) * (0.92 + 0.08 * rng.random());
  const freq = lerp(t, [[0, start], [0.18 * dur, mid], [dur, end]]);
  const env = expdecay(t, attack(p, dur, 0.5), dur * 0.7);
  return mul(voice(p, t, freq, rng, 1, 1), env);
}

function greet(p: Personality, variant: number): Buf {
  const rng = variantRng(p, "greet", variant);
  let sig = greetSyllable(p, rng, 1, 1);
  if (rng.random() < 0.4) {
    const gap = Math.floor(((0.05 + 0.06 * rng.random()) / p.speed) * SR);
    const f0Scale = 0.95 + 0.06 * rng.random();
    sig = concat(sig, new Float32Array(gap), greetSyllable(p, rng, 0.8, f0Scale));
  }
  normalise(sig, -3);
  return sig;
}

function inquire(p: Personality, variant: number): Buf {
  const rng = variantRng(p, "inquire", variant);
  const dur = (0.42 + 0.25 * rng.random()) / p.speed;
  const t = tAxis(dur);
  const f0 = p.pitch_center_hz * (0.88 + 0.1 * rng.random());
  const rise = 1.15 + 0.5 * p.pitch_spread + 0.2 * Math.max(p.glide_bias, 0) + 0.1 * rng.random();
  const freq = lerp(t, [[0, f0 * 0.92], [0.3 * dur, f0 * 0.95], [dur, f0 * rise]]);
  const env = bell(t, dur * (0.06 + 0.1 * (1 - p.attack_sharpness)), dur * 0.32);
  const sig = mul(voice(p, t, freq, rng, 0.6, 1), env);
  normalise(sig, -3);
  return sig;
}

function peck(p: Personality, variant: number): Buf {
  const rng = variantRng(p, "peck", variant);
  const dur = (0.16 + 0.12 * rng.random()) / p.speed;
  const t = tAxis(dur);
  const f0 = p.pitch_center_hz * (0.45 + 0.2 * rng.random());
  const freq = lerp(t, [[0, f0 * 1.5], [0.04 * dur, f0], [dur, f0 * 0.8]]);
  const env = expdecay(t, attack(p, dur, 1), dur * 0.35);
  const body = mul(voice(p, t, freq, rng, 0.3, 0.5), env);
  const clickLen = Math.floor((0.003 + 0.006 * p.attack_sharpness) * SR);
  const gain = f32(0.4 + 0.4 * p.attack_sharpness);
  const c = click(t.length, rng, clickLen);
  for (let i = 0; i < body.length; i++) body[i] = body[i] + f32(gain * c[i]);
  normalise(body, -3);
  return body;
}

function chirpSyllable(p: Personality, rng: Rng, f0: number, dur: number, contour: [number, number][]): Buf {
  const t = tAxis(dur);
  const warble = vibrato(t, p.warble_hz, p.warble_depth, rng.uniform(0, TAU));
  const shape = lerp(t, contour);
  const freq = new Float32Array(t.length), ff0 = f32(f0);
  for (let i = 0; i < freq.length; i++) freq[i] = f32(ff0 * shape[i]) * warble[i];
  const env = expdecay(t, attack(p, dur, 0.7), dur * 0.55);
  const soft = { ...p, quackiness: p.quackiness * 0.2, brightness: p.brightness * 0.5, formant_gain: p.formant_gain * 0.5 };
  return mul(voice(soft, t, freq, rng, 1, 0.4), env);
}

function chirp(p: Personality, variant: number): Buf {
  const rng = variantRng(p, "chirp", variant);
  const shape = variant % 4;
  const f0 = p.pitch_center_hz * (0.95 + 0.75 * rng.random());
  let sig: Buf;
  if (shape === 0) {
    const dur = (0.1 + 0.1 * rng.random()) / p.speed, peakV = 1.12 + 0.1 * rng.random();
    sig = chirpSyllable(p, rng, f0, dur, [[0, 0.88], [0.5 * dur, peakV], [dur, 1.05]]);
  } else if (shape === 1) {
    const dur = (0.1 + 0.1 * rng.random()) / p.speed, start = 1.12 + 0.1 * rng.random();
    sig = chirpSyllable(p, rng, f0, dur, [[0, start], [0.3 * dur, 1], [dur, 0.78]]);
  } else if (shape === 2) {
    const dur = (0.22 + 0.14 * rng.random()) / p.speed;
    const trill = { ...p, warble_depth: Math.max(p.warble_depth, 0.7) * 1.6 };
    sig = chirpSyllable(trill, rng, f0, dur, [[0, 1], [0.5 * dur, 1.06], [dur, 0.94]]);
  } else {
    const dur = (0.08 + 0.05 * rng.random()) / p.speed;
    const a = chirpSyllable(p, rng, f0, dur, [[0, 0.95], [0.4 * dur, 1.1], [dur, 0.88]]);
    const gap = Math.floor(((0.03 + 0.03 * rng.random()) / p.speed) * SR);
    const durB = dur * 0.9, f0B = f0 * (0.92 + 0.1 * rng.random());
    sig = concat(a, new Float32Array(gap), chirpSyllable(p, rng, f0B, durB, [[0, 0.95], [0.4 * durB, 1.1], [durB, 0.88]]));
  }
  normalise(sig, -6);
  return sig;
}

function coo(p: Personality, variant: number): Buf {
  const rng = variantRng(p, "coo", variant);
  const dur = (0.85 + 0.55 * rng.random()) / p.speed;
  const t = tAxis(dur);
  const f0 = p.pitch_center_hz * (0.42 + 0.15 * (1 - p.attack_sharpness)) * (0.94 + 0.12 * rng.random());
  const driftA = 1 + 0.05 * rng.random() + 0.04 * p.glide_bias;
  const freq = lerp(t, [[0, f0 * 0.94], [dur * 0.5, f0 * driftA], [dur, f0 * 0.9]]);
  const env = bell(t, dur * (0.18 + 0.1 * (1 - p.attack_sharpness)), dur * 0.3);
  const soft = {
    ...p, breath: Math.max(p.breath, 0.12) + 0.1, quackiness: p.quackiness * 0.25, am_rate_hz: p.am_rate_hz * 0.3,
    vibrato_rate_hz: p.vibrato_rate_hz * 0.45, vibrato_depth: p.vibrato_depth * 0.7,
  };
  const sig = mul(voice(soft, t, freq, rng, 1, 1), env);
  normalise(sig, -5);
  return sig;
}

/** (start, loop, end) of the roller-blade joy ride; `wheee` plays start + loop×2 + end. */
export function wheeeSegments(p: Personality, variant: number): [Buf, Buf, Buf] {
  const rng = variantRng(p, "wheee", variant);
  const dStart = (0.8 + 0.3 * rng.random()) / p.speed;
  const dLoop = (1.6 + 0.6 * rng.random()) / p.speed;
  const dEnd = (0.55 + 0.25 * rng.random()) / p.speed;
  const total = dStart + dLoop + dEnd;
  const t = tAxis(total);
  const t1 = dStart, t2 = dStart + dLoop;
  const f0 = p.pitch_center_hz * (0.95 + 0.1 * rng.random());
  const top = 1.6 + 0.5 * p.pitch_spread + 0.25 * rng.random();
  const base = lerp(t, [[0, f0 * 0.85], [0.15 * dStart, f0], [t1, f0 * top], [t2, f0 * top], [t2 + 0.25 * dEnd, f0 * top * 1.04], [total, f0 * 0.6]]);
  const wobHz = 4.5 + 3 * rng.random();
  const swell = lerp(t, [[0, 0.15], [t1, 1], [total, 1]]);
  const freq = new Float32Array(t.length);
  for (let i = 0; i < t.length; i++) freq[i] = base[i] * f32(2 ** ((0.5 * swell[i] * Math.sin(TAU * wobHz * t[i])) / 12));
  const env = lerp(t, [[0, 0], [Math.min(0.06, 0.5 * dStart), 1], [t2 + 0.4 * dEnd, 1], [total, 0]]);
  const joy = { ...p, vibrato_depth: p.vibrato_depth * 0.5, quackiness: p.quackiness * 0.5 };
  const sig = mul(voice(joy, t, freq, rng, 0.5, 0.5), env);
  normalise(sig, -4);
  const n1 = Math.floor(t1 * SR), n2 = Math.floor(t2 * SR);
  const start = sig.slice(0, n1), loop = sig.slice(n1, n2), end = sig.slice(n2);
  const nx = Math.min(Math.floor(0.08 * SR), loop.length >> 1, n1), len = loop.length;
  for (let i = 0; i < nx; i++) {
    const w = f32(i / Math.max(Math.max(nx, 1) - 1, 1));
    const tail = len - nx + i;
    loop[tail] = f32((1 - w) * loop[tail]) + f32(w * sig[n1 - nx + i]);
  }
  return [start, loop, end];
}

function wheee(p: Personality, variant: number): Buf {
  const [s, l, e] = wheeeSegments(p, variant);
  return concat(s, l, l, e);
}

export const TAGS = { alarm, greet, inquire, peck, chirp, coo, wheee } as const;
export type Tag = keyof typeof TAGS;
/** Variants per tag, as lib.rs `variant_count`. */
export function variantCount(tag: Tag) { return tag === "greet" || tag === "chirp" ? 12 : tag === "wheee" ? 6 : 10; }

export function render(tag: Tag, p: Personality, variant: number): Float32Array { return TAGS[tag](p, variant); }

// ── playback ────────────────────────────────────────────────────────────────────────────────

let ctx: AudioContext | null = null;
let master: GainNode | null = null;
let volume = 0.8;
let muted = false;
/** Paused by the listener (pauseVoice), as opposed to suspended by the browser's autoplay rule. */
let paused = false;
const listeners = new Set<() => void>();
const changed = () => listeners.forEach((l) => l());
/** Called whenever speaking, pausing or the audio context's state may have changed. */
export function onVoiceChange(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; }
const personalities = new Map<string, Personality>();
const cache = new Map<string, AudioBuffer>();
const envelopes = new Map<string, Float32Array>();
const busyUntil = new Map<string, number>();

// What each duck is saying right now: every scheduled sound with its start, end and loudness
// envelope, so the beak can open with the audio it is actually playing (`voiceLevel`).
const ENV_HZ = 60;
interface Voiced { src: AudioBufferSourceNode; start: number; end: number; env: Float32Array; gain: number }
const voiced = new Map<string, Voiced[]>();

// The loudest RMS window of a full-voiced syllable (greet, alarm, inquire peak at 0.42; a chirp,
// rendered 3 dB down, at 0.3; a coo at 0.33), so a chirp opens the beak less than a greeting.
const FULL_RMS = 0.42;

/** RMS loudness in 1/ENV_HZ windows, 1 for a full-voiced syllable. */
export function loudness(pcm: Float32Array, sr = SR): Float32Array {
  const win = Math.max(1, Math.round(sr / ENV_HZ));
  const out = new Float32Array(Math.ceil(pcm.length / win));
  for (let k = 0; k < out.length; k++) {
    let s = 0;
    const end = Math.min(pcm.length, (k + 1) * win);
    for (let i = k * win; i < end; i++) s += pcm[i] * pcm[i];
    out[k] = Math.min(1, Math.sqrt(s / Math.max(1, end - k * win)) / FULL_RMS);
  }
  return out;
}

function audio() {
  if (!ctx) {
    ctx = new AudioContext();
    master = ctx.createGain();
    const comp = ctx.createDynamicsCompressor(); // several ducks at once must not clip
    comp.threshold.value = -10; comp.ratio.value = 4;
    master.connect(comp).connect(ctx.destination);
    master.gain.value = muted ? 0 : volume;
    ctx.onstatechange = changed;
  }
  if (ctx.state === "suspended" && !paused) void ctx.resume().catch(() => undefined);
  return ctx;
}

export function setVoiceVolume(v: number) { volume = Math.max(0, Math.min(1, v)); if (master) master.gain.value = muted ? 0 : volume; }
export function setVoiceMuted(m: boolean) { muted = m; if (master) master.gain.value = m ? 0 : volume; }
/** Browsers start audio only after a user gesture; call from one. */
export function unlockVoice() { try { audio(); } catch { /* no audio */ } }

// The first click or key anywhere on the page counts as that gesture, so a duck that speaks before
// anyone pressed Send (a greeting, idle chatter) is heard from the first interaction on.
if (typeof window !== "undefined") {
  const first = () => { unlockVoice(); window.removeEventListener("pointerdown", first); window.removeEventListener("keydown", first); };
  window.addEventListener("pointerdown", first);
  window.addEventListener("keydown", first);
}

/** Hold every duck's voice where it is (the audio clock stops; the beak shuts). */
export function pauseVoice() {
  if (!ctx || paused) return;
  paused = true;
  void ctx.suspend().catch(() => undefined);
  changed();
}

/** Carry on from where pauseVoice stopped. */
export function resumeVoice() {
  if (!paused) return;
  paused = false;
  if (ctx) void ctx.resume().catch(() => undefined);
  changed();
}

export function voicePaused() { return paused; }

/** The browser is holding the audio back until the page is interacted with (autoplay rule). */
export function voiceBlocked() { return !!ctx && ctx.state === "suspended" && !paused; }

export function personalityOf(id: string) {
  let p = personalities.get(id);
  if (!p) { p = personality(seedFromId(id)); personalities.set(id, p); }
  return p;
}

function buffer(id: string, tag: Tag, variant: number) {
  const key = `${id}/${tag}/${variant}`;
  let b = cache.get(key);
  if (!b) {
    const pcm = render(tag, personalityOf(id), variant);
    b = audio().createBuffer(1, pcm.length, SR);
    b.copyToChannel(pcm as Float32Array, 0);
    cache.set(key, b);
    envelopes.set(key, loudness(pcm));
  }
  return { b, env: envelopes.get(key)! };
}

/** `gap`: silence, in seconds, after whatever the duck is already saying (between sentences). */
export interface Placement { pan?: number; gain?: number; delay?: number; gap?: number }

/** Play one tag in a duck's voice; returns when it will have finished (AudioContext seconds). */
export function play(id: string, tag: Tag, variant?: number, at: Placement = {}): number {
  if (muted) return 0;
  try {
    const c = audio();
    const v = variant ?? Math.floor(Math.random() * variantCount(tag));
    const src = c.createBufferSource();
    const { b, env } = buffer(id, tag, v % variantCount(tag));
    src.buffer = b;
    const g = c.createGain();
    g.gain.value = at.gain ?? 1;
    const pan = c.createStereoPanner();
    pan.pan.value = Math.max(-1, Math.min(1, at.pan ?? 0));
    src.connect(g).connect(pan).connect(master!);
    const busy = busyUntil.get(id);
    const start = Math.max(c.currentTime + (at.delay ?? 0), busy !== undefined ? busy + (at.gap ?? 0) : 0);
    src.start(start);
    const end = start + b.duration;
    busyUntil.set(id, end + 0.06);
    const entry: Voiced = { src, start, end, env, gain: Math.min(1, at.gain ?? 1) };
    voiced.set(id, [...(voiced.get(id) ?? []), entry]);
    src.onended = () => {
      const left = (voiced.get(id) ?? []).filter((e) => e !== entry);
      if (left.length) voiced.set(id, left); else voiced.delete(id);
      changed();
    };
    changed();
    return end;
  } catch {
    return 0;
  }
}

/** How loud duck `id` is being heard right now, 0..1 — 0 when silent, muted, stopped or failed. */
export function voiceLevel(id: string): number {
  const list = voiced.get(id);
  // a paused or browser-suspended context is silent: the clock stands still, so the beak shuts
  if (!ctx || !list || muted || volume === 0 || ctx.state !== "running") return 0;
  // currentTime runs ahead of the speaker by the output latency; follow what is heard
  const t = ctx.currentTime - (ctx.outputLatency || ctx.baseLatency || 0);
  for (const e of list) {
    if (t < e.start || t >= e.end) continue;
    return e.env[Math.min(e.env.length - 1, Math.floor((t - e.start) * ENV_HZ))] * e.gain;
  }
  return 0;
}

/** Is anything of duck `id`'s still scheduled or playing? */
export function isSpeaking(id: string): boolean {
  return !!ctx && (voiced.get(id) ?? []).some((e) => ctx!.currentTime < e.end);
}

/** Cut duck `id` off mid-sentence: everything it was saying or about to say. */
export function stopVoice(id: string) {
  for (const e of voiced.get(id) ?? []) {
    e.src.onended = null;
    try { e.src.stop(); } catch { /* never started */ }
  }
  voiced.delete(id);
  busyUntil.delete(id);
  changed();
}

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

// What a mood sounds like, in the robot's own tags. Babble words add chirps, so a long
// sentence is a longer utterance; the same sentence always picks the same variants.
const LEAD: Record<string, Tag[]> = {
  happy: ["greet"], proud: ["greet", "chirp"], curious: ["inquire"], alert: ["alarm", "alarm"],
  sorry: ["coo"], sleepy: ["coo"], ramble: ["chirp"],
};

/** Say a chat reply aloud in duck `id`'s voice. */
export function speak(id: string, mood = "ramble", babble = "", at: Placement = {}) {
  const words = babble.replace(/[.!?]/g, "").split(/\s+/).filter(Boolean);
  const h = hash(`${mood}:${babble}`);
  const tags: Tag[] = [...(LEAD[mood] ?? LEAD.ramble)];
  const extra = Math.min(3, Math.round(words.length / 5));
  for (let i = 0; i < extra; i++) tags.splice(mood === "curious" ? 0 : tags.length, 0, "chirp");
  let end = 0;
  tags.forEach((tag, i) => { end = play(id, tag, (h >>> (i * 3)) % variantCount(tag), { ...at, delay: i ? 0.04 : at.delay, gap: i ? 0 : at.gap }); });
  return end;
}
