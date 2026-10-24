import { useSyncExternalStore } from "react";
import { cancelAll } from "./api";
import { send } from "./ws";

// The one way the page moves the duck. The keyboard (WASD, arrows), the on-screen pad and Space
// all land here, and here becomes a twist streamed to the control service as `teleop` — whose
// executor is also what runs chat and voice commands ("walk forward", "come here"). Same executor,
// same robot.move into robotd, same velstand policy stepping the MuJoCo legs. Nothing in the page
// animates a leg: the legs move because the policy walks, so they cannot slide or drift.

export type Drive = "forward" | "backward" | "left" | "right" | "strafeLeft" | "strafeRight";
export interface Twist { vx: number; vy: number; vyaw: number }
export interface Transport {
  teleop(t: Twist): void;
  /** stop whatever the executor is running (a chat "walk forward", a route) */
  cancel(): void;
  every(fn: () => void, ms: number): () => void;
}

// Speeds the gait steps for: the executor raises anything slower to these anyway
// (Settings.gait_min_*), so asking for them is asking for what will happen.
export const SPEED = { forward: 0.3, backward: 0.38, strafe: 0.12, turn: 0.9 };
const FAST = 1.33; // with Shift; the executor clamps to vx 0.4, vyaw 1.0
const RESEND_MS = 100; // robotd forgets a twist after 500 ms, the executor after 350 ms

export class Locomotion {
  private held = new Map<Drive, Set<string>>(); // drive → the sources holding it
  private fast = false;
  private stopPump: (() => void) | null = null;
  private listeners = new Set<() => void>();
  private snapshot: ReadonlySet<Drive> = new Set();

  constructor(private io: Transport) {}

  press(drive: Drive, source = "ui") {
    const s = this.held.get(drive) ?? new Set<string>();
    if (s.has(source)) return;
    s.add(source);
    this.held.set(drive, s);
    this.changed();
  }

  release(drive: Drive, source = "ui") {
    const s = this.held.get(drive);
    if (!s?.delete(source)) return;
    if (!s.size) this.held.delete(drive);
    this.changed();
  }

  /** Let go of everything one source holds (its window lost focus, its pad unmounted). */
  releaseSource(source: string) {
    let any = false;
    for (const [d, s] of this.held) if (s.delete(source)) { any = true; if (!s.size) this.held.delete(d); }
    if (any) this.changed();
  }

  setFast(on: boolean) {
    if (on === this.fast) return;
    this.fast = on;
    if (this.held.size) this.changed();
  }

  /** Stop now: every held direction, and anything the executor is running. Talking goes on. */
  stop() {
    this.held.clear();
    this.stopPump?.();
    this.stopPump = null;
    this.io.teleop({ vx: 0, vy: 0, vyaw: 0 });
    this.io.cancel();
    this.publish();
  }

  twist(): Twist {
    const on = (d: Drive) => this.held.has(d);
    const k = this.fast ? FAST : 1;
    const f = Number(on("forward")) - Number(on("backward"));
    const side = Number(on("strafeLeft")) - Number(on("strafeRight"));
    const turn = Number(on("left")) - Number(on("right"));
    return {
      vx: f > 0 ? SPEED.forward * k : f < 0 ? -SPEED.backward * k : 0,
      vy: side * SPEED.strafe * k,
      vyaw: turn * SPEED.turn * k,
    };
  }

  /** The directions held right now (a new set object whenever it changes). */
  active(): ReadonlySet<Drive> { return this.snapshot; }

  subscribe(l: () => void) { this.listeners.add(l); return () => { this.listeners.delete(l); }; }

  private changed() {
    const t = this.twist();
    this.io.teleop(t);
    const moving = t.vx !== 0 || t.vy !== 0 || t.vyaw !== 0;
    if (moving && !this.stopPump) this.stopPump = this.io.every(() => this.io.teleop(this.twist()), RESEND_MS);
    if (!moving && this.stopPump) { this.stopPump(); this.stopPump = null; }
    this.publish();
  }

  private publish() {
    this.snapshot = new Set(this.held.keys());
    this.listeners.forEach((l) => l());
  }
}

export const locomotion = new Locomotion({
  teleop: (t) => send({ type: "teleop", ...t }),
  cancel: () => { void cancelAll().catch(() => undefined); },
  every: (fn, ms) => { const h = window.setInterval(fn, ms); return () => window.clearInterval(h); },
});

export function useDrive(): ReadonlySet<Drive> {
  return useSyncExternalStore((l) => locomotion.subscribe(l), () => locomotion.active());
}
