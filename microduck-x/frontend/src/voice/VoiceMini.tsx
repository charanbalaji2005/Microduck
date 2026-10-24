import { useState } from "react";
import { setUi, useStore, type VoiceEntry } from "../lib/store";
import { confirm, discard, pttDown, pttUp, setHandsFree, submitText, toggleMic, useMic } from "./controller";

export function VoiceEntryView({ v }: { v: VoiceEntry }) {
  const commands = useStore((s) => s.commands);
  return (
    <div className="voice-entry">
      <div className="voice-said">
        <span className="tag">{v.source}</span>
        <b>“{v.transcript || "…"}”</b>
        {v.confidence !== undefined && v.confidence !== null && <span className="muted small"> {(v.confidence * 100).toFixed(0)}%</span>}
        {v.engine && <span className="muted small"> · {v.engine}</span>}
      </div>
      {v.ignored && <div className="muted small">ignored: {v.ignored}</div>}
      {v.errors.map((e) => <div key={e} className="errbox">{e}</div>)}
      {v.unmatched.length > 0 && <div className="warnbox">not understood: {v.unmatched.join(" · ")}</div>}
      {v.commands.length > 0 && <div className="small">→ {v.commands.join(" → ")}</div>}
      {v.notes.length > 0 && <div className="muted small">{v.notes.join("; ")}</div>}
      {v.needsConfirmation && (
        <div className="confirm">
          <span>{v.reply}</span>
          <button className="primary" onClick={() => confirm(v)}>Run it</button>
          <button onClick={() => discard(v)}>Discard</button>
        </div>
      )}
      {v.executions.map((e) => {
        const live = commands[e.id];
        return <div key={e.id} className="small mono">{e.action}: <b>{live?.status ?? e.status}</b> {live?.message ? `— ${live.message}` : ""}</div>;
      })}
    </div>
  );
}

export default function VoiceMini() {
  const mic = useMic();
  const st = useStore((s) => s.status);
  const voice = useStore((s) => s.voice);
  const handsFree = useStore((s) => s.ui.handsFree);
  const [text, setText] = useState("");
  const engine = st?.speech.engine;
  return (
    <section className="card voice-card">
      <h3>Voice <span className="muted small">{engine ? `offline · ${engine}` : st?.speech.reason ?? "…"}</span></h3>
      <div className="voice-controls">
        <button className={`mic ${mic.recording ? "rec" : mic.open ? "open" : ""}`}
          onMouseDown={() => pttDown()} onMouseUp={() => pttUp()} onMouseLeave={() => mic.recording && pttUp()}
          onTouchStart={() => pttDown()} onTouchEnd={() => pttUp()} disabled={!engine} title="Hold to talk (or hold Space)">
          {mic.recording ? "● listening" : mic.busy ? "…" : "hold to talk"}
        </button>
        <button onClick={() => toggleMic()} className={mic.open ? "on" : ""}>{mic.open ? "mic on" : "mic off"}</button>
        <label className="toggle" title="Hands-free: voice activity detection; commands must start with “Microduck”">
          <input type="checkbox" checked={handsFree} onChange={(e) => setHandsFree(e.target.checked)} disabled={!engine} /> hands-free
        </label>
      </div>
      <div className="level"><div className="level-bar" style={{ width: `${Math.min(100, mic.level * 900)}%` }} />
        <div className="level-floor" style={{ left: `${Math.min(100, mic.floor * 3.5 * 900)}%` }} /></div>
      {mic.error && <div className="errbox">{mic.error}</div>}
      <form onSubmit={(e) => { e.preventDefault(); submitText(text); setText(""); }} className="voice-text">
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="…or type: walk forward for 2 seconds" />
        <button type="submit">send</button>
      </form>
      {voice.slice(0, 2).map((v) => <VoiceEntryView key={v.id} v={v} />)}
      {voice.length > 2 && <button className="link" onClick={() => setUi({ page: "voice" })}>all {voice.length} voice interactions →</button>}
    </section>
  );
}
