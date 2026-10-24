import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import Icon from "../Icon";

// A long answer, collapsed to its beginning inside its own container. Whether it collapses is
// decided by the caller (content length and structure) and confirmed by measuring: an answer
// that fits is never shown with an "Expand" it does not need.

export default function ExpandableResponse({ enabled, expanded, onToggle, hiddenHint, children }:
  { enabled: boolean; expanded: boolean; onToggle: (next: boolean) => void; hiddenHint?: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(enabled);
  useLayoutEffect(() => {
    if (!enabled || !ref.current) { setOverflows(false); return; }
    const el = ref.current;
    setOverflows(el.scrollHeight > el.clientHeight + 48 || expanded);
  }, [enabled, expanded, children]);

  if (!enabled) return <>{children}</>;
  const collapsed = overflows && !expanded;
  return (
    <div className={`expandable ${collapsed ? "collapsed" : ""}`}>
      <div className="expandable-body" ref={ref}>{children}</div>
      {overflows && (
        <button type="button" className="expand-btn" aria-expanded={expanded}
                onClick={() => {
                  onToggle(!expanded);
                  if (expanded) ref.current?.closest(".h-duck")?.scrollIntoView({ block: "nearest" });
                }}>
          <Icon name={expanded ? "collapse" : "expand"} size={16} />
          {expanded ? "Collapse" : `Show full response${hiddenHint ? ` · ${hiddenHint}` : ""}`}
        </button>
      )}
    </div>
  );
}
