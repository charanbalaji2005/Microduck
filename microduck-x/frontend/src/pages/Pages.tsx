import { useEffect, useState } from "react";
import { command, getJson, postJson } from "../lib/api";
import { JOINT_LABELS, setState, setUi, useStore } from "../lib/store";
import type { EnvDescription, ExperimentDef, RunSummary } from "../lib/types";
import Chart from "../components/Chart";
import { CommandRow, JointTable, RecentEvents, TelemetryMini, useSeries } from "../components/Shell";
import VoiceMini, { VoiceEntryView } from "../voice/VoiceMini";

const COLORS = ["#34d399", "#60a5fa", "#f5b041", "#f472b6", "#a78bfa", "#38bdf8", "#facc15", "#fb7185", "#4ade80", "#c084fc", "#2dd4bf", "#fda4af", "#93c5fd", "#fcd34d", "#86efac"];

function usePoll<T>(path: string, ms = 3000, deps: unknown[] = []): [T | null, () => void, string | null] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = () => getJson<T>(path).then((d) => { setData(d); setErr(null); }).catch((e) => setErr(String(e)));
  useEffect(() => { load(); const t = setInterval(load, ms); return () => clearInterval(t); }, [path, ms, ...deps]); // eslint-disable-line
  return [data, load, err];
}

// ── Voice ────────────────────────────────────────────────────────────────────

export function VoicePage() {
  const voice = useStore((s) => s.voice);
  const st = useStore((s) => s.status);
  return (
    <div className="page two-col">
      <div>
        <VoiceMini />
        <section className="card">
          <h3>How voice reaches the robot</h3>
          <ol className="small">
            <li>The browser records the microphone locally (push-to-talk, or voice-activity detection in hands-free mode).</li>
            <li>The clip goes to the local control service only, and is transcribed offline by <b>{st?.speech.engine ?? "no engine"}</b>.</li>
            <li>A deterministic parser maps the words onto the command allowlist; nothing else can run.{st?.llm.enabled ? ` The local LLM (${st.llm.model}) is consulted only for text the parser could not place, and its output is validated the same way.` : " No language model is configured."}</li>
            <li>Low-confidence or uncalibrated transcripts wait for your confirmation — except stop commands, which never wait.</li>
            <li>The command goes through the safety gate to robotd's JSON-RPC (or to the simulator, for scene operations), and is reported complete only on observed results.</li>
          </ol>
          {st?.speech.whisper.installed && !st.speech.whisper.cached && (
            <div className="warnbox">faster-whisper is installed but its model <b>{st.speech.whisper.model}</b> (~{st.speech.whisper.size_mb} MB) is not downloaded; using {st.speech.engine ?? "nothing"}. Download it in Settings.</div>
          )}
        </section>
        <section className="card">
          <h3>Try saying</h3>
          <div className="chips small">
            {["Microduck, stand up", "walk forward slowly for two seconds", "turn right by ninety degrees", "stop moving immediately", "switch to the laboratory",
              "camera follow mode", "show the joint angles", "start the walking test", "go to goal two", "return to the starting position",
              "explain the robot's current status", "why did it fall", "pause the simulation", "push it gently from the left", "compare the last two runs"].map((p) => <span key={p} className="chip">{p}</span>)}
          </div>
        </section>
      </div>
      <section className="card scroll">
        <h3>Voice history</h3>
        {voice.map((v) => <VoiceEntryView key={v.id} v={v} />)}
        {!voice.length && <div className="muted">Nothing yet.</div>}
      </section>
    </div>
  );
}

// ── Environments ─────────────────────────────────────────────────────────────

export function EnvironmentsPage() {
  const [data] = usePoll<{ current: string; opts: Record<string, string>; environments: EnvDescription[] }>("/api/environments", 5000);
  const managed = useStore((s) => !!s.status?.sim.managed);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const go = async (environment: string, layout?: string) => {
    setBusy(environment + (layout ?? "")); setMsg(null);
    try { const r = await command({ action: "set_environment", environment, layout }); setMsg(`${r.status}: ${r.message}`); }
    catch (e) { setMsg(String(e)); } finally { setBusy(null); }
  };
  return (
    <div className="page">
      {!managed && <div className="warnbox">Switching scenes restarts the MuJoCo body, which this backend does not manage. Start with <code>microduck-x/scripts/x-start.sh</code> (or set MDX_MANAGE_SIM=1).</div>}
      {msg && <div className="infobox">{msg}</div>}
      <div className="env-grid">
        {data?.environments.map((e) => (
          <section key={e.name} className={`card env-card ${data.current === e.name ? "current" : ""}`}>
            <h3>{e.title} {e.original && <span className="tag ok">original</span>} {data.current === e.name && <span className="tag run">active</span>}</h3>
            <p className="small">{e.description}</p>
            <div className="muted small mono">base: {e.base_scene} · bounds {e.bounds.join(", ")} m · markers: {e.markers.map((m) => m.name).join(", ")}</div>
            <div className="row">
              {e.layouts.length > 1
                ? e.layouts.map((l) => <button key={l} disabled={!managed || !!busy} onClick={() => go(e.name, l)}>{busy === e.name + l ? "…" : `load · ${l}`}</button>)
                : <button disabled={!managed || !!busy} onClick={() => go(e.name)}>{busy === e.name ? "restarting body…" : "load"}</button>}
            </div>
          </section>
        ))}
      </div>
      <section className="card small">
        <h3>What “physical” means here</h3>
        Every visible object is a MuJoCo geom in the compiled model the viewer is built from. Anything with height is a collision geom
        (<code>contype=conaffinity=1</code>, like the robot's own); floor markings are flat visual-only decals ≤ 2 mm thick. The lab's foam
        blocks are free bodies the duck can push. The garden ramp is terrain the policies were never validated on.
      </section>
    </div>
  );
}

// ── Telemetry ────────────────────────────────────────────────────────────────

export function TelemetryPage() {
  const [seconds, setSeconds] = useState(30);
  const [sel, setSel] = useState<number[]>([2, 3, 4, 12, 13, 14]);
  const h = useSeries(seconds);
  return (
    <div className="page">
      <div className="row">
        <span className="muted">window</span>
        {[10, 30, 60, 120].map((s) => <button key={s} className={seconds === s ? "on" : ""} onClick={() => setSeconds(s)}>{s}s</button>)}
        <span className="muted">joints</span>
        {JOINT_LABELS.map((n, i) => i !== 9 && (
          <button key={n} className={sel.includes(i) ? "on" : ""} onClick={() => setSel(sel.includes(i) ? sel.filter((x) => x !== i) : [...sel, i])}>{n}</button>
        ))}
      </div>
      <div className="two-col">
        <div>
          <section className="card"><Chart title="joint angles (simulator)" unit="deg" seconds={seconds} height={170}
            series={sel.map((i, k) => ({ label: JOINT_LABELS[i], color: COLORS[k % COLORS.length], values: h.map((p) => (p.q[i] === null || p.q[i] === undefined ? null : (p.q[i]! * 180) / Math.PI)) }))} /></section>
          <section className="card"><Chart title="joint velocities (simulator)" unit="deg/s" seconds={seconds} height={150}
            series={sel.map((i, k) => ({ label: JOINT_LABELS[i], color: COLORS[k % COLORS.length], values: h.map((p) => (p.qd[i] === null || p.qd[i] === undefined ? null : (p.qd[i]! * 180) / Math.PI)) }))} /></section>
          <section className="card"><Chart title="velocity" unit="m/s · rad/s" seconds={seconds} series={[
            { label: "v fwd", color: "#34d399", values: h.map((p) => p.vFwd) }, { label: "ω z", color: "#a78bfa", values: h.map((p) => p.wz) },
            { label: "req vx", color: "#60a5fa", values: h.map((p) => p.reqVx), dashed: true }, { label: "req vyaw", color: "#f472b6", values: h.map((p) => p.reqVyaw), dashed: true }]} /></section>
          <section className="card"><Chart title="balance" unit="deg" seconds={seconds} series={[
            { label: "tilt", color: "#ef4444", values: h.map((p) => p.tilt) }, { label: "roll", color: "#a78bfa", values: h.map((p) => p.roll) },
            { label: "pitch", color: "#38bdf8", values: h.map((p) => p.pitch) }]} /></section>
          <section className="card"><Chart title="foot contact (simulator)" seconds={seconds} min={-0.1} max={2.2} series={[
            { label: "left", color: "#34d399", values: h.map((p) => p.footL) }, { label: "right +1", color: "#f5b041", values: h.map((p) => p.footR + 1.05) }]} /></section>
          <section className="card"><Chart title="trunk height" unit="m" seconds={seconds} series={[{ label: "z", color: "#34d399", values: h.map((p) => p.z) }]} /></section>
        </div>
        <div>
          <TelemetryMini />
          <section className="card"><h3>Joints <span className="muted small">simulator vs robotd</span></h3><JointTable velocities /></section>
          <section className="card small muted">
            “Simulator” columns are MuJoCo ground truth. “robotd” columns are what the robot's own software measured and commanded
            (<code>robot.state</code>). The mouth joint is not in the walking model, so the simulator reports none.
            IMU: robotd consumes projected gravity and gyro from the body server; foot contacts are simulator-only.
          </section>
        </div>
      </div>
    </div>
  );
}

// ── Navigation ───────────────────────────────────────────────────────────────

export function NavigationPage() {
  const [model] = usePoll<{ env: EnvDescription }>("/api/model", 10000);
  const ui = useStore((s) => s.ui);
  const plan = useStore((s) => s.plan);
  const [msg, setMsg] = useState<string | null>(null);
  const preview = async (target: string) => {
    try {
      const r = await postJson("/api/nav/preview", { target });
      setState({ plan: { goal: r.goal, path: r.path }, occupancy: r.grid });
      setUi({ overlays: { ...ui.overlays, occupancy: true, path: true } });
      setMsg(`planned ${r.path.length - 1} legs to ${r.goal} (${r.pose_source})`);
    } catch (e) { setMsg(String(e)); }
  };
  const go = async (target: string) => { const r = await command({ action: "goto", target }); setMsg(`${r.status}: ${r.message || "running"}`); };
  return (
    <div className="page two-col">
      <section className="card">
        <h3>Markers in {model?.env.title ?? "…"}</h3>
        {model?.env.markers.map((m) => (
          <div key={m.name} className="row between">
            <span><b>{m.name}</b> <span className="muted small mono">({m.x.toFixed(2)}, {m.y.toFixed(2)}) · {m.kind}</span></span>
            <span className="row"><button onClick={() => preview(m.name)}>preview path</button><button className="primary" onClick={() => go(m.name)}>go</button></span>
          </div>
        ))}
        <div className="row"><button onClick={() => command({ action: "waypoints" })}>tour all goals</button>
          <label className="toggle"><input type="checkbox" checked={ui.overlays.occupancy} onChange={(e) => setUi({ overlays: { ...ui.overlays, occupancy: e.target.checked } })} /> show occupancy grid</label></div>
        {msg && <div className="infobox">{msg}</div>}
        {plan && <div className="small mono">last plan → {plan.goal}: {plan.path.map(([x, y]) => `(${x.toFixed(2)},${y.toFixed(2)})`).join(" → ")}</div>}
      </section>
      <section className="card small">
        <h3>How it works — and what it does not prove</h3>
        <p>The original robot has no navigation: robotd follows a velocity twist. This planner is a Microduck X extension. It rasterises
          every collidable, non-robot geom of the <b>compiled MuJoCo model</b> whose height overlaps 1.5–35 cm onto a 5 cm grid, inflates it by the
          duck's 12 cm radius, runs A*, shortcuts the path, and drives it with a heading controller on <code>robot.move</code> at 20 Hz.</p>
        <p>The pose comes from the simulator (ground truth); robotd's own contact odometry is shown beside it in Telemetry. A collision-free line on
          a grid is not proof a biped can walk it: execution is closed-loop with a progress watchdog (8 s), a timeout, the fall gate, and a count of
          obstacle contacts. Failures are reported, never retried silently.</p>
      </section>
    </div>
  );
}

// ── Experiments ──────────────────────────────────────────────────────────────

export function ExperimentsPage() {
  const [defs, reloadDefs] = usePoll<{ definitions: ExperimentDef[]; current: any }>("/api/experiments", 2000);
  const [runs] = usePoll<RunSummary[]>("/api/runs", 3000);
  const [trials, setTrials] = useState<Record<string, number>>({});
  const [open, setOpen] = useState<any>(null);
  const [cmp, setCmp] = useState<string[]>([]);
  const [cmpResult, setCmpResult] = useState<any>(null);
  const run = async (name: string) => { await postJson(`/api/experiments/${name}/run`, { trials: trials[name] ?? 1 }); reloadDefs(); };
  const show = async (id: string) => setOpen(await getJson(`/api/runs/${id}?samples=true`));
  const compare = async () => setCmpResult(await getJson(`/api/compare?a=${cmp[0]}&b=${cmp[1]}`));
  const cur = defs?.current;
  return (
    <div className="page two-col">
      <div>
        {cur && <div className="infobox">Running <b>{cur.name}</b> — trial {cur.trial}/{cur.trials}</div>}
        {defs?.definitions.map((d) => (
          <section key={d.name} className="card">
            <h3>{d.title} <span className="muted small mono">{d.name}</span></h3>
            <p className="small">{d.description}</p>
            <div className="muted small mono">{d.environment}{d.layout ? `/${d.layout}` : ""} · start {d.keyframe} · steps {d.steps.map((s) => String(s.action)).join(" → ")}</div>
            <div className="muted small mono">success: {Object.entries(d.success).filter(([k, v]) => v !== null && v !== undefined && (k !== "goal_tolerance_m" || d.success.goal)).map(([k, v]) => `${k}=${v}`).join(", ")}</div>
            <div className="row">
              <label className="small">trials <input type="number" min={1} max={20} value={trials[d.name] ?? 1} onChange={(e) => setTrials({ ...trials, [d.name]: Number(e.target.value) })} /></label>
              <button className="primary" disabled={!!cur} onClick={() => run(d.name)}>run</button>
            </div>
          </section>
        ))}
      </div>
      <div>
        <section className="card">
          <h3>Runs {cmp.length === 2 && <button onClick={compare}>compare selected</button>}</h3>
          <table className="runs">
            <thead><tr><th /><th>run</th><th>status</th><th>pass</th><th /></tr></thead>
            <tbody>
              {runs?.map((r) => (
                <tr key={r.id}>
                  <td><input type="checkbox" checked={cmp.includes(r.id)} onChange={(e) => setCmp(e.target.checked ? [...cmp, r.id].slice(-2) : cmp.filter((x) => x !== r.id))} /></td>
                  <td className="mono small"><button className="link" onClick={() => show(r.id)}>{r.id}</button>{r.saved ? " ★" : ""}</td>
                  <td><span className={`tag ${r.status === "passed" ? "ok" : r.status === "failed" ? "bad" : "warn"}`}>{r.status}</span></td>
                  <td className="mono">{r.passed_trials}/{r.trials}</td>
                  <td className="small"><a href={`/api/runs/${r.id}/export.json`}>json</a> · <a href={`/api/runs/${r.id}/export.csv`}>csv</a></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!runs?.length && <div className="muted">No runs yet.</div>}
        </section>
        {cmpResult && (
          <section className="card"><h3>Comparison</h3>
            <table className="runs mono small"><thead><tr><th>metric</th><th>{cmpResult.a.id}</th><th>{cmpResult.b.id}</th><th>Δ</th></tr></thead>
              <tbody>{Object.keys(cmpResult.delta_b_minus_a).map((k) => <tr key={k}><td>{k}</td><td>{cmpResult.a[k]}</td><td>{cmpResult.b[k]}</td><td>{cmpResult.delta_b_minus_a[k]}</td></tr>)}</tbody></table>
          </section>
        )}
        {open && <RunDetail run={open} onClose={() => setOpen(null)} />}
      </div>
    </div>
  );
}

function RunDetail({ run, onClose }: { run: any; onClose: () => void }) {
  return (
    <section className="card">
      <h3>{run.title} <span className="muted small mono">{run.id}</span> <button onClick={onClose}>close</button></h3>
      <div className="muted small mono">microduck {run.software?.microduck_rev ?? "?"} · walk {run.software?.robotd_ack?.walk ?? "?"} · scene {run.software?.sim?.env} {JSON.stringify(run.software?.sim?.opts ?? {})}</div>
      {run.results.map((t: any) => (
        <div key={t.trial} className="trial">
          <div><b>trial {t.trial}</b> <span className={`tag ${t.passed ? "ok" : "bad"}`}>{t.passed ? "pass" : "fail"}</span> <span className="muted small">{t.settle?.detail}</span></div>
          <ul className="small">
            {t.criteria_passed.map((c: string) => <li key={c} className="ok-text">✓ {c}</li>)}
            {t.criteria_failed.map((c: string) => <li key={c} className="bad-text">✗ {c}</li>)}
          </ul>
          <div className="mono small">{Object.entries(t.metrics).filter(([k]) => !["end"].includes(k)).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`).join(" · ")}</div>
          {Array.isArray(t.samples) && t.samples.length > 2 && (
            <Chart title="trajectory: forward speed and tilt" series={[
              { label: "v fwd", color: "#34d399", values: t.samples.map((s: any) => s.v_fwd ?? null) },
              { label: "tilt/100", color: "#ef4444", values: t.samples.map((s: any) => (s.tilt === undefined ? null : s.tilt / 100)) }]} />
          )}
        </div>
      ))}
    </section>
  );
}

// ── Policy ───────────────────────────────────────────────────────────────────

export function PolicyPage() {
  const [p] = usePoll<{ ack: any; policies: any }>("/api/policies", 4000);
  const slots = p?.policies?.slots ?? [];
  return (
    <div className="page two-col">
      <section className="card">
        <h3>What robotd is running <span className="muted small">robot.policies</span></h3>
        {p?.policies?.error && <div className="errbox">{p.policies.error}</div>}
        <div className="kv mono small">
          <span>mode</span><b>{p?.policies?.mode ?? "–"}</b><span>enabled</span><b>{String(p?.policies?.enabled ?? "–")}</b>
          <span>sitting</span><b>{String(p?.policies?.sitting ?? "–")}</b><span>skills</span><b>{(p?.policies?.skills ?? []).join(", ") || "–"}</b>
        </div>
        <table className="runs small mono">
          <thead><tr><th>slot</th><th>file</th><th>origin</th><th>state</th></tr></thead>
          <tbody>{slots.map((s: any) => (
            <tr key={s.slot}><td>{s.slot}</td><td>{s.path ?? "—"}</td><td>{s.origin ?? "—"}</td><td className={s.error ? "bad-text" : "ok-text"}>{s.error ?? (s.path ? "loaded" : "disabled")}</td></tr>
          ))}</tbody>
        </table>
        {p?.ack?.unavailable && <div className="errbox">robotd: {p.ack.unavailable}</div>}
      </section>
      <section className="card small">
        <h3>What this means</h3>
        <p>Policies are ONNX networks trained in <code>microduck_rl</code> (MuJoCo + PPO) and loaded by robotd from the official set
          (<code>pollen-robotics/microduck-policies</code>, pinned v8). The default walk slot is <code>velstand.onnx</code>, which walks on a twist and stands at
          zero command; there is no separate stand network by default. robotd refuses any graph that is not obs[1,61] → actions[1,14].</p>
        <p>The dashboard says “policy driving” only when <code>robot.state.policy</code> names one. If the slot table shows an error, the robot holds
          its pose and every movement command is refused here — install the set with <code>scripts/seed-policies.sh</code> (x-setup does this).</p>
        <p>Inference runs inside robotd at 50 Hz; its loop rate is in the status bar. Training and fine-tuning are not started from this dashboard.</p>
      </section>
    </div>
  );
}

// ── Logs ─────────────────────────────────────────────────────────────────────

export function LogsPage() {
  const events = useStore((s) => s.events);
  const [level, setLevel] = useState("all");
  const [q, setQ] = useState("");
  const shown = events.filter((e) => (level === "all" || e.level === level) && (!q || (e.kind + e.message).toLowerCase().includes(q.toLowerCase())));
  const order = useStore((s) => s.commandOrder);
  const commands = useStore((s) => s.commands);
  const download = () => {
    const blob = new Blob([JSON.stringify(events, null, 1)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "microduck-x-events.json"; a.click();
  };
  return (
    <div className="page two-col">
      <section className="card scroll">
        <h3>Events <span className="row inline">
          {["all", "info", "warn", "error"].map((l) => <button key={l} className={level === l ? "on" : ""} onClick={() => setLevel(l)}>{l}</button>)}
          <input placeholder="filter" value={q} onChange={(e) => setQ(e.target.value)} /><button onClick={download}>download</button></span></h3>
        {shown.slice().reverse().map((e) => (
          <div key={e.id} className={`event ${e.level}`}>
            <span className="mono muted">{new Date(e.t * 1000).toLocaleTimeString([], { hour12: false })}</span><span className="kind">{e.kind}</span><span>{e.message}</span>
          </div>
        ))}
      </section>
      <section className="card scroll"><h3>Command history</h3>{order.map((id) => commands[id] && <CommandRow key={id} e={commands[id]} />)}</section>
    </div>
  );
}

// ── Settings ─────────────────────────────────────────────────────────────────

export function SettingsPage() {
  const ui = useStore((s) => s.ui);
  const st = useStore((s) => s.status);
  const [dl, setDl] = useState<any>(null);
  const ask = async () => setDl(await postJson("/api/voice/download", {}));
  const confirmDl = async () => setDl(await postJson("/api/voice/download", { confirm: true }));
  const ov = ui.overlays;
  return (
    <div className="page two-col">
      <section className="card">
        <h3>Viewer</h3>
        <label className="slider">camera sensitivity <input type="range" min={0.2} max={3} step={0.1} value={ui.sensitivity} onChange={(e) => setUi({ sensitivity: Number(e.target.value) })} /> {ui.sensitivity.toFixed(1)}</label>
        <label className="slider">field of view <input type="range" min={25} max={90} value={ui.fov} onChange={(e) => setUi({ fov: Number(e.target.value) })} /> {ui.fov}°</label>
        <label className="toggle"><input type="checkbox" checked={ui.shadows} onChange={(e) => setUi({ shadows: e.target.checked })} /> soft shadows</label>
        <label className="toggle"><input type="checkbox" checked={ui.ao} onChange={(e) => setUi({ ao: e.target.checked })} /> ambient occlusion (SSAO, costs GPU)</label>
        <h3>Overlays</h3>
        <div className="chips">
          {(Object.keys(ov) as (keyof typeof ov)[]).map((k) => (
            <label key={k} className="toggle"><input type="checkbox" checked={ov[k]} onChange={(e) => setUi({ overlays: { ...ov, [k]: e.target.checked } })} /> {k}</label>
          ))}
        </div>
      </section>
      <section className="card">
        <h3>Voice</h3>
        <div className="kv small mono">
          <span>engine</span><b>{st?.speech.engine ?? `none — ${st?.speech.reason}`}</b>
          <span>preference</span><b>{st?.speech.preference}</b>
          <span>ffmpeg</span><b>{String(st?.speech.ffmpeg)}</b>
          <span>whisper</span><b>{st?.speech.whisper.installed ? `${st.speech.whisper.model} · ${st.speech.whisper.cached ? "on disk" : "not downloaded"}` : "not installed"}</b>
          <span>min confidence</span><b>{st?.config.stt_min_confidence}</b>
          <span>wake word</span><b>{st?.config.require_wake_word ? "required" : "hands-free only"}</b>
          <span>LLM</span><b>{st?.llm.enabled ? st.llm.model : "off"}</b>
        </div>
        {st?.speech.whisper.installed && !st.speech.whisper.cached && !dl && <button onClick={ask}>Download whisper model…</button>}
        {dl?.confirm_required && (
          <div className="confirm">Download <b>{dl.model}</b> (~{dl.size_mb} MB) from Hugging Face into <code>{dl.destination}</code>? It is cached; nothing is downloaded again.
            <button className="primary" onClick={confirmDl}>Download</button><button onClick={() => setDl(null)}>Cancel</button></div>
        )}
        {dl?.state && <div className="infobox">download: {dl.state} {String(st?.speech.whisper.download?.state ?? "")}</div>}
        <h3>Spoken replies</h3>
        <div className="row">{(["browser", "espeak", "off"] as const).map((m) => (
          <button key={m} className={ui.tts === m ? "on" : ""} disabled={m === "espeak" && !st?.tts.espeak} onClick={() => setUi({ tts: m })}>{m === "browser" ? "browser (local voices)" : m}</button>))}</div>
        <h3>Service</h3>
        <div className="kv small mono">
          <span>version</span><b>{st?.version}</b><span>bind</span><b>{st?.config.local_only ? "localhost only" : "NETWORK"}{st?.config.auth ? " · token" : ""}</b>
          <span>robotd socket</span><b>{st?.robotd.socket}</b><span>simulator</span><b>{st?.sim.address}{st?.sim.managed ? " (managed)" : ""}</b>
          <span>limits</span><b>vx ±{st?.config.limits.vx} · vy ±{st?.config.limits.vy} · vyaw ±{st?.config.limits.vyaw} · ≤{st?.config.max_command_s}s</b>
        </div>
      </section>
    </div>
  );
}

export function OverviewSide() {
  return (
    <div className="page"><RecentEvents limit={30} /></div>
  );
}
