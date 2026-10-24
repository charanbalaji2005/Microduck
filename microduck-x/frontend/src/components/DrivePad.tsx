import { useState, useEffect, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { locomotion, useDrive, type Drive } from "../lib/locomotion";

// On-screen walk controls: hold a direction to walk, as with the keys. Every button drives the
// same locomotion controller as WASD / the arrows, so the two can be mixed freely.

const SVG_ICONS: Record<string, ReactNode> = {
  forward: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="18 15 12 9 6 15" />
    </svg>
  ),
  left: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <polyline points="3 3 3 8 8 8" />
    </svg>
  ),
  right: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 12a9 9 0 1 1-9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <polyline points="21 3 21 8 16 8" />
    </svg>
  ),
  backward: (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  ),
  stop: (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="#ef4444" stroke="none">
      <rect x="5" y="5" width="14" height="14" rx="2.5" />
    </svg>
  ),
  dpad: (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="12 2 15 7 9 7 12 2" fill="currentColor" stroke="none" />
      <polygon points="12 22 15 17 9 17 12 22" fill="currentColor" stroke="none" />
      <polygon points="2 12 7 9 7 15 2 12" fill="currentColor" stroke="none" />
      <polygon points="22 12 17 9 17 15 22 12" fill="currentColor" stroke="none" />
      <circle cx="12" cy="12" r="2.5" fill="currentColor" stroke="none" />
    </svg>
  ),
  close: (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  ),
};

const BUTTONS: { drive: Drive; icon: string; label: string; keys: string; area: string }[] = [
  { drive: "forward", icon: "forward", label: "Walk forward", keys: "W / ↑", area: "up" },
  { drive: "left", icon: "left", label: "Turn left", keys: "A / ←", area: "left" },
  { drive: "right", icon: "right", label: "Turn right", keys: "D / →", area: "right" },
  { drive: "backward", icon: "backward", label: "Walk backward", keys: "S / ↓", area: "down" },
];

/** `open` / `onOpenChange` make it controllable (the phone layout's nav opens it); otherwise it keeps its own state. */
export default function DrivePad({ open: openProp, onOpenChange }: { open?: boolean; onOpenChange?: (open: boolean) => void } = {}) {
  const [openOwn, setOpenOwn] = useState(false);
  const open = openProp ?? openOwn;
  const setOpen = (v: boolean) => { setOpenOwn(v); onOpenChange?.(v); };
  const active = useDrive();
  // A held button must never keep the duck walking: let go when the page loses focus or is
  // hidden (an app switch, a notification shade, a phone call), and when the pad goes away.
  useEffect(() => {
    const letGo = () => locomotion.releaseSource("pad");
    const hidden = () => { if (document.hidden) letGo(); };
    window.addEventListener("blur", letGo);
    window.addEventListener("pagehide", letGo);
    document.addEventListener("visibilitychange", hidden);
    return () => {
      window.removeEventListener("blur", letGo);
      window.removeEventListener("pagehide", letGo);
      document.removeEventListener("visibilitychange", hidden);
      letGo();
    };
  }, []);
  useEffect(() => { if (!open) locomotion.releaseSource("pad"); }, [open]);

  const hold = (drive: Drive) => ({
    onPointerDown: (e: PointerEvent<HTMLButtonElement>) => {
      if (e.button !== 0) return;
      e.preventDefault(); // no text selection, no focus-scroll, no long-press menu
      e.currentTarget.setPointerCapture(e.pointerId);
      locomotion.press(drive, "pad");
    },
    onPointerUp: () => locomotion.release(drive, "pad"),
    onPointerCancel: () => locomotion.release(drive, "pad"),
    onLostPointerCapture: () => locomotion.release(drive, "pad"),
    onKeyDown: (e: KeyboardEvent) => { if (e.key === "Enter" && !e.repeat) locomotion.press(drive, "pad"); },
    onKeyUp: (e: KeyboardEvent) => { if (e.key === "Enter") locomotion.release(drive, "pad"); },
    onBlur: () => locomotion.release(drive, "pad"),
    onContextMenu: (e: { preventDefault: () => void }) => e.preventDefault(),
  });

  return (
    <div className={`dp-container ${open ? "open" : "minimized"}`}>
      {open ? (
        <nav className="drive-pad" aria-label="Walk controls">
          <button
            type="button"
            className="dp-minimize-btn"
            onClick={() => setOpen(false)}
            title="Minimize controls"
            aria-label="Minimize walk controls"
          >
            {SVG_ICONS.close}
          </button>
          {BUTTONS.map((b) => (
            <button
              key={b.drive}
              type="button"
              className={`dp-${b.area} ${active.has(b.drive) ? "on" : ""}`}
              aria-label={`${b.label} (hold; keys ${b.keys})`}
              aria-pressed={active.has(b.drive)}
              title={`${b.label} — hold (${b.keys})`}
              {...hold(b.drive)}
            >
              {SVG_ICONS[b.icon]}
            </button>
          ))}
          <button
            type="button"
            className="dp-stop"
            aria-label="Stop moving (Space)"
            title="Stop moving (Space)"
            onClick={() => locomotion.stop()}
          >
            {SVG_ICONS.stop}
          </button>
        </nav>
      ) : (
        <button
          type="button"
          className="dp-launcher-btn"
          onClick={() => setOpen(true)}
          title="Walk controls (D-Pad)"
          aria-label="Open walk controls"
        >
          {SVG_ICONS.dpad}
        </button>
      )}
    </div>
  );
}
