#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import re
from dataclasses import dataclass, field
from pathlib import Path


SHELL_PREFIXES = (
    "$ ",
    "% ",
)

ASCII_CODE_PREFIXES = (
    "fd =",
    "pid =",
    "write(",
    "read(",
    "open(",
    "close(",
    "fork(",
    "exec(",
    "wait(",
    "exit(",
    "cat ",
    "echo ",
    "grep ",
    "ls ",
    "make ",
)

INLINE_CODE_PATTERNS = [
    re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*\(\))"),
    re.compile(r"\b([A-Za-z0-9_.-]+\.(?:c|h|txt))\b"),
    re.compile(r"\b(fd \d+|pid|xv6|UNIX|RISC-V|qemu|Piazza)\b"),
]


@dataclass
class Block:
    kind: str
    value: str | None = None
    items: list[tuple[int, str]] = field(default_factory=list)


@dataclass
class Slide:
    kind: str
    title: str
    section: str | None = None
    blocks: list[Block] = field(default_factory=list)
    resource: str | None = None


def strip_ai_preface(lines: list[str]) -> list[str]:
    if lines and lines[0].lstrip("\ufeff").startswith("[AI 汉化说明]"):
        idx = 0
        while idx < len(lines) and lines[idx].strip():
            idx += 1
        while idx < len(lines) and not lines[idx].strip():
            idx += 1
        return lines[idx:]
    return lines


def load_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    return strip_ai_preface(text.splitlines())


def is_section_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if line[:1].isspace():
        return False
    if stripped.startswith(("* ", "- ", "[", "http://", "https://")):
        return False
    return True


def detect_resource(title: str, asset_dir: Path) -> str | None:
    match = re.search(r"\b([A-Za-z0-9_.-]+\.(?:c|h|txt))\b", title)
    if not match:
        return None
    candidate = asset_dir / match.group(1)
    if candidate.exists():
        return match.group(1)
    return None


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def looks_like_code(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.startswith(SHELL_PREFIXES):
        return True
    if not contains_cjk(stripped) and stripped.startswith(ASCII_CODE_PREFIXES):
        return True
    if not contains_cjk(stripped) and (";" in stripped or re.search(r"[A-Za-z_][A-Za-z0-9_]*\(", stripped)):
        return True
    return False


def make_inline_html(text: str, asset_dir: Path) -> str:
    escaped = html.escape(text)

    def replace_file(match: re.Match[str]) -> str:
        token = match.group(1)
        if (asset_dir / token).exists():
            return f'<a href="./{html.escape(token)}"><code>{html.escape(token)}</code></a>'
        return f"<code>{html.escape(token)}</code>"

    escaped = INLINE_CODE_PATTERNS[1].sub(replace_file, escaped)
    escaped = INLINE_CODE_PATTERNS[0].sub(r"<code>\1</code>", escaped)
    escaped = INLINE_CODE_PATTERNS[2].sub(r"<code>\1</code>", escaped)

    url_pattern = re.compile(r"(https?://[^\s<]+)")
    escaped = url_pattern.sub(r'<a href="\1">\1</a>', escaped)
    return escaped


def finalize_blocks(raw_lines: list[str], asset_dir: Path) -> list[Block]:
    blocks: list[Block] = []
    list_items: list[tuple[int, str]] = []
    code_lines: list[str] = []

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            blocks.append(Block(kind="list", items=list_items))
            list_items = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            blocks.append(Block(kind="code", value="\n".join(code_lines)))
            code_lines = []

    for raw in raw_lines:
        stripped = raw.strip()
        if not stripped:
            flush_list()
            flush_code()
            continue

        if looks_like_code(stripped):
            flush_list()
            code_lines.append(stripped)
            continue

        flush_code()

        indent = len(raw) - len(raw.lstrip(" "))

        if stripped.startswith("[") and stripped.endswith("]"):
            flush_list()
            blocks.append(Block(kind="diagram", value=stripped[1:-1]))
            continue

        if stripped.startswith(("http://", "https://")):
            if indent > 0 or list_items:
                link_html = f'<a href="{html.escape(stripped)}">{html.escape(stripped)}</a>'
                list_items.append((max(1, indent // 2), link_html))
            else:
                flush_list()
                blocks.append(Block(kind="link", value=stripped))
            continue

        compact = raw.lstrip(" ")
        if compact.startswith(("* ", "- ")):
            level = max(1, indent // 2)
            text = compact[2:].strip()
            list_items.append((level, make_inline_html(text, asset_dir)))
        elif indent > 0:
            level = max(1, indent // 2)
            list_items.append((level, make_inline_html(stripped, asset_dir)))
        else:
            flush_list()
            blocks.append(Block(kind="paragraph", value=make_inline_html(stripped, asset_dir)))

    flush_list()
    flush_code()
    return blocks


def parse_outline(path: Path, asset_dir: Path) -> tuple[str, list[Slide]]:
    lines = load_lines(path)
    if not lines:
        raise ValueError(f"{path} is empty")

    title = ""
    idx = 0
    while idx < len(lines):
        if lines[idx].strip():
            title = lines[idx].strip()
            idx += 1
            break
        idx += 1

    slides: list[Slide] = []
    current_section: str | None = None
    current_title: str | None = None
    current_lines: list[str] = []

    def flush_slide() -> None:
        nonlocal current_title, current_lines
        if current_title is None:
            return
        slides.append(
            Slide(
                kind="content",
                title=current_title,
                section=current_section,
                blocks=finalize_blocks(current_lines, asset_dir),
                resource=detect_resource(current_title, asset_dir),
            )
        )
        current_title = None
        current_lines = []

    while idx < len(lines):
        line = lines[idx]
        stripped = line.strip()
        if not stripped:
            if current_title is not None:
                current_lines.append(line)
            idx += 1
            continue

        if is_section_heading(line):
            flush_slide()
            current_section = stripped
            slides.append(Slide(kind="divider", title=stripped))
            idx += 1
            continue

        if line.startswith("* "):
            flush_slide()
            current_title = line[2:].strip()
            idx += 1
            continue

        if current_title is not None:
            current_lines.append(line)
        idx += 1

    flush_slide()
    return title, slides


def render_list(items: list[tuple[int, str]]) -> str:
    base_level = items[0][0]
    normalized = [(level - base_level + 1, content) for level, content in items]
    parts: list[str] = []
    current_level = 0
    first = True
    for level, content in normalized:
        while current_level < level:
            parts.append("<ul>")
            current_level += 1
            first = True
        while current_level > level:
            parts.append("</li></ul>")
            current_level -= 1
            first = False
        if not first:
            parts.append("</li>")
        parts.append(f"<li>{content}")
        first = False
    while current_level > 0:
        parts.append("</li></ul>")
        current_level -= 1
    return "".join(parts)


def render_blocks(blocks: list[Block]) -> str:
    rendered: list[str] = []
    for block in blocks:
        if block.kind == "list":
            rendered.append(render_list(block.items))
        elif block.kind == "code":
            rendered.append(f"<pre><code>{html.escape(block.value or '')}</code></pre>")
        elif block.kind == "diagram":
            rendered.append(
                '<div class="diagram-placeholder">'
                f"<span>示意图占位</span><strong>{html.escape(block.value or '')}</strong>"
                "</div>"
            )
        elif block.kind == "link":
            url = html.escape(block.value or "")
            rendered.append(f'<p class="resource-link"><a href="{url}">{url}</a></p>')
        elif block.kind == "paragraph":
            rendered.append(f'<p class="slide-text">{block.value or ""}</p>')
    return "\n".join(rendered)


def render_slide(slide: Slide) -> str:
    if slide.kind == "divider":
        return (
            '<section class="section-divider">'
            f'<p class="section-kicker">Lecture Flow</p><h2>{html.escape(slide.title)}</h2>'
            "</section>"
        )

    badge = f'<p class="section-badge">{html.escape(slide.section)}</p>' if slide.section else ""
    resource = ""
    if slide.resource:
        resource = (
            '<p class="resource-chip">'
            f'相关示例：<a href="./{html.escape(slide.resource)}"><code>{html.escape(slide.resource)}</code></a>'
            "</p>"
        )
    return (
        "<section>"
        f"{badge}"
        f"<h3>{html.escape(slide.title)}</h3>"
        f"{resource}"
        f'{render_blocks(slide.blocks)}'
        "</section>"
    )


def render_html(deck_title: str, slides: list[Slide], source_path: Path, output_path: Path) -> str:
    slides_html = "\n".join(render_slide(slide) for slide in slides)
    deck_subtitle = html.escape(f"{source_path.parents[1].name} · {source_path.stem}")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{html.escape(deck_title)} · Slides</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.css">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/theme/white.css">
  <style>
    :root {{
      --bg: #f6f1e8;
      --paper: #fffaf2;
      --ink: #231815;
      --muted: #705b4a;
      --accent: #9f2f1f;
      --line: rgba(35, 24, 21, 0.12);
      --code-bg: #1f2430;
      --code-ink: #f4f1ea;
    }}
    body {{
      background:
        radial-gradient(circle at top left, rgba(159, 47, 31, 0.08), transparent 28%),
        linear-gradient(180deg, #fbf7f0 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    .reveal {{
      font-family: "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif;
      color: var(--ink);
    }}
    .reveal .slides {{
      text-align: left;
    }}
    .reveal section {{
      background: linear-gradient(180deg, rgba(255,250,242,0.98), rgba(250,244,234,0.98));
      border: 1px solid var(--line);
      border-radius: 28px;
      box-shadow: 0 24px 80px rgba(52, 36, 25, 0.12);
      padding: 48px 56px;
    }}
    .reveal .title-slide {{
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 620px;
      background:
        linear-gradient(135deg, rgba(159, 47, 31, 0.10), rgba(255,255,255,0) 35%),
        linear-gradient(180deg, #fffdf8 0%, #f7f1e7 100%);
    }}
    .reveal .title-slide h1,
    .reveal h2,
    .reveal h3 {{
      font-family: "Noto Serif SC", "Source Han Serif SC", "Songti SC", serif;
      letter-spacing: 0.01em;
      text-transform: none;
      color: var(--ink);
    }}
    .reveal .title-slide h1 {{
      font-size: 2.3em;
      line-height: 1.15;
      margin: 0 0 0.3em;
      max-width: 11em;
    }}
    .reveal .deck-meta,
    .reveal .section-badge,
    .reveal .section-kicker {{
      color: var(--accent);
      font-size: 0.5em;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      font-weight: 700;
      margin: 0 0 1em;
    }}
    .reveal .title-links,
    .reveal .resource-chip {{
      color: var(--muted);
      font-size: 0.42em;
      margin-top: 1.2em;
    }}
    .reveal .title-links a,
    .reveal .resource-chip a,
    .reveal .resource-link a {{
      color: var(--accent);
    }}
    .reveal h2 {{
      font-size: 1.8em;
      margin: 0;
    }}
    .reveal h3 {{
      font-size: 1.25em;
      line-height: 1.2;
      margin: 0 0 0.6em;
    }}
    .reveal .section-divider {{
      display: flex;
      flex-direction: column;
      justify-content: center;
      align-items: flex-start;
      min-height: 620px;
      background:
        linear-gradient(160deg, rgba(159, 47, 31, 0.16), rgba(255,255,255,0) 55%),
        linear-gradient(180deg, #fff8ef 0%, #f6eee2 100%);
    }}
    .reveal ul {{
      display: block;
      margin: 0.3em 0 0;
      padding-left: 1.1em;
      color: var(--ink);
      font-size: 0.62em;
      line-height: 1.55;
    }}
    .reveal ul ul {{
      margin-top: 0.25em;
      font-size: 0.95em;
    }}
    .reveal li {{
      margin: 0.18em 0;
    }}
    .reveal .slide-text,
    .reveal .resource-link {{
      font-size: 0.62em;
      line-height: 1.55;
      color: var(--ink);
      margin: 0.55em 0;
    }}
    .reveal pre {{
      width: 100%;
      margin: 0.8em 0 0;
      box-shadow: none;
      font-size: 0.42em;
    }}
    .reveal pre code {{
      max-height: none;
      padding: 1.1em 1.2em;
      border-radius: 18px;
      background: var(--code-bg);
      color: var(--code-ink);
      line-height: 1.5;
    }}
    .diagram-placeholder {{
      display: grid;
      gap: 0.5em;
      margin-top: 1em;
      padding: 1.1em 1.2em;
      border: 1px dashed rgba(159, 47, 31, 0.38);
      border-radius: 18px;
      background: rgba(159, 47, 31, 0.05);
      color: var(--muted);
      font-size: 0.52em;
    }}
    .diagram-placeholder span {{
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 0.82em;
      color: var(--accent);
    }}
    .reveal code {{
      font-family: "SFMono-Regular", "Menlo", "Consolas", monospace;
      background: rgba(35, 24, 21, 0.07);
      border-radius: 0.35em;
      padding: 0.1em 0.28em;
    }}
    .reveal .progress {{
      color: var(--accent);
    }}
    @media (max-width: 900px) {{
      .reveal section {{
        padding: 30px 24px;
        border-radius: 18px;
      }}
      .reveal .title-slide,
      .reveal .section-divider {{
        min-height: auto;
      }}
      .reveal .title-slide h1 {{
        font-size: 1.55em;
      }}
      .reveal h2 {{
        font-size: 1.35em;
      }}
      .reveal h3 {{
        font-size: 1em;
      }}
      .reveal ul,
      .reveal .slide-text,
      .reveal .resource-link {{
        font-size: 0.76em;
      }}
      .reveal pre {{
        font-size: 0.56em;
      }}
    }}
  </style>
</head>
<body>
  <div class="reveal">
    <div class="slides">
      <section class="title-slide">
        <div>
          <p class="deck-meta">MIT {deck_subtitle} · Reveal.js Reconstruction</p>
          <h1>{html.escape(deck_title)}</h1>
          <p class="slide-text">根据讲稿式文本提炼为可翻页的本地幻灯片，保留原始讲义结构、示例程序入口与示意图占位。</p>
        </div>
        <div class="title-links">
          <p>原始讲义：<a href="../{html.escape(source_path.name)}"><code>{html.escape(source_path.name)}</code></a></p>
          <p>配套示例：<a href="./index.html"><code>l-overview/</code></a></p>
          <p>键盘操作：左右切换，空格继续，按 <code>Esc</code> 查看全局概览。</p>
        </div>
      </section>
      {slides_html}
    </div>
  </div>
  <script src="https://cdn.jsdelivr.net/npm/reveal.js@5/dist/reveal.js"></script>
  <script>
    Reveal.initialize({{
      hash: true,
      slideNumber: "c/t",
      progress: true,
      controls: true,
      center: false,
      width: 1360,
      height: 768,
      margin: 0.06,
      transition: "none",
      backgroundTransition: "none",
      navigationMode: "linear"
    }});
  </script>
</body>
</html>
"""


def build_deck(input_path: Path, output_path: Path) -> None:
    asset_dir = output_path.parent
    deck_title, slides = parse_outline(input_path, asset_dir)
    html_text = render_html(deck_title, slides, input_path, output_path)
    output_path.write_text(html_text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a reveal.js deck from an outline-style lecture txt.")
    parser.add_argument("input", type=Path, help="Source TXT outline file")
    parser.add_argument("output", type=Path, help="Output HTML file")
    args = parser.parse_args()
    build_deck(args.input, args.output)


if __name__ == "__main__":
    main()
