import Icon from "../components/Icon";
import { setState, setUi, useStore } from "../lib/store";
import { unlockVoice } from "./duckVoice";
import { pauseTalking, replayReply, resumeTalking, setMuted, setVolume, stopTalking, useVoiceState } from "./events";

// The duck's voice, under the chat bar: what it is doing (speaking, paused, held back by the
// browser) and the controls for it. Only the voice — the legs are never touched from here.

/** Mute or unmute the duck, everywhere it is offered (this bar, the ring around the duck). */
export function setVoiceMuted(muted: boolean) {
  setMuted(muted);
  setUi({ muted });
  if (muted) stopTalking();
  else { unlockVoice(); setState({ voiceError: null }); }
}

const LABEL = { blocked: "Voice held by browser", speaking: "Speaking…", paused: "Paused", idle: "Voice" } as const;

export default function VoiceControls() {
  const v = useVoiceState();
  const muted = useStore((s) => s.ui.muted);
  const volume = useStore((s) => s.ui.volume);
  const autoSpeak = useStore((s) => s.ui.autoSpeak);
  const state = v.blocked ? "blocked" : v.speaking ? (v.paused ? "paused" : "speaking") : "idle";

  return (
    <div className="h-voice" role="group" aria-label="Duck voice">
      <span className={`h-voice-state ${state}`} role="status" aria-live="polite">
        <i aria-hidden="true" />{muted && state === "idle" ? "Muted" : LABEL[state]}
      </span>
      {v.speaking && !v.paused && (
        <button type="button" onClick={pauseTalking} aria-label="Pause voice" title="Pause"><Icon name="pause" size={14} /></button>
      )}
      {v.speaking && v.paused && (
        <button type="button" onClick={resumeTalking} aria-label="Resume voice" title="Resume"><Icon name="play" size={14} /></button>
      )}
      {!v.speaking && (
        <button type="button" disabled={!v.canReplay || muted} onClick={() => { unlockVoice(); replayReply(); }}
                aria-label="Replay the last reply" title="Replay the last reply"><Icon name="replay" size={14} /></button>
      )}
      <button type="button" disabled={!v.speaking} onClick={stopTalking} aria-label="Stop voice"
              title="Stop the voice; the duck keeps doing what it was doing"><Icon name="stop" size={14} /></button>
      <button type="button" className={muted ? "on" : ""} onClick={() => setVoiceMuted(!muted)} aria-pressed={muted}
              aria-label={muted ? "Unmute" : "Mute"} title={muted ? "Unmute" : "Mute"}><Icon name={muted ? "mute" : "volume"} size={14} /></button>
      <input type="range" min={0} max={1} step={0.05} value={volume} aria-label="Voice volume" title={`Volume ${Math.round(volume * 100)}%`}
             onChange={(e) => { const x = Number(e.target.value); setVolume(x); setUi({ volume: x }); }} />
      <label className="h-voice-auto" title="Speak each reply as it arrives">
        <input type="checkbox" checked={autoSpeak} onChange={(e) => setUi({ autoSpeak: e.target.checked })} /> Auto speak
      </label>
    </div>
  );
}
