import type { ReactNode } from "react";
import { parseBlocks, parseInline, type Block, type Inline } from "../../lib/chatContent";
import CodeBlock from "./CodeBlock";
import { MathBlock, MathInline } from "./MathResponse";

// A reply's Markdown as React elements. Text from the model is never inserted as HTML; the only
// generated markup comes from KaTeX (TeX → HTML, trust off) and highlight.js (escaped source).

function inlines(xs: Inline[], key: string): ReactNode[] {
  return xs.map((x, i) => {
    const k = `${key}.${i}`;
    switch (x.t) {
      case "text": return x.v;
      case "br": return <br key={k} />;
      case "code": return <code key={k}>{x.v}</code>;
      case "math": return x.display ? <MathBlock key={k} tex={x.v} /> : <MathInline key={k} tex={x.v} />;
      case "strong": return <strong key={k}>{inlines(x.v, k)}</strong>;
      case "em": return <em key={k}>{inlines(x.v, k)}</em>;
      case "link": return <a key={k} href={x.href} target="_blank" rel="noopener noreferrer nofollow">{inlines(x.v, k)}</a>;
    }
  });
}

export const Inlines = ({ text }: { text: string }) => <>{inlines(parseInline(text), "i")}</>;

function block(b: Block, i: number, codeIndex: { n: number }): ReactNode {
  const k = `b${i}`;
  switch (b.t) {
    case "p": return <p key={k}><Inlines text={b.text} /></p>;
    case "h": return <p key={k} className={`md-h md-h${Math.min(b.level, 3)}`} role="heading" aria-level={Math.min(b.level + 2, 6)}><Inlines text={b.text} /></p>;
    case "quote": return <blockquote key={k}><Inlines text={b.text} /></blockquote>;
    case "hr": return <hr key={k} />;
    case "math": return <MathBlock key={k} tex={b.tex} />;
    case "code": return <CodeBlock key={k} info={b.info} index={++codeIndex.n} />;
    case "list": {
      const items = b.items.map((it, j) => <li key={j}><Inlines text={it} /></li>);
      return b.ordered ? <ol key={k} start={b.start !== 1 ? b.start : undefined}>{items}</ol> : <ul key={k}>{items}</ul>;
    }
    case "table": return (
      <div key={k} className="md-table" tabIndex={0} role="region" aria-label="table">
        <table>
          <thead><tr>{b.head.map((c, j) => <th key={j} style={{ textAlign: b.align[j] ?? undefined }}><Inlines text={c} /></th>)}</tr></thead>
          <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, n) => <td key={n} style={{ textAlign: b.align[n] ?? undefined }}><Inlines text={c} /></td>)}</tr>)}</tbody>
        </table>
      </div>
    );
  }
}

export default function MarkdownRenderer({ text }: { text: string }) {
  const codeIndex = { n: 0 };
  return <div className="md">{parseBlocks(text).map((b, i) => block(b, i, codeIndex))}</div>;
}
