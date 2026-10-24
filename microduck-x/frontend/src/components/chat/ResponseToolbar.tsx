import { useEffect, useState } from "react";
import { equations, speakable, type CodeInfo, type Kind } from "../../lib/chatContent";
import { canReadAloud, downloadText, onReading, pauseReading, readAloud, stopReading } from "../../lib/clipboard";
import { stopTalking } from "../../voice/events";
import { codeFileName } from "../../lib/chatContent";
import { clipboardHtml } from "../../lib/highlight";
import Icon from "../Icon";
import { useCopy } from "./CopyButton";
import FeedbackControls from "./FeedbackControls";
import ResponseActionsMenu, { type MenuAction } from "./ResponseActionsMenu";

// The actions under a brain reply: the common ones always, the rest only where they fit the
// reply's kind (code, maths, long answers). Shown on hover or focus on desktop, always on touch.

export interface ToolbarProps {
  id: string; kind: Kind; text: string; code: CodeInfo[]; rating?: "up" | "down" | null;
  expandable: boolean; expanded: boolean; setExpanded: (v: boolean) => void;
  onShare: () => void; onRegenerate?: () => void;
  /** say this answer in the duck's own voice */
  onSpeak?: () => void;
}

export default function ResponseToolbar(p: ToolbarProps) {
  const copy = useCopy();
  const [reading, setReading] = useState(false);
  const [paused, setPaused] = useState(false);
  useEffect(() => onReading((id, isPaused) => { setReading(id === p.id); setPaused(id === p.id && isPaused); }), [p.id]);

  const copyLabel = p.kind === "math" ? "Copy solution" : p.kind === "long" || p.kind === "list" ? "Copy full answer" : "Copy response";
  const allCode = p.code.map((c) => c.code).join("\n\n");
  const eqs = p.kind === "math" ? equations(p.text) : [];
  const menu: MenuAction[] = [];
  if (p.code.length) {
    menu.push({ label: p.code.length > 1 ? `Copy all code (${p.code.length} blocks)` : "Copy code", icon: "copy", run: () => copy.run(allCode) });
    menu.push({ label: "Copy code with formatting", icon: "copy", run: () => copy.run(allCode, clipboardHtml(p.code)) });
    menu.push({ label: p.code.length > 1 ? `Download code (${p.code.length} files)` : `Download ${codeFileName(p.code[0])}`, icon: "download",
      run: () => p.code.forEach((c, i) => setTimeout(() => downloadText(codeFileName(c, i + 1), c.code), i * 250)) });
  }
  if (eqs.length) menu.push({ label: eqs.length > 1 ? `Copy equations (${eqs.length})` : "Copy equation", icon: "copy", run: () => copy.run(eqs.join("\n")) });
  if (p.expandable) menu.push({ label: p.expanded ? "Collapse response" : p.kind === "math" ? "Expand worked steps" : "Expand response",
    icon: p.expanded ? "collapse" : "expand", run: () => p.setExpanded(!p.expanded) });
  if (p.kind !== "conversation" && p.kind !== "error" && p.kind !== "action")
    menu.push({ label: "Download as Markdown", icon: "download", run: () => downloadText("microduck-answer.md", p.text + "\n", "text/markdown") });

  const toggleRead = () => {
    if (reading) { stopReading(); stopTalking(); return; }
    stopTalking(); // the chirps and the words would talk over each other
    readAloud(p.id, speakable(p.text));
  };

  return (
    <div className="rt-bar" role="toolbar" aria-label="Answer actions">
      <button type="button" className={`rt-btn icon ${copy.result ?? ""}`} onClick={() => copy.run(p.text)} aria-label={copyLabel} title={copyLabel}>
        <Icon name={copy.result === "copied" || copy.result === "copied-plain" ? "check" : "copy"} size={16} />
      </button>
      <button type="button" className="rt-btn icon" onClick={p.onShare} aria-label="Share" title="Share"><Icon name="share" size={16} /></button>
      <a
        href="https://github.com/charanbalaji2005/Microduck-AI"
        target="_blank"
        rel="noopener noreferrer"
        className="rt-btn icon"
        aria-label="GitHub Repository"
        title="Microduck-AI on GitHub"
      >
        <Icon name="github" size={16} />
      </a>
      {p.onSpeak && (
        <button type="button" className="rt-btn icon" onClick={p.onSpeak} aria-label="Play in the duck's voice" title="Play in the duck's voice">
          <Icon name="duck" size={16} />
        </button>
      )}
      {canReadAloud() && (
        <button type="button" className={`rt-btn icon ${reading ? "on" : ""}`} onClick={toggleRead}
                aria-label={reading ? "Stop audio" : "Read aloud"} title={reading ? "Stop audio" : "Read aloud (your browser's voice)"}>
          <Icon name={reading ? "stop" : "volume"} size={16} />
        </button>
      )}
      {reading && (
        <button type="button" className="rt-btn icon on" onClick={() => pauseReading(!paused)}
                aria-label={paused ? "Resume reading" : "Pause reading"} title={paused ? "Resume" : "Pause"}>
          <Icon name={paused ? "play" : "pause"} size={16} />
        </button>
      )}
      {p.onRegenerate && (
        <button type="button" className="rt-btn icon" onClick={p.onRegenerate} aria-label="Regenerate response" title="Regenerate">
          <Icon name="refresh" size={16} />
        </button>
      )}
      <FeedbackControls id={p.id} rating={p.rating} />
      <ResponseActionsMenu actions={menu} />
      <span className={`rt-note ${copy.result === "failed" ? "bad" : ""}`} role="status" aria-live="polite">{copy.note ?? ""}</span>
    </div>
  );
}
