import { Component, type ReactNode } from "react";
import type { Kind } from "../../lib/chatContent";
import ExpandableResponse from "./ExpandableResponse";
import MarkdownRenderer from "./MarkdownRenderer";
import MathResponse from "./MathResponse";

// Picks how a reply is drawn from its kind. Whatever goes wrong while rendering (a half-written
// table mid-stream, odd TeX), the reply falls back to its plain text instead of breaking the chat.

class Fallback extends Component<{ text: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(prev: { text: string }) { if (prev.text !== this.props.text && this.state.failed) this.setState({ failed: false }); }
  render() { return this.state.failed ? <div className="md plain">{this.props.text}</div> : this.props.children; }
}

export default function ResponseRenderer({ text, kind, pending, expandable, expanded, onExpand, hiddenHint }:
  { text: string; kind: Kind; pending?: boolean; expandable: boolean; expanded: boolean; onExpand: (v: boolean) => void; hiddenHint?: string }) {
  const body = <MarkdownRenderer text={text} />;
  return (
    <Fallback text={text}>
      <ExpandableResponse enabled={expandable && !pending} expanded={expanded} onToggle={onExpand} hiddenHint={hiddenHint}>
        {kind === "math" && !pending ? <MathResponse text={text}>{body}</MathResponse> : body}
      </ExpandableResponse>
    </Fallback>
  );
}
