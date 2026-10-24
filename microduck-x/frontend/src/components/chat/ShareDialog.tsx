import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CodeInfo } from "../../lib/chatContent";
import { languageLabel } from "../../lib/highlight";
import Icon from "../Icon";
import CopyButton from "./CopyButton";

// Sharing a reply. There is no conversation-link service behind this page, so no link is made up:
// what is shared is the text itself — the answer, or one code block — through the system share
// sheet where the browser has one, or copied. Only that text: never the question history, the
// system prompt, tool data or anything from the service.

export default function ShareDialog({ answer, code, onClose }: { answer: string; code: CodeInfo[]; onClose: () => void }) {
  const [pick, setPick] = useState(0); // 0: the answer; n: code block n
  const [note, setNote] = useState<string | null>(null);
  const text = pick === 0 ? answer : code[pick - 1].code;
  const first = useRef<HTMLButtonElement>(null);
  const canShare = typeof navigator !== "undefined" && typeof navigator.share === "function";

  useEffect(() => {
    first.current?.focus();
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } };
    window.addEventListener("keydown", esc, true);
    return () => window.removeEventListener("keydown", esc, true);
  }, [onClose]);

  const share = async () => {
    try {
      await navigator.share({ title: pick === 0 ? "Microduck's answer" : code[pick - 1].filename ?? "Code from Microduck", text });
      setNote("Shared.");
    } catch (e) {
      // AbortError: the user closed the share sheet — not a failure worth shouting about
      setNote((e as Error)?.name === "AbortError" ? null : "This browser could not share it; copy it instead.");
    }
  };

  return createPortal(
    <div className="share-modal" role="dialog" aria-modal="true" aria-labelledby="share-title" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="share-card">
        <div className="share-head">
          <h2 id="share-title">Share</h2>
          <button type="button" className="rt-btn icon" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button>
        </div>
        {code.length > 0 && (
          <div className="share-pick" role="radiogroup" aria-label="What to share">
            <label><input type="radio" checked={pick === 0} onChange={() => setPick(0)} /> The whole answer</label>
            {code.map((c, i) => (
              <label key={i}><input type="radio" checked={pick === i + 1} onChange={() => setPick(i + 1)} />
                {" "}{c.filename ?? `${languageLabel(c.language)} code${code.length > 1 ? ` #${i + 1}` : ""}`}</label>
            ))}
          </div>
        )}
        <textarea readOnly value={text} rows={8} aria-label="Text to share" onFocus={(e) => e.currentTarget.select()} />
        <p className="share-note">Shares this text only — not a link, and nothing else from the conversation.</p>
        <div className="share-actions">
          {canShare && (
            <button type="button" className="h-yellow" ref={first} onClick={share}><Icon name="share" size={16} /> Share…</button>
          )}
          <CopyButton text={text} label="Copy text" />
        </div>
        {note && <p className="share-note" role="status">{note}</p>}
      </div>
    </div>, document.body);
}
