// Syntax highlighting for code cards: highlight.js core with the languages a chat answer uses.
// highlight.js escapes the source itself, so its output is safe to insert; nothing is executed.
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import c from "highlight.js/lib/languages/c";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import go from "highlight.js/lib/languages/go";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import kotlin from "highlight.js/lib/languages/kotlin";
import markdown from "highlight.js/lib/languages/markdown";
import php from "highlight.js/lib/languages/php";
import plaintext from "highlight.js/lib/languages/plaintext";
import python from "highlight.js/lib/languages/python";
import ruby from "highlight.js/lib/languages/ruby";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import swift from "highlight.js/lib/languages/swift";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import ini from "highlight.js/lib/languages/ini";

const LANGS = { bash, c, cpp, csharp, css, go, java, javascript, json, kotlin, markdown, php, plaintext, python, ruby, rust, sql, swift, typescript, xml, yaml, ini };
for (const [name, lang] of Object.entries(LANGS)) hljs.registerLanguage(name, lang);

const ALIAS: Record<string, string> = {
  py: "python", python: "python", python3: "python",
  js: "javascript", javascript: "javascript", jsx: "javascript", mjs: "javascript", cjs: "javascript",
  ts: "typescript", typescript: "typescript", tsx: "typescript",
  html: "xml", xml: "xml", svg: "xml",
  css: "css", scss: "css", less: "css",
  sh: "bash", bash: "bash", shell: "bash", zsh: "bash", console: "bash", terminal: "bash",
  c: "c", h: "c",
  "c++": "cpp", cpp: "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp",
  java: "java",
  json: "json", jsonc: "json",
  sql: "sql",
  cs: "csharp", "c#": "csharp", csharp: "csharp",
  rs: "rust", rust: "rust",
  go: "go", golang: "go",
  yml: "yaml", yaml: "yaml",
  md: "markdown", markdown: "markdown",
  text: "plaintext", txt: "plaintext", plaintext: "plaintext",
  toml: "ini", ini: "ini",
  kt: "kotlin", kotlin: "kotlin",
  rb: "ruby", ruby: "ruby",
  php: "php",
  swift: "swift",
};

/** Display name for the language badge. */
export function languageLabel(lang: string) {
  const l = (lang || "").toLowerCase().trim();
  const names: Record<string, string> = {
    jsx: "JSX", tsx: "TSX", js: "JavaScript", javascript: "JavaScript",
    ts: "TypeScript", typescript: "TypeScript",
    py: "Python", python: "Python", python3: "Python",
    html: "HTML", xml: "XML", css: "CSS", scss: "SCSS",
    json: "JSON", jsonc: "JSON", sql: "SQL",
    cpp: "C++", "c++": "C++", c: "C", java: "Java",
    bash: "Bash", sh: "Shell", shell: "Shell", zsh: "Zsh", console: "Terminal", terminal: "Terminal",
    text: "Text", plaintext: "Text",
    csharp: "C#", cs: "C#", "c#": "C#",
    rs: "Rust", rust: "Rust", go: "Go", golang: "Go",
    yml: "YAML", yaml: "YAML", md: "Markdown", markdown: "Markdown",
    kt: "Kotlin", kotlin: "Kotlin", rb: "Ruby", ruby: "Ruby", php: "PHP", swift: "Swift",
    toml: "TOML", ini: "INI"
  };
  return names[l] ?? (l ? l[0].toUpperCase() + l.slice(1) : "Code");
}

/** Highlighted HTML for `code` (escaped by highlight.js), or null when the language is unknown. */
export function highlight(code: string, lang: string): string | null {
  const name = ALIAS[lang.toLowerCase()] ?? lang.toLowerCase();
  if (!hljs.getLanguage(name)) return null;
  try {
    return hljs.highlight(code, { language: name, ignoreIllegals: true }).value;
  } catch {
    return null;
  }
}

const CLIP_COLORS: [RegExp, string][] = [
  [/hljs-(keyword|selector-tag|built_in)/, "#7c3aed"], [/hljs-(string|regexp|template-string)/, "#15803d"],
  [/hljs-(number|literal|symbol)/, "#c2410c"], [/hljs-(comment|quote)/, "#6b7280"], [/hljs-(title|section)/, "#1d4ed8"],
  [/hljs-(type|class)/, "#a16207"], [/hljs-(attr|attribute|property|variable|params)/, "#9a3412"], [/hljs-(tag|name)/, "#b91c1c"],
  [/hljs-meta/, "#0e7490"],
];

/** Code as HTML with inline colours, for "copy with formatting" (pasted documents have no stylesheet). */
export function clipboardHtml(blocks: { code: string; language: string }[]) {
  const esc = (s: string) => s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]!));
  return blocks.map(({ code, language }) => {
    const html = (highlight(code, language) ?? esc(code)).replace(/class="([^"]+)"/g, (_, cls: string) => {
      const hit = CLIP_COLORS.find(([re]) => re.test(cls));
      return hit ? `style="color:${hit[1]}"` : "";
    });
    return `<pre style="font-family:Consolas,'Courier New',monospace;font-size:13px;white-space:pre;background:#f6f8fa;padding:10px;border-radius:6px"><code>${html}</code></pre>`;
  }).join("\n");
}
