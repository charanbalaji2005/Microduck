import katex from "katex";
import "katex/dist/katex.min.css";
import { useMemo, type ReactNode } from "react";
import { equations } from "../../lib/chatContent";
import CopyButton from "./CopyButton";

// Mathematics through KaTeX: TeX in, HTML out. `trust: false` keeps \href, \url and friends from
// producing links or anything active, and a TeX error shows the source instead of breaking the reply.

function render(tex: string, displayMode: boolean) {
  try {
    return katex.renderToString(tex, { displayMode, throwOnError: false, trust: false, strict: "ignore", output: "htmlAndMathml", maxSize: 20, maxExpand: 200 });
  } catch {
    return null;
  }
}

export function MathInline({ tex }: { tex: string }) {
  const html = useMemo(() => render(tex, false), [tex]);
  return html ? <span className="math-inline" dangerouslySetInnerHTML={{ __html: html }} /> : <code>{tex}</code>;
}

export function MathBlock({ tex }: { tex: string }) {
  const html = useMemo(() => render(tex, true), [tex]);
  return (
    <div className="math-block">
      {html ? <div className="math-display" tabIndex={0} dangerouslySetInnerHTML={{ __html: html }} /> : <pre><code>{tex}</code></pre>}
      <CopyButton className="math-copy" text={tex} label="Copy equation" small />
    </div>
  );
}

/** A worked solution: the content, with the solution's own copy actions under it. */
export default function MathResponse({ text, children }: { text: string; children: ReactNode }) {
  const eqs = useMemo(() => equations(text), [text]);
  return (
    <div className="math-response">
      {children}
      {eqs.length > 0 && (
        <div className="math-actions">
          <CopyButton text={text} label="Copy solution" small />
          <CopyButton text={eqs.join("\n")} label={eqs.length > 1 ? `Copy ${eqs.length} equations` : "Copy equation"} small />
        </div>
      )}
    </div>
  );
}
