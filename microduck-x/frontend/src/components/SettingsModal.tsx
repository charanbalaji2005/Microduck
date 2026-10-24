import { behaviorManager, type BehaviorMode } from "../lib/autonomousBehavior";
import { setVolume, setMuted } from "../voice/events";

export interface UserPreferences {
  volume: number;
  muted: boolean;
  showDroidText: boolean;
  showTranslation: boolean;
  showEmotionBadge: boolean;
  autoBubbles: boolean;
  behaviorMode: BehaviorMode;
}

const PREF_KEY = "microquack_preferences_v1";

export const defaultPreferences: UserPreferences = {
  volume: 0.85,
  muted: false,
  showDroidText: true,
  showTranslation: true,
  showEmotionBadge: true,
  autoBubbles: true,
  behaviorMode: "curious",
};

export function loadPreferences(): UserPreferences {
  try {
    const raw = localStorage.getItem(PREF_KEY);
    if (raw) return { ...defaultPreferences, ...JSON.parse(raw) };
  } catch { /* storage disabled */ }
  return defaultPreferences;
}

export function savePreferences(prefs: UserPreferences) {
  try {
    localStorage.setItem(PREF_KEY, JSON.stringify(prefs));
  } catch { /* storage disabled */ }
}

interface SettingsModalProps {
  open: boolean;
  prefs: UserPreferences;
  onUpdatePrefs: (next: UserPreferences) => void;
  onClose: () => void;
  onResetFactoryColors: () => void;
}

export default function SettingsModal({ open, prefs, onUpdatePrefs, onClose, onResetFactoryColors }: SettingsModalProps) {
  if (!open) return null;

  const update = (patch: Partial<UserPreferences>) => {
    const next = { ...prefs, ...patch };
    if (patch.volume !== undefined) setVolume(patch.volume);
    if (patch.muted !== undefined) setMuted(patch.muted);
    if (patch.behaviorMode !== undefined) behaviorManager.setMode(patch.behaviorMode);
    savePreferences(next);
    onUpdatePrefs(next);
  };

  const resetAll = () => {
    localStorage.removeItem(PREF_KEY);
    setVolume(defaultPreferences.volume);
    setMuted(defaultPreferences.muted);
    behaviorManager.setMode(defaultPreferences.behaviorMode);
    onResetFactoryColors();
    onUpdatePrefs({ ...defaultPreferences });
  };

  return (
    <div className="settings-backdrop" onClick={onClose}>
      <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="settings-header">
          <h3>⚙ Microduck &amp; Voice Settings</h3>
          <button className="settings-close" onClick={onClose}>×</button>
        </div>

        <div className="settings-section">
          <h4>🔊 Authentic Procedural Voice Synthesizer</h4>
          <div className="settings-row">
            <label>Master Voice Volume: {Math.round(prefs.volume * 100)}%</label>
            <input
              type="range"
              min="0"
              max="1"
              step="0.05"
              value={prefs.volume}
              onChange={(e) => update({ volume: parseFloat(e.target.value) })}
            />
          </div>
          <div className="settings-row checkbox">
            <label>
              <input
                type="checkbox"
                checked={prefs.muted}
                onChange={(e) => update({ muted: e.target.checked })}
              />
              Mute All Synthesized Droid Audio
            </label>
          </div>
        </div>

        <div className="settings-section">
          <h4>💬 Speech Bubble &amp; Subtitles</h4>
          <div className="settings-row checkbox">
            <label>
              <input
                type="checkbox"
                checked={prefs.showDroidText}
                onChange={(e) => update({ showDroidText: e.target.checked })}
              />
              Show Droid-Language Phonetic Transcription (e.g. "bweep wheep brdee!")
            </label>
          </div>
          <div className="settings-row checkbox">
            <label>
              <input
                type="checkbox"
                checked={prefs.showTranslation}
                onChange={(e) => update({ showTranslation: e.target.checked })}
              />
              Show Human-Readable English Interpretation
            </label>
          </div>
          <div className="settings-row checkbox">
            <label>
              <input
                type="checkbox"
                checked={prefs.showEmotionBadge}
                onChange={(e) => update({ showEmotionBadge: e.target.checked })}
              />
              Show Emotion &amp; Mood Badge (HAPPY, CURIOUS, etc.)
            </label>
          </div>
          <div className="settings-row checkbox">
            <label>
              <input
                type="checkbox"
                checked={prefs.autoBubbles}
                onChange={(e) => update({ autoBubbles: e.target.checked })}
              />
              Show Automatic Floating Speech Bubbles
            </label>
          </div>
        </div>

        <div className="settings-section">
          <h4>🤖 Autonomous Behavior Mode</h4>
          <div className="behavior-buttons">
            {(["curious", "explore", "play", "social", "rest", "manual"] as BehaviorMode[]).map((m) => (
              <button
                key={m}
                className={`behavior-btn ${prefs.behaviorMode === m ? "active" : ""}`}
                onClick={() => update({ behaviorMode: m })}
              >
                {m.toUpperCase()}
              </button>
            ))}
          </div>
          <p className="settings-hint">
            {prefs.behaviorMode === "curious" && "Mutters procedural droid chatter about stars and surroundings when idle."}
            {prefs.behaviorMode === "explore" && "Active curiosity with sound chirps and sensor telemetry queries."}
            {prefs.behaviorMode === "play" && "High-energy chirp melodies and playful posture."}
            {prefs.behaviorMode === "social" && "Responds eagerly to speech, gestures, and greetings."}
            {prefs.behaviorMode === "rest" && "Quiet and calm, low-frequency soft purrs."}
            {prefs.behaviorMode === "manual" && "Autonomous muttering paused; only speaks when prompted."}
          </p>
        </div>

        <div className="settings-footer">
          <button className="settings-btn reset" onClick={resetAll}>Reset to Factory Defaults</button>
          <button className="settings-btn done" onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}
