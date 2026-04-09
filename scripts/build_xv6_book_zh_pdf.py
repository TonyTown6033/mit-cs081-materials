#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = ROOT / "official-pages/6.S081-2020/xv6/book-riscv-rev1.pdf"
TMP_DIR = ROOT / "tmp/pdfs"
FIGURE_DIR = TMP_DIR / "figures"
FINAL_DIR = ROOT / "readings"
RAW_TEXT_PATH = TMP_DIR / "xv6-book-2020-en.txt"
MARKDOWN_PATH = FINAL_DIR / "xv6-book-2020-zh.md"
CACHE_PATH = TMP_DIR / "xv6-book-2020-zh-cache.json"
OUTPUT_PDF = FINAL_DIR / "xv6-book-2020-zh-cn.pdf"
FIGURE_MARKER_RE = re.compile(r"^\[\[FIGURE (\d+\.\d+)\]\]$")
FIGURE_GAP_MAX = 38.0

CODE_TRIGGERS = (
    "int ",
    "char ",
    "void ",
    "uint",
    "struct ",
    "static ",
    "return ",
    "#include ",
    "for(",
    "for (",
    "if(",
    "if (",
    "while(",
    "while (",
    "switch(",
    "printf(",
    "fprintf(",
    "exec(",
    "fork(",
    "wait(",
    "exit(",
    "panic(",
    "$ ",
    "% ",
)


@dataclass
class Unit:
    key: str
    markdown: str


def run(cmd: list[str], *, input_text: str | None = None, timeout: int = 180) -> str:
    completed = subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(cmd)}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout


def extract_pdf_text(pdf_path: Path) -> str:
    return run(["pdftotext", "-layout", str(pdf_path), "-"], timeout=120)


def clean_page(page: str) -> str:
    lines = [line.rstrip() for line in page.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"\d+", stripped):
            continue
        if re.fullmatch(r"Chapter \d+", stripped):
            cleaned.append(stripped)
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def clean_text(raw_text: str) -> str:
    pages = [clean_page(page) for page in raw_text.split("\f")]
    pages = [page for page in pages if page]
    text = "\n\n".join(pages)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def headingize(title: str, level: int) -> str:
    return "#" * level + " " + title.strip()


def normalize_paragraph(lines: list[str]) -> str:
    if not lines:
        return ""
    text = lines[0].strip()
    for line in lines[1:]:
        current = line.strip()
        if not current:
            continue
        if text.endswith("-") and current[:1].islower():
            text = text[:-1] + current
        else:
            text += " " + current
    return text


def is_section_heading(line: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\s+.+", line.strip()))


def parse_figure_number(line: str) -> str | None:
    match = re.match(r"^Figure\s*(\d+\.\d+)\s*:", line.strip())
    if match:
        return match.group(1)
    return None


def normalize_figure_caption(line: str) -> str:
    stripped = line.strip()
    match = re.match(r"^Figure\s*(\d+\.\d+)\s*:\s*(.*)$", stripped)
    if not match:
        return stripped
    number, rest = match.groups()
    if rest:
        return f"Figure {number}: {rest.strip()}"
    return f"Figure {number}:"


def has_code_syntax(plain: str) -> bool:
    if plain.startswith(CODE_TRIGGERS):
        return True
    if plain.startswith("System call") and "Description" in plain:
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\s\*]*\([^)]*\)\s*(\{|;)", plain):
        return True
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_\s\*]*\([^)]*\)\s{2,}.+", plain):
        return True
    if re.search(r"(->|==|!=|<=|>=)", plain):
        return True
    if re.search(r"[{}]\s*$", plain):
        return True
    if re.fullmatch(r".*\b[A-Za-z_][A-Za-z0-9_]*\([^)]*\);\s*", plain):
        return True
    if re.search(r"\b(return|break|continue)\b.*;\s*$", plain):
        return True
    if re.search(r"\b(int|char|void|uint|struct|static)\b.*;\s*$", plain):
        return True
    if re.search(r"=\s*[^;]+;\s*$", plain):
        return True
    return False


def is_code_line(line: str) -> bool:
    stripped = line.rstrip()
    if not stripped:
        return False
    plain = stripped.lstrip()
    if not re.search(r"[A-Za-z]", plain):
        return False
    if has_code_syntax(plain):
        return True
    if re.match(r"^\s{4,}\S", line):
        if re.match(r"^(if|for|while|switch|case|return)\b", plain):
            return True
    return False


def build_unit_markdown(heading: str, body: str, *, level: int = 1) -> str:
    parts = [headingize(heading, level)]
    formatted = format_body_as_markdown(body) if body.strip() else ""
    if formatted:
        parts.extend(["", formatted])
    return "\n".join(parts).strip()


def format_body_as_markdown(body: str) -> str:
    lines = body.splitlines()
    out: list[str] = []
    para: list[str] = []
    code: list[str] = []

    def flush_para() -> None:
        nonlocal para
        paragraph = normalize_paragraph(para)
        if paragraph:
            out.append(paragraph)
            out.append("")
        para = []

    def flush_code() -> None:
        nonlocal code
        if code:
            out.append("```")
            out.extend(code)
            out.append("```")
            out.append("")
        code = []

    def is_figure_label_block(block: list[str]) -> bool:
        if not block:
            return False
        if block[0].strip() == "```" or block[-1].strip() == "```":
            return False
        text = " ".join(line.strip() for line in block if line.strip())
        if not text:
            return False
        punctuation_text = re.sub(r"\.+", "", text)
        if re.search(r"[!?;:]", punctuation_text):
            return False
        if "." in punctuation_text:
            return False
        words = text.split()
        return 1 <= len(words) <= 40 and len(text) <= 260

    def drop_trailing_figure_block() -> None:
        removed = False
        while True:
            while out and not out[-1].strip():
                out.pop()
            if not out:
                break
            end = len(out)
            start = end - 1
            while start > 0 and out[start - 1].strip():
                start -= 1
            block = out[start:end]
            if not is_figure_label_block(block):
                break
            del out[start:end]
            removed = True
        if removed:
            while out and not out[-1].strip():
                out.pop()

    index = 0
    while index < len(lines):
        raw = lines[index]
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            flush_para()
            flush_code()
            index += 1
            continue
        figure_number = parse_figure_number(stripped)
        if figure_number:
            para = []
            code = []
            drop_trailing_figure_block()
            caption_lines = [normalize_figure_caption(stripped)]
            index += 1
            while index < len(lines):
                continuation = lines[index].rstrip()
                continuation_stripped = continuation.strip()
                if not continuation_stripped:
                    break
                if is_section_heading(continuation):
                    break
                if parse_figure_number(continuation_stripped):
                    break
                caption_lines.append(continuation_stripped)
                index += 1
            out.append(f"[[FIGURE {figure_number}]]")
            out.append("")
            out.append(normalize_paragraph(caption_lines))
            out.append("")
            continue
        if is_section_heading(line):
            flush_para()
            flush_code()
            out.append("## " + stripped)
            out.append("")
            index += 1
            continue
        if is_code_line(line):
            flush_para()
            code.append(line)
            index += 1
            continue
        flush_code()
        para.append(line)
        index += 1

    flush_para()
    flush_code()
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out).strip()


def split_chapter(chapter_no: str, chapter_title: str, body: str) -> list[Unit]:
    units: list[Unit] = []
    section_matches = list(re.finditer(r"(?m)^(\d+\.\d+)\s+(.+)$", body))
    intro_end = section_matches[0].start() if section_matches else len(body)
    intro_body = body[:intro_end].strip()

    units.append(
        Unit(
            key=f"chapter-{chapter_no}",
            markdown=build_unit_markdown(f"Chapter {chapter_no}: {chapter_title}", intro_body, level=1),
        )
    )

    for idx, match in enumerate(section_matches):
        section_no = match.group(1)
        section_title = match.group(2).strip()
        start = match.end()
        end = section_matches[idx + 1].start() if idx + 1 < len(section_matches) else len(body)
        section_body = body[start:end].strip()
        units.append(
            Unit(
                key=f"chapter-{chapter_no}-section-{section_no.replace('.', '-')}",
                markdown=build_unit_markdown(f"{section_no} {section_title}", section_body, level=2),
            )
        )

    return units


def split_book(text: str) -> list[Unit]:
    units: list[Unit] = []

    contents_match = re.search(r"\nContents\n(.*?)\nForeword and acknowledgments\n", text, re.S)
    foreword_match = re.search(r"\nForeword and acknowledgments\n(.*?)\nChapter 1\n", text, re.S)
    chapter_matches = list(re.finditer(r"\nChapter (\d+)\n\n([^\n]+)\n", text))

    title_page = text.split("\nContents\n", 1)[0].strip()
    if title_page:
        title_lines = [line.strip() for line in title_page.splitlines() if line.strip()]
        title_heading = title_lines[0] if title_lines else "xv6"
        title_body = "\n".join(title_lines[1:])
        units.append(Unit("title", build_unit_markdown(title_heading, title_body, level=1)))

    if contents_match:
        units.append(Unit("contents", build_unit_markdown("Contents", contents_match.group(1), level=1)))

    if foreword_match:
        units.append(
            Unit(
                "foreword",
                build_unit_markdown("Foreword and acknowledgments", foreword_match.group(1), level=1),
            )
        )

    for idx, match in enumerate(chapter_matches):
        chapter_no = match.group(1)
        chapter_title = match.group(2).strip()
        start = match.end()
        end = chapter_matches[idx + 1].start() if idx + 1 < len(chapter_matches) else len(text)
        body = text[start:end].strip()
        units.extend(split_chapter(chapter_no, chapter_title, body))

    return units


def load_cache(path: Path) -> dict[str, str]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_cache(path: Path, cache: dict[str, str]) -> None:
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def translate_markdown(markdown_text: str, cache: dict[str, str], model: str) -> str:
    digest = hashlib.sha256((model + "\n" + markdown_text).encode("utf-8")).hexdigest()
    if digest in cache:
        return cache[digest]

    prompt = f"""把下面这段 xv6 操作系统教材内容翻译成简体中文。

要求：
1. 严格保留 Markdown 结构，例如 #、##、列表、代码块。
2. 代码块、函数名、文件名、命令、错误消息、代码标识符保持英文原样。
3. 技术术语尽量统一，例如 operating system=操作系统，kernel=内核，process=进程，page table=页表，trap=陷阱，file descriptor=文件描述符，scheduler=调度器。
4. 不要省略，不要总结，不要添加前言、结语或注释。
5. 图号、引用编号、URL、章节编号保留原样。
6. 占位标记如 [[FIGURE 1.1]] 必须原样保留，不翻译，不改格式。

只输出翻译后的 Markdown。

{markdown_text}
"""

    cmd = ["claude", "-p", "--bare", "--effort", "low"]
    if model != "default":
        cmd.extend(["--model", model])
    output = run(cmd, input_text=prompt, timeout=240).strip()
    cache[digest] = output
    return output


def build_translated_markdown(units: Iterable[Unit], cache: dict[str, str], model: str) -> str:
    translated_units: list[str] = []
    for index, unit in enumerate(units, start=1):
        print(f"[{index}] translating {unit.key}", file=sys.stderr)
        translated_units.append(translate_markdown(unit.markdown, cache, model).strip())
        translated_units.append("")
        save_cache(CACHE_PATH, cache)
    return "\n".join(translated_units).strip() + "\n"


def inline_markup(text: str) -> str:
    text = text.replace("&emsp;", "    ").replace("&ensp;", "  ").replace("&nbsp;", " ")
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r'<font name="Courier">\1</font>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    return escaped


def looks_like_body_line(line: dict[str, float | str], page_width: float) -> bool:
    text = str(line["text"])
    width = float(line["x1"]) - float(line["x0"])
    return float(line["x0"]) <= 95 and width >= page_width * 0.72 and text.count(" ") >= 6


def extract_figure_images(pdf_path: Path) -> dict[str, Path]:
    import pdfplumber

    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    figure_paths: dict[str, Path] = {}

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            lines = page.extract_text_lines(layout=False, strip=False, return_chars=False)
            if not lines:
                continue
            for idx, line in enumerate(lines):
                figure_number = parse_figure_number(str(line["text"]))
                if not figure_number:
                    continue

                figure_lines: list[dict[str, float | str]] = []
                next_top = float(line["top"])
                cursor = idx - 1
                while cursor >= 0:
                    candidate = lines[cursor]
                    gap = next_top - float(candidate["bottom"])
                    if gap > FIGURE_GAP_MAX:
                        break
                    if looks_like_body_line(candidate, page.width):
                        break
                    figure_lines.insert(0, candidate)
                    next_top = float(candidate["top"])
                    cursor -= 1

                if not figure_lines:
                    continue

                top = max(24.0, min(float(item["top"]) for item in figure_lines) - 18.0)
                bottom = min(page.height - 24.0, float(line["top"]) - 8.0)
                left = max(24.0, min(float(item["x0"]) for item in figure_lines) - 48.0)
                right = min(page.width - 24.0, max(float(item["x1"]) for item in figure_lines) + 48.0)
                if bottom <= top or right <= left:
                    continue

                target = FIGURE_DIR / f"figure-{figure_number.replace('.', '-')}.png"
                page.crop((left, top, right, bottom)).to_image(resolution=170).save(str(target), format="PNG")
                figure_paths[figure_number] = target

    return figure_paths


def markdown_to_story(markdown_text: str, figure_images: dict[str, Path]):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.pdfmetrics import registerFont
    from reportlab.platypus import Image, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer

    registerFont(UnicodeCIDFont("STSong-Light"))

    styles = getSampleStyleSheet()
    base = ParagraphStyle(
        "CJKBase",
        parent=styles["BodyText"],
        fontName="STSong-Light",
        fontSize=10.5,
        leading=16,
        spaceAfter=8,
    )
    h1 = ParagraphStyle(
        "CJKH1",
        parent=base,
        fontSize=20,
        leading=28,
        spaceBefore=10,
        spaceAfter=16,
        textColor=HexColor("#7d2a18"),
    )
    h2 = ParagraphStyle(
        "CJKH2",
        parent=base,
        fontSize=14,
        leading=20,
        spaceBefore=10,
        spaceAfter=10,
        textColor=HexColor("#3d2d25"),
    )
    title = ParagraphStyle(
        "CJKTitle",
        parent=h1,
        alignment=TA_CENTER,
        fontSize=24,
        leading=34,
        spaceAfter=22,
    )
    meta = ParagraphStyle(
        "CJKMeta",
        parent=base,
        alignment=TA_CENTER,
        textColor=HexColor("#6a5a4e"),
        spaceAfter=10,
    )
    bullet = ParagraphStyle(
        "CJKBullet",
        parent=base,
        leftIndent=18,
        firstLineIndent=-10,
    )
    figure_caption = ParagraphStyle(
        "CJKFigureCaption",
        parent=base,
        alignment=TA_CENTER,
        fontSize=9.2,
        leading=12,
        textColor=HexColor("#5f5148"),
        spaceAfter=12,
    )
    code_style = ParagraphStyle(
        "CJKCode",
        parent=styles["Code"],
        fontName="Courier",
        fontSize=8.4,
        leading=11,
        leftIndent=18,
        rightIndent=18,
        spaceAfter=10,
    )
    code_style_cjk = ParagraphStyle(
        "CJKCodeCJK",
        parent=styles["Code"],
        fontName="STSong-Light",
        fontSize=8.8,
        leading=12,
        leftIndent=18,
        rightIndent=18,
        spaceAfter=10,
    )

    def add_page_number(canvas, doc):
        canvas.setFont("Helvetica", 9)
        canvas.setFillColor(HexColor("#6a5a4e"))
        canvas.drawRightString(doc.pagesize[0] - 0.65 * inch, 0.45 * inch, f"{canvas.getPageNumber()}")

    story = []
    code_mode = False
    code_lines: list[str] = []
    pending_para: list[str] = []
    first_h1 = True
    pending_title_page = True
    title_page_mode = False
    skip_next_h1_break = False
    pending_figure_number: str | None = None

    def flush_para() -> None:
        nonlocal pending_para, pending_figure_number, skip_next_h1_break, title_page_mode
        if not pending_para:
            return
        text = " ".join(line.strip() for line in pending_para if line.strip())
        if text:
            if title_page_mode:
                style = meta
            elif pending_figure_number:
                style = figure_caption
            else:
                style = base
            story.append(Paragraph(inline_markup(text), style))
            if title_page_mode:
                story.append(PageBreak())
                title_page_mode = False
                skip_next_h1_break = True
            elif pending_figure_number:
                pending_figure_number = None
        pending_para = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines:
            code_text = "\n".join(code_lines)
            if re.search(r"[\u4e00-\u9fff]", code_text):
                for raw_line in code_lines:
                    if raw_line.strip():
                        preserved = html.escape(raw_line).replace(" ", "&nbsp;")
                        story.append(Paragraph(preserved, code_style_cjk))
                    else:
                        story.append(Spacer(1, 0.06 * inch))
            else:
                story.append(Preformatted(code_text, code_style))
        code_lines = []

    lines = markdown_text.splitlines()
    for line in lines:
        if line.startswith("```"):
            flush_para()
            if code_mode:
                flush_code()
            code_mode = not code_mode
            continue

        if code_mode:
            code_lines.append(line)
            continue

        figure_marker = FIGURE_MARKER_RE.match(line.strip())
        if figure_marker:
            flush_para()
            flush_code()
            figure_no = figure_marker.group(1)
            image_path = figure_images.get(figure_no)
            if image_path and image_path.exists():
                flowable = Image(str(image_path))
                flowable._restrictSize(6.4 * inch, 5.6 * inch)
                flowable.hAlign = "CENTER"
                story.append(flowable)
                story.append(Spacer(1, 0.08 * inch))
            pending_figure_number = figure_no
            continue

        if line.startswith("# "):
            flush_para()
            flush_code()
            if not first_h1 and not skip_next_h1_break:
                story.append(PageBreak())
            skip_next_h1_break = False
            first_h1 = False
            if pending_title_page:
                story.append(Spacer(1, 1.2 * inch))
                story.append(Paragraph(inline_markup(line[2:].strip()), title))
                story.append(Paragraph("根据 MIT xv6 英文原版自动翻译整理", meta))
                pending_title_page = False
                title_page_mode = True
            else:
                story.append(Paragraph(inline_markup(line[2:].strip()), h1))
            continue

        if line.startswith("## "):
            flush_para()
            flush_code()
            story.append(Paragraph(inline_markup(line[3:].strip()), h2))
            continue

        if line.startswith(("- ", "* ")):
            flush_para()
            flush_code()
            story.append(Paragraph(inline_markup("• " + line[2:].strip()), bullet))
            continue

        if not line.strip():
            flush_para()
            story.append(Spacer(1, 0.06 * inch))
            continue

        pending_para.append(line)

    flush_para()
    flush_code()

    doc = SimpleDocTemplate(
        str(OUTPUT_PDF),
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.7 * inch,
        title="xv6 中文版",
        author="OpenAI Codex",
    )
    return doc, story, add_page_number


def build_pdf(markdown_text: str, pdf_path: Path) -> None:
    figure_images = extract_figure_images(pdf_path)
    doc, story, add_page_number = markdown_to_story(markdown_text, figure_images)
    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a Chinese PDF translation for the xv6 book.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--model", default="default")
    parser.add_argument("--skip-translate", action="store_true")
    args = parser.parse_args()

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    FINAL_DIR.mkdir(parents=True, exist_ok=True)

    if RAW_TEXT_PATH.exists():
        raw_text = RAW_TEXT_PATH.read_text(encoding="utf-8")
    else:
        raw_text = clean_text(extract_pdf_text(args.pdf))
        RAW_TEXT_PATH.write_text(raw_text, encoding="utf-8")

    units = split_book(raw_text)
    if not units:
        raise RuntimeError("failed to split xv6 book into translatable units")

    cache = load_cache(CACHE_PATH)
    if args.skip_translate and MARKDOWN_PATH.exists():
        markdown_text = MARKDOWN_PATH.read_text(encoding="utf-8")
    else:
        markdown_text = build_translated_markdown(units, cache, args.model)
        MARKDOWN_PATH.write_text(markdown_text, encoding="utf-8")
        save_cache(CACHE_PATH, cache)

    build_pdf(markdown_text, args.pdf)
    print(OUTPUT_PDF)


if __name__ == "__main__":
    main()
