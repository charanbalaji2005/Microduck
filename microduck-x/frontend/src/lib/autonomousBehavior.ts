// Autonomous Behavior State Machine & Persona Interaction Layer
// The duck talks by itself after a quiet spell (POST /api/chat/idle: talk only, never a command) and
// keeps a behaviour mode for the page. Every line it says is chosen by the backend persona.

import { chat, postJson } from "./api";
import { playSoundEvent, type SoundEvent } from "../voice/events";

export type BehaviorState =
  | "IDLE"
  | "OBSERVING"
  | "GREETING"
  | "LISTENING"
  | "RESPONDING"
  | "EXPLORING"
  | "PLAYING"
  | "RESTING"
  | "PAUSED"
  | "EMERGENCY_STOP";

export type BehaviorMode = "curious" | "explore" | "play" | "social" | "rest" | "manual";

class AutonomousBehaviorManager {
  private state: BehaviorState = "IDLE";
  private mode: BehaviorMode = "curious";
  private enabled: boolean = true;
  private intervalSeconds: number = 30; // first idle line after ~30 s; each unanswered one waits longer
  private timer: number | null = null;
  private lastInteractionTime: number = Date.now();
  private listeners = new Set<(state: BehaviorState, mode: BehaviorMode) => void>();

  constructor() {
    this.scheduleNextThought();
  }

  public getState(): BehaviorState {
    return this.state;
  }

  public getMode(): BehaviorMode {
    return this.mode;
  }

  public isEnabled(): boolean {
    return this.enabled;
  }

  public setEnabled(enabled: boolean) {
    this.enabled = enabled;
    if (!enabled && this.timer) {
      window.clearTimeout(this.timer);
      this.timer = null;
    } else if (enabled) {
      this.scheduleNextThought();
    }
    this.notify();
  }

  public setMode(mode: BehaviorMode) {
    this.mode = mode;
    this.recordInteraction();
    if (mode === "rest") {
      this.setState("RESTING");
      playSoundEvent("resting");
    } else if (mode === "play") {
      this.setState("PLAYING");
      playSoundEvent("play");
    } else if (mode === "explore") {
      this.setState("EXPLORING");
      playSoundEvent("curiosity");
    } else {
      this.setState("IDLE");
    }
    this.notify();
  }

  public setState(next: BehaviorState) {
    if (this.state === next) return;
    this.state = next;
    this.notify();
  }

  public recordInteraction() {
    this.lastInteractionTime = Date.now();
    this.unanswered = 0;
    if (this.timer) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    if (this.state !== "EMERGENCY_STOP" && this.state !== "PAUSED") {
      this.state = "RESPONDING";
      this.notify();
      setTimeout(() => {
        if (this.state === "RESPONDING") {
          this.state = this.mode === "rest" ? "RESTING" : "IDLE";
          this.notify();
        }
      }, 3000);
    }
    this.scheduleNextThought();
  }

  public triggerSoundEvent(event: SoundEvent) {
    this.recordInteraction();
    return playSoundEvent(event);
  }

  public onWaveDetected() {
    if (this.state === "EMERGENCY_STOP") return;
    this.setState("GREETING");
    this.recordInteraction();
    playSoundEvent("greeting");
    void chat("Hello!");
  }

  public subscribe(cb: (state: BehaviorState, mode: BehaviorMode) => void) {
    this.listeners.add(cb);
    cb(this.state, this.mode);
    return () => { this.listeners.delete(cb); };
  }

  private notify() {
    this.listeners.forEach((cb) => cb(this.state, this.mode));
  }

  private scheduleNextThought() {
    if (!this.enabled || this.mode === "manual" || this.state === "EMERGENCY_STOP") return;
    if (this.timer) window.clearTimeout(this.timer);

    const jitter = Math.random() * 10 - 5;
    const delayMs = Math.min(180_000, Math.max(8000, (this.intervalSeconds + jitter) * 1000 * 1.6 ** this.unanswered));

    this.timer = window.setTimeout(() => {
      this.timer = null;
      if (!this.enabled || this.mode === "manual" || this.state === "EMERGENCY_STOP") return;
      if (Date.now() - this.lastInteractionTime >= delayMs * 0.8) {
        this.performAutonomousThought();
      }
      this.scheduleNextThought();
    }, delayMs);
  }

  /** What the page knows that the duck could talk about: the scenery, the background ducks. */
  public setHintProvider(fn: () => Record<string, unknown>) { this.hint = fn; }

  /** Any sign of life from the user (a click, a key) — quiets the duck without a state change. */
  public touch() {
    this.lastInteractionTime = Date.now();
    this.unanswered = 0;
  }

  private hint: () => Record<string, unknown> = () => ({});
  private unanswered = 0;

  private async performAutonomousThought() {
    if (this.state === "EMERGENCY_STOP" || this.mode === "rest") return;
    if (document.hidden) return; // nobody is looking
    this.setState("OBSERVING");
    try {
      // The duck speaks on its own: the reply arrives over /ws like any other. Talk only —
      // the backend never runs a command from this.
      await postJson("/api/chat/idle", { ...this.hint(), unanswered: this.unanswered });
      this.unanswered++;
    } catch {
      // Offline fallback
    } finally {
      setTimeout(() => {
        if (this.state === "OBSERVING") {
          this.setState("IDLE");
        }
      }, 4000);
    }
  }
}

export const behaviorManager = new AutonomousBehaviorManager();
