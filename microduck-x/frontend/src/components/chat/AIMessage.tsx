import { useMemo, useState } from "react";
import { classify, cleanAnswer, codeBlocks, isLong, type Kind } from "../../lib/chatContent";
import type { ChatMsg } from "../../lib/store";
import ResponseRenderer from "./ResponseRenderer";
import ResponseToolbar from "./ResponseToolbar";
import ShareDialog from "./ShareDialog";
import { sayReply } from "../../voice/events";
import { unlockVoice } from "../../voice/duckVoice";
import "./chat.css";

// One reply from the Groq brain: the duck-speak strip (personality, kept apart from the answer),
// the answer drawn by its kind, what it started, its sources, follow-ups and the toolbar.

const KIND_LABEL: Record<Kind, string> = { conversation: "", math: "MATH", code: "CODE", long: "LONG ANSWER", list: "LIST", error: "ERROR", action: "ACTION" };

export default function AIMessage({ m, duckSpeak, busy, onAsk, onRegenerate, onConfirm }:
  { m: ChatMsg; duckSpeak: boolean; busy: boolean; onAsk: (q: string) => void; onRegenerate: (q: string) => void; onConfirm?: (ok: boolean) => void }) {
  const [expanded, setExpanded] = useState(false);
  const [sharing, setSharing] = useState(false);
  const text = useMemo(() => cleanAnswer(m.text), [m.text]);
  const kind = useMemo(() => m.pending ? classify(text) : classify(text, m.structured, m.error), [text, m.structured, m.error, m.pending]);
  const code = useMemo(() => m.pending ? [] : codeBlocks(text), [text, m.pending]);
  const expandable = !m.pending && isLong(text);
  const lines = text.split("\n").length;

  return (
    <div className={`h-duck ai kind-${kind}${m.pending ? " pending" : ""}${m.error ? " failed" : ""}`}>
      {((duckSpeak && m.brain) || (!m.brain && m.babble)) && m.babble && !m.pending && (
        <div className="ai-babble" title="Duck-speak: the duck's own noises, not a translation"><span aria-hidden="true">🦆</span> {m.babble}</div>
      )}
      {m.pending && !text ? (
        <div className="h-thinking" aria-label="thinking"><i /><i /><i /> thinking</div>
      ) : (
        <div className="h-text ai-answer">
          <ResponseRenderer text={text} kind={kind} pending={m.pending} expandable={expandable} expanded={expanded}
                            onExpand={setExpanded} hiddenHint={`${lines} lines`} />
          {m.pending && <span className="h-caret" />}
        </div>
      )}
      {m.actions && (
        <div className="h-acts">
          {m.actions.map((a, i) => (
            <span key={i} className={a.status === "rejected" ? "warn" : ""}>
              {a.status === "rejected" ? "refused" : a.status === "nothing to do" ? "said" : "started"} {a.action.replace(/_/g, " ")}
            </span>
          ))}
        </div>
      )}
      {m.sources && (
        <div className="h-sources">
          <span>Looked up — sources:</span>
          {m.sources.filter((s) => /^https?:\/\//i.test(s.url)).map((s) => (
            <a key={s.url} href={s.url} target="_blank" rel="noopener noreferrer nofollow">{s.title}</a>
          ))}
        </div>
      )}
      {!m.pending && m.followups && m.followups.length > 0 && (
        <div className="ai-followups" aria-label="Suggested follow-up questions">
          {m.followups.map((q) => <button key={q} type="button" disabled={busy} onClick={() => onAsk(q)}>{q}</button>)}
        </div>
      )}
      {m.retry && (
        <div className="h-confirm">
          <button className="h-yellow" disabled={busy} onClick={() => onAsk(m.retry!)}>Try again</button>
        </div>
      )}
      {m.confirm && onConfirm && (
        <div className="h-confirm">
          <button className="h-yellow" onClick={() => onConfirm(true)}>Yes, do it</button>
          <button className="h-dark" onClick={() => onConfirm(false)}>No</button>
        </div>
      )}
      {!m.pending && !m.error && text && (
        <ResponseToolbar id={m.id} kind={kind} text={text} code={code} rating={m.feedback}
                         expandable={expandable} expanded={expanded} setExpanded={setExpanded}
                         onShare={() => setSharing(true)}
                         onSpeak={() => { unlockVoice(); sayReply(m.mood, m.babble, m.text); }}
                         onRegenerate={m.question && !busy ? () => onRegenerate(m.question!) : undefined} />
      )}
      {!m.pending && (KIND_LABEL[kind] || m.mood) && (
        <div className="h-mood-badge">{[KIND_LABEL[kind], (m.mood ?? "").toUpperCase()].filter(Boolean).join(" · ")}</div>
      )}
      {sharing && <ShareDialog answer={text} code={code} onClose={() => setSharing(false)} />}
    </div>
  );
}
