import { useEffect, useRef, useState } from "react";
import { copyRich, copyText, type CopyResult } from "../../lib/clipboard";
import Icon from "../Icon";

/** Copy state for a button: the label changes only after the browser reports the result. */
export function useCopy() {
  const [result, setResult] = useState<CopyResult | null>(null);
  const timer = useRef<number>();
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const run = async (text: string, html?: string) => {
    const r = html ? await copyRich(html, text) : await copyText(text);
    setResult(r);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setResult(null), 1800);
    return r;
  };
  const note = result === "copied" ? "Copied" : result === "copied-plain" ? "Copied as plain text" : result === "failed" ? "Copy failed — select and copy by hand" : null;
  return { run, result, note };
}

export default function CopyButton({ text, html, label = "Copy", small, className = "" }:
  { text: string; html?: string; label?: string; small?: boolean; className?: string }) {
  const { run, result, note } = useCopy();
  return (
    <button type="button" className={`rt-btn ${small ? "small" : ""} ${result ?? ""} ${className}`} onClick={() => run(text, html)}
            aria-label={label} title={label}>
      <Icon name={result === "copied" || result === "copied-plain" ? "check" : "copy"} />
      <span>{note ?? label}</span>
      <span className="sr-only" aria-live="polite">{note ?? ""}</span>
    </button>
  );
}
