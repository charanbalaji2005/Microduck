import { useState } from "react";
import { chatFeedback } from "../../lib/api";
import { setState } from "../../lib/store";
import Icon from "../Icon";

/** Thumbs up / down. Recorded in the control service's event log (nowhere else); a second click clears it. */
export default function FeedbackControls({ id, rating }: { id: string; rating?: "up" | "down" | null }) {
  const [failed, setFailed] = useState(false);
  const rate = async (r: "up" | "down") => {
    const next = rating === r ? null : r;
    try {
      await chatFeedback(id, next);
      setFailed(false);
      setState((s) => ({ chat: s.chat.map((m) => m.id === id ? { ...m, feedback: next } : m) }));
    } catch {
      setFailed(true);
    }
  };
  return (
    <span className="fb" role="group" aria-label="Rate this answer">
      <button type="button" className={`rt-btn icon ${rating === "up" ? "on" : ""}`} aria-pressed={rating === "up"}
              aria-label="Good answer" title="Good answer" onClick={() => rate("up")}><Icon name="thumb-up" size={16} /></button>
      <button type="button" className={`rt-btn icon ${rating === "down" ? "on" : ""}`} aria-pressed={rating === "down"}
              aria-label="Bad answer" title="Bad answer" onClick={() => rate("down")}><Icon name="thumb-down" size={16} /></button>
      {failed && <span className="fb-err" role="status">not saved</span>}
    </span>
  );
}
