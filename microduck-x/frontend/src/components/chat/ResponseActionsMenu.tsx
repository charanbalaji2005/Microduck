import { useEffect, useRef, useState } from "react";
import Icon, { type IconName } from "../Icon";

export interface MenuAction { label: string; icon: IconName; run: () => void | Promise<unknown> }

/** The "more" popover: a keyboard-navigable menu of the actions that fit this reply. */
export default function ResponseActionsMenu({ actions }: { actions: MenuAction[] }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => {
    if (!open) return;
    items.current[0]?.focus();
    const away = (e: PointerEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("pointerdown", away);
    return () => document.removeEventListener("pointerdown", away);
  }, [open]);

  if (!actions.length) return null;
  const onKey = (e: React.KeyboardEvent) => {
    const list = items.current.filter(Boolean) as HTMLButtonElement[];
    const at = list.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setOpen(false); wrap.current?.querySelector<HTMLButtonElement>(".rt-more")?.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); list[(at + 1) % list.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); list[(at - 1 + list.length) % list.length]?.focus(); }
  };

  return (
    <span className="rt-menu-wrap" ref={wrap} onKeyDown={onKey}>
      <button type="button" className="rt-btn icon rt-more" aria-haspopup="menu" aria-expanded={open} aria-label="More actions" title="More actions"
              onClick={() => setOpen(!open)}>
        <Icon name="more" size={16} />
      </button>
      {open && (
        <div className="rt-menu" role="menu">
          {actions.map((a, i) => (
            <button key={a.label} type="button" role="menuitem" ref={(el) => { items.current[i] = el; }}
                    onClick={() => { setOpen(false); void a.run(); }}>
              <Icon name={a.icon} size={16} /> {a.label}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}
