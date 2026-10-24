// A chat reply's content: parsed into blocks once, for the renderer, the toolbar (what can be
// copied, downloaded, read aloud) and the expand/collapse decision. Pure functions, no DOM.
// Tolerant of partial text — a reply still streaming in may stop mid-fence or mid-equation.

export type Kind = "conversation" | "math" | "code" | "long" | "list" | "error" | "action";
export const KINDS: Kind[] = ["conversation", "math", "code", "long", "list", "error", "action"];

export interface CodeInfo { language: string; filename: string | null; code: string; closed: boolean }

export type Block =
  | { t: "p"; text: string }
  | { t: "h"; level: number; text: string }
  | { t: "list"; ordered: boolean; start: number; items: string[] }
  | { t: "quote"; text: string }
  | { t: "hr" }
  | { t: "code"; info: CodeInfo }
  | { t: "math"; tex: string }
  | { t: "table"; head: string[]; align: ("left" | "right" | "center" | null)[]; rows: string[][] };

/** What brain.py's `structure()` sends; checked before use (see `structuredOf`). */
export interface Structured { kind: Kind; summary: string; code: { language: string; filename: string | null; lines: number }[]; math: number; length: number; lines: number }

const FOLLOWUPS = /^\s*>>\s*follow-?ups?\s*:.*$/gim;
const FENCE_OPEN = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)(.*)$/;
const FILENAME = /(?:title|file(?:name)?)\s*=\s*["']?([\w./-]{1,80})/;

/** The answer as the user should see and copy it: without the model's follow-ups line. */
export function cleanAnswer(text: string) {
  return text.replace(/\r\n?/g, "\n").replace(FOLLOWUPS, "").replace(/\n{3,}/g, "\n\n").trimEnd();
}

function splitRow(line: string) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  return s.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"));
}
const isSep = (line: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);

export function parseBlocks(input: string): Block[] {
  const lines = cleanAnswer(input).split("\n");
  const out: Block[] = [];
  let para: string[] = [];
  let list: Extract<Block, { t: "list" }> | null = null;
  const flushPara = () => { if (para.length) out.push({ t: "p", text: para.join("\n") }); para = []; };
  const flushList = () => { if (list) out.push(list); list = null; };
  const flush = () => { flushPara(); flushList(); };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const fence = FENCE_OPEN.exec(line);
    if (fence) {
      flush();
      const body: string[] = [];
      let closed = false;
      while (++i < lines.length) {
        if (/^\s*(`{3,}|~{3,})\s*$/.test(lines[i])) { closed = true; break; }
        body.push(lines[i]);
      }
      const fname = FILENAME.exec(fence[3]);
      out.push({ t: "code", info: { language: (fence[2] || "text").toLowerCase(), filename: fname ? fname[1] : null, code: body.join("\n"), closed } });
      continue;
    }
    const trimmed = line.trim();
    // displayed maths: $$ … $$ or \[ … \] on lines of their own (one line or several)
    const mathOpen = /^(\$\$|\\\[)/.exec(trimmed);
    if (mathOpen && !(trimmed.length > 2 && /^\$\$.*\$\$.+/.test(trimmed))) {
      const close = mathOpen[1] === "$$" ? "$$" : "\\]";
      let tex = trimmed.slice(2);
      let done = tex.endsWith(close) && tex.length >= close.length;
      if (done) tex = tex.slice(0, -close.length);
      let j = i;
      while (!done && ++j < lines.length) {
        const l = lines[j];
        if (l.trim().endsWith(close)) { tex += "\n" + l.trim().slice(0, -close.length); done = true; }
        else tex += "\n" + l;
      }
      if (done) { flush(); out.push({ t: "math", tex: tex.trim() }); i = j; continue; }
      // unclosed (still streaming): fall through and show it as text for now
    }
    if (!trimmed) { flush(); continue; }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { flush(); out.push({ t: "hr" }); continue; }
    const h = /^(#{1,6})\s+(.*)$/.exec(trimmed);
    if (h) { flush(); out.push({ t: "h", level: h[1].length, text: h[2].replace(/\s#+$/, "") }); continue; }
    if (trimmed.includes("|") && i + 1 < lines.length && isSep(lines[i + 1])) {
      flush();
      const head = splitRow(line);
      const align = splitRow(lines[i + 1]).map((c) => c.startsWith(":") && c.endsWith(":") ? "center" : c.endsWith(":") ? "right" : c.startsWith(":") ? "left" : null);
      const rows: string[][] = [];
      i += 1;
      while (i + 1 < lines.length && lines[i + 1].includes("|") && lines[i + 1].trim()) rows.push(splitRow(lines[++i]));
      out.push({ t: "table", head, align, rows: rows.map((r) => head.map((_, k) => r[k] ?? "")) });
      continue;
    }
    if (trimmed.startsWith(">")) {
      flush();
      const q = [trimmed.replace(/^>\s?/, "")];
      while (i + 1 < lines.length && lines[i + 1].trim().startsWith(">")) q.push(lines[++i].trim().replace(/^>\s?/, ""));
      out.push({ t: "quote", text: q.join("\n") });
      continue;
    }
    const bullet = /^\s*[-*+•]\s+(.*)$/.exec(line);
    const numbered = /^\s*(\d+)[.)]\s+(.*)$/.exec(line);
    if (bullet || numbered) {
      flushPara();
      const ordered = !!numbered;
      if (list && list.ordered !== ordered) flushList();
      list ??= { t: "list", ordered, start: numbered ? Number(numbered[1]) : 1, items: [] };
      list.items.push(numbered ? numbered[2] : bullet![1]);
      continue;
    }
    if (list && /^\s{2,}\S/.test(line)) { list.items[list.items.length - 1] += "\n" + trimmed; continue; }
    flushList();
    para.push(trimmed);
  }
  flush();
  return out;
}

// ── inline: code, maths, bold, italic, links ──────────────────────────────────

export type Inline =
  | { t: "text"; v: string } | { t: "code"; v: string } | { t: "math"; v: string; display: boolean }
  | { t: "strong"; v: Inline[] } | { t: "em"; v: Inline[] } | { t: "link"; href: string; v: Inline[] } | { t: "br" };

const INLINE = new RegExp([
  "(`+)([^`]|[^`][\\s\\S]*?[^`])\\1(?!`)",                // 1,2 code
  "\\$\\$([^$]+?)\\$\\$",                                // 3 display maths inside a line
  "(?<![\\\\$\\w])\\$(?=\\S)([^$\\n]+?)(?<=\\S)\\$(?!\\d)",    // 4 inline maths (not $5 or $10)
  "\\\\\\(([\\s\\S]+?)\\\\\\)",                          // 5 \( … \)
  "\\*\\*([^*\\n][\\s\\S]*?)\\*\\*",                     // 6 bold
  "\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\)",                  // 7,8 link
  "(https?:\\/\\/[^\\s)<>\\]]+)",                        // 9 bare url
  "(?<![\\w*])\\*(?=\\S)([^*\\n]+?)(?<=\\S)\\*(?![\\w*])", // 10 italic
  "(?<!\\w)_(?=\\S)([^_\\n]+?)(?<=\\S)_(?!\\w)",          // 11 italic, underscores (not snake_case)
  "( {2,}|\\\\)\\n",                                       // 12 hard break
].join("|"), "g");

export function safeUrl(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let last = 0;
  const push = (v: string) => { if (v) out.push({ t: "text", v }); };
  for (const m of text.matchAll(INLINE)) {
    const at = m.index ?? 0;
    push(text.slice(last, at));
    last = at + m[0].length;
    if (m[2] !== undefined) out.push({ t: "code", v: m[2].trim() || m[2] });
    else if (m[3] !== undefined) out.push({ t: "math", v: m[3], display: true });
    else if (m[4] !== undefined) out.push({ t: "math", v: m[4], display: false });
    else if (m[5] !== undefined) out.push({ t: "math", v: m[5], display: false });
    else if (m[6] !== undefined) out.push({ t: "strong", v: parseInline(m[6]) });
    else if (m[7] !== undefined) {
      const href = safeUrl(m[8]);
      if (href) out.push({ t: "link", href, v: parseInline(m[7]) }); else push(m[0]);
    } else if (m[9] !== undefined) {
      const url = m[9].replace(/[.,;:!?'"]+$/, "");
      const href = safeUrl(url);
      if (href) out.push({ t: "link", href, v: [{ t: "text", v: url }] }); else push(url);
      last = at + url.length;
    } else if (m[10] !== undefined) out.push({ t: "em", v: parseInline(m[10]) });
    else if (m[11] !== undefined) out.push({ t: "em", v: parseInline(m[11]) });
    else if (m[12] !== undefined) out.push({ t: "br" });
  }
  push(text.slice(last));
  return out;
}

// ── what the toolbar works with ───────────────────────────────────────────────

export function codeBlocks(text: string): CodeInfo[] {
  return parseBlocks(text).flatMap((b) => b.t === "code" ? [b.info] : []);
}

/** Every equation in the reply, as TeX: displayed ones and inline ones. */
export function equations(text: string): string[] {
  const out: string[] = [];
  const walk = (xs: Inline[]) => xs.forEach((x) => {
    if (x.t === "math") out.push(x.v.trim());
    else if (x.t === "strong" || x.t === "em" || x.t === "link") walk(x.v);
  });
  for (const b of parseBlocks(text)) {
    if (b.t === "math") out.push(b.tex);
    else if (b.t === "p" || b.t === "h" || b.t === "quote") walk(parseInline(b.text));
    else if (b.t === "list") b.items.forEach((it) => walk(parseInline(it)));
    else if (b.t === "table") [b.head, ...b.rows].flat().forEach((c) => walk(parseInline(c)));
  }
  return out;
}

/** The words of a reply for reading aloud: no code listings, no TeX, no Markdown marks. */
export function speakable(text: string): string {
  const parts: string[] = [];
  const plain = (s: string) => s.replace(/`([^`]+)`/g, "$1").replace(/\$\$?[^$]+\$\$?|\\\(.+?\\\)/g, " (equation) ")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1").replace(/[*_#>|]/g, "").replace(/\s+/g, " ").trim();
  for (const b of parseBlocks(text)) {
    if (b.t === "code") parts.push(`(${b.info.language === "text" ? "a" : b.info.language} code block)`);
    else if (b.t === "math") parts.push("(equation)");
    else if (b.t === "list") parts.push(...b.items.map(plain));
    else if (b.t === "table") parts.push("(table)");
    else if (b.t !== "hr") parts.push(plain(b.text));
  }
  return parts.filter(Boolean).join(". ").replace(/\.\s*\./g, ".");
}

/** brain.py's metadata, if it is well formed; otherwise null and the page decides for itself. */
export function structuredOf(x: unknown): Structured | null {
  if (!x || typeof x !== "object") return null;
  const s = x as Record<string, unknown>;
  if (!KINDS.includes(s.kind as Kind) || typeof s.summary !== "string" || !Array.isArray(s.code)) return null;
  if (!s.code.every((c) => c && typeof c === "object" && typeof (c as any).language === "string")) return null;
  if (typeof s.math !== "number" || typeof s.length !== "number" || typeof s.lines !== "number") return null;
  return s as unknown as Structured;
}

/** The reply's kind: the service's, when it sent a valid one, else read from the text. */
export function classify(text: string, structured?: unknown, error?: string | null): Kind {
  const s = structuredOf(structured);
  if (s) return s.kind;
  if (error) return "error";
  const blocks = parseBlocks(text);
  if (blocks.some((b) => b.t === "code")) return "code";
  const eq = equations(text).length;
  if (blocks.some((b) => b.t === "math") || eq >= 2) return "math";
  if (text.length > 900 || text.split("\n").length > 16) return "long";
  const items = blocks.reduce((n, b) => n + (b.t === "list" ? b.items.length : b.t === "table" ? b.rows.length : 0), 0);
  return items >= 3 ? "list" : "conversation";
}

/** Long enough to start collapsed: by length and structure, never a short reply. */
export function isLong(text: string) {
  const lines = text.split("\n").length;
  const codeLines = codeBlocks(text).reduce((n, c) => n + c.code.split("\n").length, 0);
  return text.length > 1400 || lines > 28 || codeLines > 40;
}

const EXT: Record<string, string> = {
  python: "py", py: "py", javascript: "js", js: "js", typescript: "ts", ts: "ts", jsx: "jsx", tsx: "tsx", html: "html",
  xml: "xml", css: "css", json: "json", java: "java", c: "c", cpp: "cpp", "c++": "cpp", csharp: "cs", cs: "cs", sql: "sql",
  bash: "sh", sh: "sh", shell: "sh", zsh: "sh", rust: "rs", go: "go", yaml: "yml", yml: "yml", markdown: "md", md: "md",
  kotlin: "kt", swift: "swift", ruby: "rb", php: "php", toml: "toml", ini: "ini", dockerfile: "Dockerfile",
};

/** A safe file name for a downloaded code block: its own, or snippet.<ext>. */
export function codeFileName(info: Pick<CodeInfo, "language" | "filename">, n = 1) {
  const own = info.filename?.split("/").pop()?.replace(/[^\w.-]/g, "_");
  if (own && !/^\.+$/.test(own)) return own.slice(0, 80);
  const ext = EXT[info.language] ?? "txt";
  return ext === "Dockerfile" ? "Dockerfile" : `snippet${n > 1 ? `-${n}` : ""}.${ext}`;
}
