import { useEffect, useRef, useState } from "react";
import { command } from "../lib/api";
import { behaviorManager } from "../lib/autonomousBehavior";
import { setUi, useStore } from "../lib/store";
import { nextScenery } from "../lib/ws";
import { COUSINS, MAX_COUSINS } from "../sim/puppets";
import { SCENERIES, sceneryById, sceneryUrl, type Scenery } from "../sim/scenery";
import type { SceneryStatus } from "../sim/ShowcaseViewer";
import { unlockVoice } from "../voice/duckVoice";
import Icon, { SCENERY_ICON } from "./Icon";
import "./scenery.css";

// The scenery dock: which world surrounds the stage, whether it changes by itself, how many
// background ducks play, and whether the duck talks when left alone. Picking the hangar (its own
// MuJoCo scene) or a photo while in another scene switches scenes the normal way — an
// allowlisted `set_environment` through the executor.

const EVERY = [60, 120, 300, 600];

export default function SceneryDock({ status, hidden }: { status: SceneryStatus | null; hidden?: boolean }) {
  const ui = useStore((s) => s.ui);
  const env = useStore((s) => s.status?.sim.env) ?? "";
  const [open, setOpen] = useState(false);
  const [left, setLeft] = useState(ui.sceneryEvery);
  const shown = env === "hangar" ? sceneryById("hangar") : status?.scenery ?? sceneryById(ui.scenery);
  const busy = !!status?.loading;
  const uiRef = useRef(ui);
  uiRef.current = ui;

  const pick = (s: Scenery) => {
    setOpen(false);
    if (s.kind === "hangar") { if (env !== "hangar") void command({ action: "set_environment", environment: "hangar" }); return; }
    if (env && env !== "stage") void command({ action: "set_environment", environment: "stage" });
    setUi({ scenery: s.id });
    setLeft(uiRef.current.sceneryEvery);
  };

  // a new world every few minutes, on the stage, while nobody is painting the duck
  useEffect(() => {
    if (!ui.sceneryAuto || env !== "stage" || hidden) return;
    let remaining = ui.sceneryEvery;
    setLeft(remaining);
    const h = window.setInterval(() => {
      if (--remaining <= 0) {
        setUi({ scenery: nextScenery(uiRef.current.scenery) });
        remaining = uiRef.current.sceneryEvery;
      }
      setLeft(remaining);
    }, 1000);
    return () => window.clearInterval(h);
  }, [ui.sceneryAuto, ui.sceneryEvery, ui.scenery, env, hidden]);

  // what the duck may talk about when it talks by itself, and any sign of life quiets it
  useEffect(() => {
    behaviorManager.setHintProvider(() => ({
      scenery: env === "hangar" ? "hangar" : uiRef.current.scenery,
      cousins: COUSINS.slice(0, uiRef.current.cousins).map((c) => c.name),
    }));
  }, [env]);
  useEffect(() => { behaviorManager.setEnabled(ui.idleTalk); }, [ui.idleTalk]);
  useEffect(() => {
    const alive = () => { behaviorManager.touch(); unlockVoice(); };
    window.addEventListener("pointerdown", alive);
    window.addEventListener("keydown", alive);
    return () => { window.removeEventListener("pointerdown", alive); window.removeEventListener("keydown", alive); };
  }, []);

  if (hidden) return null;
  const ring = ui.sceneryAuto && env === "stage" ? 1 - left / ui.sceneryEvery : 0;
  return (
    <div className={`sd ${open ? "open" : ""}`} style={{ ["--accent" as string]: shown.accent }}>
      {open && (
        <div className="sd-gallery glass" role="dialog" aria-label="Choose the scenery">
          <div className="sd-gallery-head">
            <div><div className="sd-kicker">Scenery</div><div className="sd-h">Where should we go?</div></div>
            <button className="sd-icon-btn" onClick={() => setOpen(false)} aria-label="Close"><Icon name="close" /></button>
          </div>
          <div className="sd-grid">
            {SCENERIES.map((s) => {
              const active = s.id === shown.id;
              return (
                <button key={s.id} className={`sd-card ${active ? "active" : ""}`} onClick={() => pick(s)} style={{ ["--accent" as string]: s.accent }}>
                  <span className="sd-thumb" style={{ backgroundImage: `url(${sceneryUrl(s.thumb)})` }}>
                    <span className={`sd-thumb-fallback k-${s.kind}`} />
                    {s.kind === "hangar" && <span className="sd-badge">MuJoCo scene</span>}
                    {active && <span className="sd-badge live"><Icon name="check" size={12} /> on now</span>}
                  </span>
                  <span className="sd-card-body">
                    <span className="sd-card-icon"><Icon name={SCENERY_ICON[s.id] ?? "scenery"} size={18} /></span>
                    <span><span className="sd-card-title">{s.title}</span><span className="sd-card-place">{s.place}</span></span>
                  </span>
                </button>
              );
            })}
          </div>
          <div className="sd-gallery-foot">
            <span>Photographs and backdrop are drawn by the viewer — the deck, pedestal and glass ring are the physics.</span>
          </div>
        </div>
      )}

      <div className="sd-bar glass">
        <button className="sd-now" onClick={() => setOpen(!open)} title="Choose the scenery">
          <span className={`sd-now-icon ${busy ? "busy" : ""}`}><Icon name={SCENERY_ICON[shown.id] ?? "scenery"} size={20} /></span>
          <span className="sd-now-text">
            <span className="sd-now-title">{busy ? "Travelling…" : shown.title}</span>
            <span className="sd-now-place">{status?.error ? "scenery files missing — run x-scenery.sh" : shown.place}</span>
          </span>
        </button>
        <span className="sd-sep" />
        <button className="sd-icon-btn" disabled={env === "hangar" || busy} title="Next scenery"
          onClick={() => pick(sceneryById(nextScenery(ui.scenery)))}><Icon name="shuffle" /></button>
        <button className={`sd-icon-btn sd-auto ${ui.sceneryAuto ? "on" : ""}`} disabled={env === "hangar"}
          title={ui.sceneryAuto ? `Changes every ${ui.sceneryEvery / 60} min — click for slower, hold Shift to stop` : "Change the scenery by itself"}
          onClick={(e) => {
            if (!ui.sceneryAuto) setUi({ sceneryAuto: true });
            else if (e.shiftKey) setUi({ sceneryAuto: false });
            else { const i = EVERY.indexOf(ui.sceneryEvery); if (i === EVERY.length - 1) setUi({ sceneryAuto: false, sceneryEvery: EVERY[0] }); else setUi({ sceneryEvery: EVERY[i + 1] ?? 120 }); }
          }}>
          <svg className="sd-ring" viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="15.5" style={{ strokeDashoffset: 97.4 * (1 - ring) }} /></svg>
          <Icon name="timer" />
          {ui.sceneryAuto && <span className="sd-auto-label">{ui.sceneryEvery < 120 ? "1m" : `${ui.sceneryEvery / 60}m`}</span>}
        </button>
        <span className="sd-sep" />
        <div className="sd-stepper" title="Background ducks (viewer animation, not physics)">
          <Icon name="duck" />
          <button onClick={() => setUi({ cousins: Math.max(0, ui.cousins - 1) })} disabled={ui.cousins <= 0} aria-label="Fewer ducks">−</button>
          <span className="sd-count">{ui.cousins}</span>
          <button onClick={() => setUi({ cousins: Math.min(MAX_COUSINS, ui.cousins + 1) })} disabled={ui.cousins >= MAX_COUSINS} aria-label="More ducks">+</button>
        </div>
        <span className="sd-sep" />
        <button className={`sd-icon-btn ${ui.idleTalk ? "on" : ""}`} onClick={() => setUi({ idleTalk: !ui.idleTalk })}
          title={ui.idleTalk ? "The duck talks when left alone — click to keep it quiet" : "Let the duck talk when left alone"}>
          <Icon name="talk" />
        </button>
      </div>
      <div className="sd-credit">{shown.credit}</div>
    </div>
  );
}
