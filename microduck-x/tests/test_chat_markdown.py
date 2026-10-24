"""The chat's answer renderer (frontend/src/components/chat/, lib/chatContent.ts), in the page's own
code, rendered by React to HTML: model text never becomes markup, only http(s) links are links,
maths goes through KaTeX, code through highlight.js with its exact text kept, half-streamed and
malformed replies do not throw. Skipped unless node and the dashboard's node_modules (with an
esbuild for this OS) are installed."""
import json
import subprocess

import pytest

from test_duck_animation import FRONTEND, SRC, _runnable

pytestmark = pytest.mark.skipif(not _runnable(), reason="needs node and the frontend's node_modules")

CODE = 'def f(n):\n    if n < 2:\n\treturn 1  # <b>not bold</b>\n    return n * f(n - 1)\n'

CASES = {
    "script": "<script>alert(1)</script> and <img src=x onerror=alert(1)>",
    "js_link": "[click](javascript:alert(1))",
    "web_link": "See [the docs](https://console.groq.com/docs) or https://example.org/a.",
    "rich": "# Recursion\n\nA function **calls itself**, like `f(n-1)`:\n\n1. base case\n2. step\n\n- *one*\n- two",
    "code": f"Here:\n\n```python title=\"fact.py\"\n{CODE}```\n\nDone.",
    "snake": "use snake_case_names here",
    "math": "**Step 1:** factor $x^2-5x+6$.\n\n$$\n(x-2)(x-3)=0\n$$\n\nSo $x=2$ or $x=3$. It costs $5 or $10.",
    "evil_tex": "$$\\href{javascript:alert(1)}{x} \\url{javascript:alert(2)}$$",
    "table": "| n | n² |\n|---|--:|\n| 2 | $4$ |\n| 3 | **9** |",
    "partial_fence": "Sure:\n\n```js\nconst a = 1;\nfunction f(",
    "partial_math": "The area is $$\\int_0^1 x",
    "partial_table": "| a | b |\n|---",
    "bad_tex": "$$\\frac{1}{$$",
    "followups": "Answer.\n>> follow-ups: why? | how?",
    # as openai/gpt-oss-120b actually wrote it on Groq (live, 2026-10-24)
    "groq_math": ("**Step 3: Factor the quadratic using those numbers**  \n\\[\nx^2 - 5x + 6 = (x-3)(x-2).\n\\]\n\n"
                  "**Answer:** The solutions are \\(x = 2\\) and \\(x = 3\\)."),
    "weird": "```\n" + "x" * 10 + "\n```\n```" + "\n$$" * 5 + "\n| | |\n|-|-|\n****\n__\n[]()",
}

ENTRY = """
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import MarkdownRenderer from "{src}/components/chat/MarkdownRenderer";
import { classify, codeBlocks, equations, speakable, structuredOf, cleanAnswer, isLong, codeFileName } from "{src}/lib/chatContent";
import { clipboardHtml } from "{src}/lib/highlight";
const cases = JSON.parse(process.argv[2]);
const html = {}, errors = {};
for (const k of Object.keys(cases)) {
  try { html[k] = renderToStaticMarkup(createElement(MarkdownRenderer, { text: cases[k] })); }
  catch (e) { errors[k] = String(e); }
}
const out = {
  html, errors,
  code: codeBlocks(cases.code),
  eqs: equations(cases.math),
  kinds: Object.fromEntries(Object.keys(cases).map((k) => [k, classify(cases[k])])),
  trusted: classify("plain words", { kind: "code", summary: "", code: [], math: 0, length: 1, lines: 1 }),
  malformed: [structuredOf({ kind: "rm -rf" }), structuredOf("x"), structuredOf({ kind: "math", summary: 1 })],
  fallbackKind: classify("Solve $a$ and $b$.", { kind: "nope" }),
  speak: speakable(cases.code + "\\n\\n" + cases.math),
  clean: cleanAnswer(cases.followups),
  long: [isLong("short"), isLong("word ".repeat(400)), isLong("```py\\n" + "x\\n".repeat(60) + "```")],
  names: [codeFileName({ language: "python", filename: "../../etc/passwd" }), codeFileName({ language: "tsx", filename: null }, 2),
          codeFileName({ language: "weird", filename: null })],
  clip: clipboardHtml([{ code: "x = '<b>'", language: "python" }]),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("md")
    entry = tmp / "entry.tsx"
    entry.write_text(ENTRY.replace("{src}", SRC.as_posix()), encoding="utf-8")
    bundle = tmp / "md.cjs"
    build = ("require('esbuild').buildSync({entryPoints: [process.argv[1]], bundle: true, format: 'cjs', platform: 'node',"
             " jsx: 'automatic', loader: {'.css': 'empty'}, outfile: process.argv[2], nodePaths: [process.argv[3]], logLevel: 'error'})")
    subprocess.run(["node", "-e", build, str(entry), str(bundle), str(FRONTEND / "node_modules")], cwd=FRONTEND, check=True)
    run = subprocess.run(["node", str(bundle), json.dumps(CASES)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout.strip().splitlines()[-1])


def test_nothing_throws_even_half_written_or_malformed(out):
    assert out["errors"] == {}


def test_html_in_model_text_stays_text(out):
    h = out["html"]
    assert "<script" not in h["script"] and "<img" not in h["script"] and "&lt;script&gt;" in h["script"]
    assert "<b>" not in h["code"] and "&lt;b&gt;" in h["code"]


def test_only_web_links_are_links(out):
    h = out["html"]
    assert "href" not in h["js_link"] and "javascript:" in h["js_link"]
    assert 'href="https://console.groq.com/docs"' in h["web_link"] and 'href="https://example.org/a"' in h["web_link"]
    assert 'rel="noopener noreferrer nofollow"' in h["web_link"]
    assert "javascript:" not in h["evil_tex"].split("annotation")[0].replace("\\href", "")  # KaTeX trust off: no link
    assert "<a " not in h["evil_tex"]


def test_markdown_structure(out):
    rich = out["html"]["rich"]
    assert 'class="md-h md-h1"' in rich and "<strong>calls itself</strong>" in rich
    assert "<code>f(n-1)</code>" in rich and "<ol><li>base case</li><li>step</li></ol>" in rich
    assert "<ul><li><em>one</em></li><li>two</li></ul>" in rich
    assert "<em>" not in out["html"]["snake"]


def test_maths_goes_through_katex_and_currency_does_not(out):
    m = out["html"]["math"]
    assert m.count('class="katex"') >= 3 and 'class="katex-display"' in m
    assert "$5 or $10" in m  # dollars stay dollars
    assert out["eqs"] == ["x^2-5x+6", "(x-2)(x-3)=0", "x=2", "x=3"]
    assert 'class="katex"' in out["html"]["table"] and "<table>" in out["html"]["table"]
    assert 'text-align:right' in out["html"]["table"]
    g = out["html"]["groq_math"]
    assert g.count('class="katex-display"') == 1 and g.count('class="katex"') == 3 and "\\[" not in g and "\\(" not in g
    assert out["kinds"]["groq_math"] == "math"


def test_code_cards_keep_the_code_exactly(out):
    h = out["html"]["code"]
    assert 'class="code-card' in h and ">Python<" in h and "fact.py" in h and 'class="hljs-keyword"' in h
    assert 'class="cb-gutter"' in h and "<span>4</span>" in h
    (block,) = out["code"]
    assert block["code"] == CODE.rstrip("\n") and block["filename"] == "fact.py" and block["closed"] is True
    assert "\t" in block["code"] and "    return" in block["code"]   # indentation, tabs too
    assert "writing…" in out["html"]["partial_fence"]                 # an open fence while streaming


def test_partial_and_malformed_fall_back_to_text(out):
    h = out["html"]
    assert "\\int_0^1 x" in h["partial_math"] and "katex" not in h["partial_math"]
    assert "<th>a</th>" in h["partial_table"] and "<tbody></tbody>" in h["partial_table"]  # rows arrive later
    assert "katex" in h["bad_tex"]  # KaTeX shows the error inline instead of throwing


def test_kinds_and_metadata_validation(out):
    k = out["kinds"]
    assert (k["code"], k["math"], k["rich"], k["web_link"]) == ("code", "math", "list", "conversation")
    assert out["trusted"] == "code"                            # a valid structure from the service is used
    assert out["malformed"] == [None, None, None]              # anything else is ignored
    assert out["fallbackKind"] == "math"                       # … and the page decides from the text


def test_toolbar_helpers(out):
    assert "(python code block)" in out["speak"] and "def f" not in out["speak"] and "x^2" not in out["speak"]
    assert out["clean"] == "Answer."
    assert out["long"] == [False, True, True]
    assert out["names"] == ["passwd", "snippet-2.tsx", "snippet.txt"]
    assert 'style="color:' in out["clip"] and "&lt;b&gt;" in out["clip"] and "class=" not in out["clip"]
