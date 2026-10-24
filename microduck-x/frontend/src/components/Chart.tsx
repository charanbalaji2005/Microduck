import { useEffect, useRef } from "react";

export interface Series { label: string; color: string; values: (number | null)[]; dashed?: boolean }

// A small canvas line chart. Redrawn on demand, no animation, gaps where data is missing
// (a missing value is drawn as a gap, never as zero).
export default function Chart({ series, height = 120, unit = "", min, max, title, seconds }: {
  series: Series[]; height?: number; unit?: string; min?: number; max?: number; title?: string; seconds?: number;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const dpr = Math.min(window.devicePixelRatio, 2);
    const w = c.clientWidth, h = height;
    c.width = w * dpr; c.height = h * dpr;
    const g = c.getContext("2d")!;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);
    const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null && Number.isFinite(v)));
    let lo = min ?? (all.length ? Math.min(...all) : 0), hi = max ?? (all.length ? Math.max(...all) : 1);
    if (hi - lo < 1e-6) { hi += 0.5; lo -= 0.5; }
    const pad = (hi - lo) * 0.08; if (min === undefined) lo -= pad; if (max === undefined) hi += pad;
    const L = 40, R = 8, T = title ? 18 : 6, B = 16;
    const x = (i: number, n: number) => L + ((w - L - R) * i) / Math.max(1, n - 1);
    const y = (v: number) => T + (h - T - B) * (1 - (v - lo) / (hi - lo));
    g.font = "10px 'JetBrains Mono', monospace";
    g.strokeStyle = "rgba(140,155,170,0.14)"; g.fillStyle = "#7d8a98"; g.lineWidth = 1;
    for (let k = 0; k <= 3; k++) {
      const v = lo + ((hi - lo) * k) / 3, yy = y(v);
      g.beginPath(); g.moveTo(L, yy); g.lineTo(w - R, yy); g.stroke();
      g.fillText(fmt(v), 2, yy + 3);
    }
    if (lo < 0 && hi > 0) { g.strokeStyle = "rgba(160,175,190,0.35)"; g.beginPath(); g.moveTo(L, y(0)); g.lineTo(w - R, y(0)); g.stroke(); }
    if (title) { g.fillStyle = "#aeb9c5"; g.font = "600 11px Inter, sans-serif"; g.fillText(title + (unit ? ` (${unit})` : ""), L, 12); }
    if (seconds) { g.fillStyle = "#6b7785"; g.font = "10px 'JetBrains Mono', monospace"; g.fillText(`-${seconds}s`, L, h - 3); g.fillText("now", w - R - 22, h - 3); }
    let lx = w - R;
    g.font = "10px Inter, sans-serif";
    for (const s of [...series].reverse()) {
      const last = [...s.values].reverse().find((v) => v !== null && Number.isFinite(v));
      const text = `${s.label} ${last === undefined || last === null ? "–" : fmt(last)}`;
      const tw = g.measureText(text).width;
      lx -= tw + 14;
      if (title) { g.fillStyle = s.color; g.fillRect(lx, 6, 8, 3); g.fillStyle = "#aeb9c5"; g.fillText(text, lx + 11, 12); }
    }
    for (const s of series) {
      g.strokeStyle = s.color; g.lineWidth = 1.6; g.setLineDash(s.dashed ? [4, 3] : []);
      g.beginPath();
      let pen = false;
      s.values.forEach((v, i) => {
        if (v === null || !Number.isFinite(v)) { pen = false; return; }
        const px = x(i, s.values.length), py = y(Math.max(lo, Math.min(hi, v)));
        if (pen) g.lineTo(px, py); else { g.moveTo(px, py); pen = true; }
      });
      g.stroke();
    }
    g.setLineDash([]);
  });
  return <canvas ref={ref} className="chart" style={{ height }} />;
}

function fmt(v: number) {
  const a = Math.abs(v);
  return a >= 100 ? v.toFixed(0) : a >= 10 ? v.toFixed(1) : v.toFixed(2);
}
