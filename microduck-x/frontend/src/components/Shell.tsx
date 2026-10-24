import { useMemo, useState } from "react";
import { cancelAll, clearEstop, command, estop } from "../lib/api";
import { JOINT_LABELS, live, setUi, useStore } from "../lib/store";
import type { Execution } from "../lib/types";
import Chart from "./Chart";
import VoiceMini from "../voice/VoiceMini";

export const PAGES: [string, string, string][] = [
  ["hangar", "Hangar (duck view)", "✦"], ["overview", "Mission Control", "◎"], ["live", "Live Simulation", "▣"], ["voice", "Voice Control", "◉"],
  ["environments", "Environments", "◫"], ["telemetry", "Robot Telemetry", "∿"], ["navigation", "Navigation", "⌖"],
  ["experiments", "Experiments", "⚗"], ["policy", "Policy Information", "⌬"], ["logs", "Logs", "≡"], ["settings", "Settings", "⚙"],
];

export function Sidebar() {
  const page = useStore((s) => s.ui.page);
  return (
    <nav className="sidebar">
      <div className="brand"><span className="brand-mark">μ</span><div><div className="brand-name">Microduck X</div><div className="brand-sub">virtual robotics lab</div></div></div>
      {PAGES.map(([id, label, icon]) => (
        <button key={id} className={`nav-item ${page === id ? "active" : ""}`} onClick={() => setUi({ page: id })}>
          <span className="nav-icon">{icon}</span>{label}
        </button>
      ))}
      <div className="sidebar-foot">
        <div>Keys: <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd> drive · <kbd>Q</kbd><kbd>E</kbd> strafe</div>
        <div><kbd>Space</kbd> hold to talk · <kbd>Esc</kbd> e-stop · <kbd>1</kbd>–<kbd>4</kbd> camera · <kbd>P</kbd> pause</div>
      </div>
    </nav>
  );
}

function Pill({ ok, warn, label, value, title }: { ok: boolean; warn?: boolean; label: string; value: string; title?: string }) {
  return <div className={`pill ${ok ? (warn ? "warn" : "ok") : "bad"}`} title={title}><span className="dot" />{label}<b>{value}</b></div>;
}

export function StatusBar() {
  const st = useStore((s) => s.status);
  const ws = useStore((s) => s.wsConnected);
  const r = st?.robotd, sim = st?.sim, safety = st?.safety;
  const fixture = r?.walk && !["velstand.onnx", "alpha_walking.onnx"].includes(r.walk);
  return (
    <header className="statusbar">
      <Pill ok={ws} label="dashboard" value={ws ? "linked" : "offline"} />
      <Pill ok={!!r?.connected} label="robotd" value={r?.connected ? `${r.loop_hz?.toFixed(1) ?? "?"} Hz` : "down"} title={r?.error ?? r?.socket} />
      <Pill ok={!!r?.connected && r?.policy !== "held"} warn={!!fixture} label="policy" value={r?.policy ?? "–"}
        title={r?.policy_unavailable ?? `walk slot: ${r?.walk ?? "?"}`} />
      <Pill ok={!!sim?.connected} warn={!!sim?.paused} label="MuJoCo" value={sim?.connected ? (sim.paused ? "paused" : `${sim.rtf?.toFixed(2) ?? "?"}× rt`) : "down"} title={sim?.error ?? sim?.address} />
      <Pill ok={!!sim?.env} label="scene" value={sim?.env ?? "–"} />
      <Pill ok={!!safety?.motion_allowed} warn={!safety?.estop && !safety?.motion_allowed} label="motion" value={safety?.estop ? "E-STOP" : safety?.motion_allowed ? "allowed" : "blocked"}
        title={safety?.blockers.join("\n")} />
      {st?.mock && <div className="pill bad"><span className="dot" />MOCK MODE<b>not the Microduck</b></div>}
      {fixture && <div className="pill warn" title="The walk slot holds a file that is not the official velstand gait"><span className="dot" />walk slot<b>{r?.walk}</b></div>}
      <div className="spacer" />
      <EStopButton />
    </header>
  );
}

export function EStopButton() {
  const latched = useStore((s) => !!s.status?.safety.estop);
  return latched ? (
    <button className="estop latched" onClick={() => clearEstop()} title="Emergency stop is latched. Click to clear.">E-STOP LATCHED · clear</button>
  ) : (
    <button className="estop" onClick={() => estop()} title="Emergency stop (Esc). Independent of voice and language models.">EMERGENCY STOP</button>
  );
}

export function ControlBar() {
  const st = useStore((s) => s.status);
  const paused = !!st?.sim.paused;
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (label: string, cmd: Record<string, unknown>) => {
    setBusy(label);
    try { await command(cmd); } finally { setBusy(null); }
  };
  return (
    <div className="controlbar">
      <button onClick={() => run("enable", { action: "enable" })} disabled={!!busy}>Stand up / enable</button>
      <button onClick={() => run("sit", { action: "skill", skill: "sit_toggle" })} disabled={!!busy}>Sit ⇄ stand</button>
      <button onClick={() => run("disable", { action: "disable" })} disabled={!!busy}>Disable policy</button>
      <span className="sep" />
      <button onClick={() => run("pause", { action: paused ? "resume" : "pause" })} disabled={!!busy}>{paused ? "Resume physics" : "Pause physics"}</button>
      <button onClick={() => run("reset", { action: "reset", keyframe: "SIT" })} disabled={!!busy}>Reset world</button>
      <button onClick={() => run("push", { action: "push", direction: "left", newtons: 3 })} disabled={!!busy}>Shove 3 N</button>
      <span className="sep" />
      <button onClick={() => cancelAll()}>Cancel task</button>
      {busy && <span className="muted small">sending {busy}…</span>}
    </div>
  );
}

const STATUS_CLASS: Record<string, string> = { completed: "ok", delivered: "ok", running: "run", queued: "run", failed: "bad", rejected: "bad", cancelled: "warn", superseded: "warn" };

export function CommandRow({ e }: { e: Execution }) {
  return (
    <div className="cmd-row">
      <span className={`tag ${STATUS_CLASS[e.status] ?? ""}`}>{e.status}</span>
      <b>{e.action}</b>
      <span className="muted small">{e.source}{e.transcript ? ` · “${e.transcript}”` : ""}</span>
      <span className="cmd-msg">{e.message}</span>
    </div>
  );
}

export function CurrentAction() {
  const st = useStore((s) => s.status);
  const act = st?.activity;
  const blockers = st?.safety.blockers ?? [];
  const refused = useStore((s) => s.teleopRefused);
  return (
    <section className="card">
      <h3>Current action</h3>
      {act ? <CommandRow e={act} /> : <div className="muted">idle</div>}
      {refused && <div className="warnbox">Keyboard drive refused: {refused.join("; ")}</div>}
      {blockers.length > 0 && <div className="warnbox"><b>Motion blocked</b><ul>{blockers.map((b) => <li key={b}>{b}</li>)}</ul></div>}
    </section>
  );
}

export function TelemetryMini() {
  useStore((s) => s.tick);
  const f = live.frame, r = live.robot, d = f?.ducks?.[0];
  const deg = (v: number | null | undefined) => (v === null || v === undefined ? "–" : ((v * 180) / Math.PI).toFixed(0));
  const tilt = d ? (Math.acos(Math.max(-1, Math.min(1, -d.gravity[2]))) * 180) / Math.PI : null;
  return (
    <section className="card">
      <h3>Robot <span className="muted small">ground truth · robotd</span></h3>
      <div className="kv mono">
        <span>pos</span><b>{d ? `${d.trunk[0].toFixed(2)}, ${d.trunk[1].toFixed(2)}, ${d.trunk[2].toFixed(3)} m` : "–"}</b>
        <span>yaw / tilt</span><b>{d ? `${deg(d.yaw)}° / ${tilt!.toFixed(0)}°` : "–"}</b>
        <span>v world</span><b>{d ? `${d.linvel.map((v) => v.toFixed(2)).join(", ")}` : "–"}</b>
        <span>feet L/R</span><b>{d ? `${d.feet.left ? "●" : "○"} ${d.feet.right ? "●" : "○"}  · ${d.external_contacts} contacts` : "–"}</b>
        <span>policy</span><b>{r?.policy ?? "–"}</b>
        <span>twist req</span><b>{r ? r.move.requested.map((v) => v.toFixed(2)).join(", ") : "–"}</b>
        <span>twist applied</span><b>{r ? r.move.applied.map((v) => v.toFixed(2)).join(", ") : "–"}</b>
        <span>limited by</span><b>{r?.move.limited_by?.join(", ") || "–"}</b>
        <span>odom (robot)</span><b>{r ? `${r.odom.position[0].toFixed(2)}, ${r.odom.position[1].toFixed(2)} · ${deg(r.odom.yaw)}°` : "–"}</b>
        <span>fallen (robot)</span><b>{r ? String(r.safety.fallen) : "–"}</b>
      </div>
    </section>
  );
}

export function JointTable({ velocities = false }: { velocities?: boolean }) {
  useStore((s) => s.tick);
  const d = live.frame?.ducks?.[0], r = live.robot;
  return (
    <table className="joints mono">
      <thead><tr><th>joint</th><th>sim q°</th><th>robotd q°</th><th>target°</th>{velocities && <th>sim q̇ °/s</th>}{velocities && <th>robotd q̇</th>}</tr></thead>
      <tbody>
        {JOINT_LABELS.map((n, i) => {
          const sq = d?.q[i], rq = r?.joints[i], tq = r?.targets[i];
          const f = (v: number | null | undefined) => (v === null || v === undefined ? "–" : ((v * 180) / Math.PI).toFixed(1));
          return (
            <tr key={n} className={i === 9 ? "muted" : ""}>
              <td>{n}</td><td>{f(sq)}</td><td>{f(rq)}</td><td>{f(tq)}</td>
              {velocities && <td>{f(d?.qd[i])}</td>}{velocities && <td>{f(r?.velocities?.[i])}</td>}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function RecentEvents({ limit = 12 }: { limit?: number }) {
  const events = useStore((s) => s.events);
  return (
    <div className="events">
      {events.slice(-limit).reverse().map((e) => (
        <div key={e.id} className={`event ${e.level}`}>
          <span className="mono muted">{new Date(e.t * 1000).toLocaleTimeString([], { hour12: false })}</span>
          <span className="kind">{e.kind}</span>
          <span>{e.message}</span>
        </div>
      ))}
    </div>
  );
}

export function RightPanel() {
  return (
    <aside className="rightpanel">
      <VoiceMini />
      <CurrentAction />
      <TelemetryMini />
      <section className="card"><h3>Recent events</h3><RecentEvents /></section>
    </aside>
  );
}

export function useSeries(seconds = 30) {
  useStore((s) => s.tick);
  return useMemo(() => {
    const n = seconds * 10;
    return live.history.slice(-n);
  }, [live.history.length, seconds]); // eslint-disable-line react-hooks/exhaustive-deps
}

export function BottomPanel() {
  const [tab, setTab] = useState("timeline");
  const order = useStore((s) => s.commandOrder);
  const commands = useStore((s) => s.commands);
  const h = useSeries(30);
  return (
    <section className="bottompanel">
      <div className="tabs">
        {["timeline", "graphs", "logs"].map((t) => <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>{t}</button>)}
      </div>
      {tab === "timeline" && (
        <div className="timeline">
          {order.slice(0, 40).map((id) => commands[id] && <CommandRow key={id} e={commands[id]} />)}
          {!order.length && <div className="muted">No commands yet. Say “Microduck, stand up”, press a button, or drive with W A S D.</div>}
        </div>
      )}
      {tab === "graphs" && (
        <div className="graphs">
          <Chart title="speed (ground truth)" unit="m/s" seconds={30} series={[
            { label: "v fwd", color: "#34d399", values: h.map((p) => p.vFwd) },
            { label: "req vx", color: "#60a5fa", values: h.map((p) => p.reqVx), dashed: true },
            { label: "applied vx", color: "#f5b041", values: h.map((p) => p.appVx), dashed: true }]} />
          <Chart title="trunk tilt / roll / pitch" unit="deg" seconds={30} series={[
            { label: "tilt", color: "#ef4444", values: h.map((p) => p.tilt) },
            { label: "roll", color: "#a78bfa", values: h.map((p) => p.roll) },
            { label: "pitch", color: "#38bdf8", values: h.map((p) => p.pitch) }]} />
          <Chart title="timing" seconds={30} series={[
            { label: "robotd loop Hz", color: "#34d399", values: h.map((p) => p.loopHz) },
            { label: "rtf×50", color: "#f5b041", values: h.map((p) => (p.rtf === null ? null : p.rtf * 50)) }]} />
        </div>
      )}
      {tab === "logs" && <RecentEvents limit={80} />}
    </section>
  );
}
