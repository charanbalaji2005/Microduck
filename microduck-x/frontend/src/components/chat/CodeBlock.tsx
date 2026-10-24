import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { codeFileName, type CodeInfo } from "../../lib/chatContent";
import { downloadText } from "../../lib/clipboard";
import { highlight, languageLabel } from "../../lib/highlight";
import Icon from "../Icon";

// A unified syntax-highlighted code container:
// Header with language badge, optional filename, Copy (with feedback indicator),
// Expand/Fullscreen overlay, and line numbers for multiline listings.

function Lines({ info, numbers, html }: { info: CodeInfo; numbers: boolean; html: string | null }) {
  const lines = info.code.split("\n");
  const count = lines.length;
  const showNumbers = numbers && count > 1;

  return (
    <div className="cb-body">
      {showNumbers && (
        <div className="cb-gutter" aria-hidden="true">
          {lines.map((_, i) => (
            <span key={i} className="cb-line-num">{i + 1}</span>
          ))}
        </div>
      )}
      <pre className="cb-pre" tabIndex={0} aria-label={`${languageLabel(info.language)} source code`}>
        {html !== null ? (
          <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <code>{info.code}</code>
        )}
      </pre>
    </div>
  );
}

export default function CodeBlock({ info, index = 1 }: { info: CodeInfo; index?: number }) {
  const [numbers, setNumbers] = useState(true);
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const copyTimer = useRef<number>();
  const cardRef = useRef<HTMLDivElement>(null);
  const html = useMemo(() => highlight(info.code, info.language), [info.code, info.language]);
  const name = codeFileName(info, index);

  useEffect(() => () => window.clearTimeout(copyTimer.current), []);

  useEffect(() => {
    if (!expanded) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setExpanded(false);
      }
    };
    window.addEventListener("keydown", handleKeyDown, true);
    return () => window.removeEventListener("keydown", handleKeyDown, true);
  }, [expanded]);

  const handleCopy = async () => {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(info.code);
      } else {
        const ta = document.createElement("textarea");
        ta.value = info.code;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.warn("Clipboard write failed:", err);
    }
  };

  const renderHeader = (isModal: boolean) => (
    <div className="cb-head">
      <div className="cb-meta">
        <span className="cb-lang">{languageLabel(info.language)}</span>
        {info.filename && <span className="cb-file" title={info.filename}>{info.filename}</span>}
        {!info.closed && <span className="cb-live">writing…</span>}
      </div>
      <div className="cb-actions">
        <button
          type="button"
          className={`cb-btn icon-only ${numbers ? "on" : ""}`}
          onClick={() => setNumbers(!numbers)}
          aria-pressed={numbers}
          aria-label={numbers ? "Hide line numbers" : "Show line numbers"}
          title={numbers ? "Hide line numbers" : "Show line numbers"}
        >
          <Icon name="lines" size={14} />
        </button>
        <button
          type="button"
          className={`cb-btn ${copied ? "copied" : ""}`}
          onClick={handleCopy}
          aria-label={copied ? "Code copied" : "Copy code"}
          title={copied ? "Copied!" : "Copy code"}
        >
          <Icon name={copied ? "check" : "copy"} size={14} />
          <span className="cb-btn-text">{copied ? "Copied!" : "Copy"}</span>
        </button>
        <button
          type="button"
          className="cb-btn icon-only"
          onClick={() => downloadText(name, info.code)}
          aria-label={`Download ${name}`}
          title={`Download ${name}`}
        >
          <Icon name="download" size={14} />
        </button>
        {isModal ? (
          <button
            type="button"
            className="cb-btn cb-close-btn"
            onClick={() => setExpanded(false)}
            aria-label="Collapse view"
            title="Collapse view (Esc)"
          >
            <Icon name="close" size={14} />
            <span className="cb-btn-text">Close</span>
          </button>
        ) : (
          <button
            type="button"
            className="cb-btn"
            onClick={() => setExpanded(true)}
            aria-label="Expand code view"
            title="Expand in full overlay"
          >
            <Icon name="maximize" size={14} />
            <span className="cb-btn-text">Expand</span>
          </button>
        )}
      </div>
    </div>
  );

  return (
    <div className="code-card" ref={cardRef}>
      {renderHeader(false)}
      <Lines info={info} numbers={numbers} html={html} />
      {expanded &&
        createPortal(
          <div
            className="cb-modal"
            role="dialog"
            aria-modal="true"
            aria-label={`${info.filename ?? languageLabel(info.language)} — expanded view`}
            onClick={(e) => {
              if (e.target === e.currentTarget) setExpanded(false);
            }}
          >
            <div className="code-card big">
              {renderHeader(true)}
              <Lines info={info} numbers={numbers} html={html} />
            </div>
          </div>,
          document.body
        )}
    </div>
  );
}
