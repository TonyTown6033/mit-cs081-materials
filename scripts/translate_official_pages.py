#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString
from deep_translator import GoogleTranslator


ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / ".translate-cache.json"
HTML_BANNER_ID = "codex-zh-translation-note"
HTML_BANNER_TEXT = (
    "本页为自动汉化版本，保留原链接、代码块与文件结构。"
    "技术术语和细节翻译可能不完全统一，请以原文和源码为准。"
)
TXT_BANNER = (
    "[自动汉化说明]\n"
    "本文件为自动汉化版本，尽量保留原有结构与技术名词。"
    "代码、命令、路径与链接通常保持原样。"
)
SKIP_HTML_TAGS = {
    "script",
    "style",
    "pre",
    "code",
    "kbd",
    "tt",
    "samp",
    "textarea",
    "option",
}
TRANSLATABLE_ATTRS = ("title", "alt", "placeholder", "aria-label")
TEXT_FILE_SUFFIXES = {".html", ".txt"}


def load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def looks_like_code(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.startswith(("http://", "https://", "mailto:")):
        return True
    if stripped.startswith(("$", "#", "%", ">", "<", "//")):
        return True
    if re.fullmatch(r"[A-Za-z0-9_./:+?&=%@#-]+", stripped):
        return True
    if any(token in stripped for token in ("::", "->", "=>", "==", "!=", "&&", "||")):
        return True
    if any(token in stripped for token in ("Makefile", ".c", ".h", ".S", ".py", ".sh", ".pdf")) and " " not in stripped:
        return True
    if re.fullmatch(r"[0-9A-Za-z_.-]+/[0-9A-Za-z_./-]+", stripped):
        return True
    return False


def should_translate_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if not re.search(r"[A-Za-z]", stripped):
        return False
    if looks_like_code(stripped):
        return False
    return True


def should_translate_line(text: str) -> bool:
    stripped = text.strip()
    if not should_translate_text(stripped):
        return False
    if len(stripped) <= 2:
        return False
    if stripped.startswith(("    ", "\t")):
        return False
    return True


def postprocess_translation(text: str) -> str:
    replacements = {
        "Xv6": "xv6",
        "XV6": "xv6",
        "实验室：": "实验：",
        "实验室 ": "实验 ",
        "操作系统工程": "操作系统工程",
        "Piazza": "Piazza",
        "Git": "Git",
        "Unix": "Unix",
        "RISC-V": "RISC-V",
        "Risc-V": "RISC-V",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def translate_string(
    text: str,
    translator: GoogleTranslator,
    cache: dict[str, str],
    progress_counter: list[int],
) -> str:
    normalized = collapse_spaces(text)
    if normalized in cache:
        return cache[normalized]

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            translated = translator.translate(normalized)
            translated = postprocess_translation(translated)
            cache[normalized] = translated
            progress_counter[0] += 1
            if progress_counter[0] % 20 == 0:
                save_cache(cache)
            return translated
        except Exception as exc:  # pragma: no cover - network-bound
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(f"Translation failed for text: {normalized}") from last_error


def translate_text_preserving_padding(
    text: str,
    translator: GoogleTranslator,
    cache: dict[str, str],
    progress_counter: list[int],
) -> str:
    if not should_translate_text(text):
        return text
    leading = re.match(r"^\s*", text).group(0)
    trailing = re.search(r"\s*$", text).group(0)
    core = text.strip()
    translated = translate_string(core, translator, cache, progress_counter)
    return f"{leading}{translated}{trailing}"


def ensure_utf8_meta(soup: BeautifulSoup) -> None:
    head = soup.head
    if head is None:
        return
    if not head.find("meta", attrs={"charset": True}):
        meta = soup.new_tag("meta")
        meta.attrs["charset"] = "utf-8"
        head.insert(0, meta)


def ensure_html_banner(soup: BeautifulSoup) -> None:
    body = soup.body
    if body is None or body.find(id=HTML_BANNER_ID):
        return
    banner = soup.new_tag("div", id=HTML_BANNER_ID)
    banner["style"] = (
        "background:#fff3cd;border:1px solid #e0c56e;padding:10px 14px;"
        "margin:12px;color:#6b5300;font-size:14px;line-height:1.5;"
    )
    banner.string = HTML_BANNER_TEXT
    body.insert(0, banner)


def translate_html_file(
    path: Path,
    translator: GoogleTranslator,
    cache: dict[str, str],
    progress_counter: list[int],
) -> bool:
    original = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(original, "html.parser")

    if soup.html and not soup.html.get("lang"):
        soup.html["lang"] = "zh-CN"

    ensure_utf8_meta(soup)
    ensure_html_banner(soup)

    for tag in soup.find_all(True):
        for attr in TRANSLATABLE_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str) and should_translate_text(value):
                tag[attr] = translate_text_preserving_padding(
                    value, translator, cache, progress_counter
                )

    for node in list(soup.descendants):
        if isinstance(node, (Comment, Doctype)):
            continue
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in SKIP_HTML_TAGS:
            continue
        text = str(node)
        translated = translate_text_preserving_padding(
            text, translator, cache, progress_counter
        )
        if translated != text:
            node.replace_with(translated)

    translated_html = str(soup)
    if translated_html == original:
        return False
    path.write_text(translated_html, encoding="utf-8")
    return True


def translate_txt_file(
    path: Path,
    translator: GoogleTranslator,
    cache: dict[str, str],
    progress_counter: list[int],
) -> bool:
    original = path.read_text(encoding="utf-8", errors="ignore")
    lines = original.splitlines()
    out_lines: list[str] = []
    changed = False

    if not original.startswith("[自动汉化说明]"):
        out_lines.extend(TXT_BANNER.splitlines())
        out_lines.append("")
        changed = True

    for line in lines:
        if not line.strip():
            out_lines.append(line)
            continue
        leading = re.match(r"^\s*", line).group(0)
        core = line[len(leading) :]
        if should_translate_line(core):
            translated = translate_string(core.strip(), translator, cache, progress_counter)
            new_line = f"{leading}{translated}"
            out_lines.append(new_line)
            if new_line != line:
                changed = True
        else:
            out_lines.append(line)

    output = "\n".join(out_lines)
    if original.endswith("\n"):
        output += "\n"
    if changed:
        path.write_text(output, encoding="utf-8")
    return changed


def target_files(root: Path, only_paths: list[str]) -> list[Path]:
    if only_paths:
        return [root / rel for rel in only_paths]
    files = [
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in TEXT_FILE_SUFFIXES
    ]
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default="official-pages",
        help="Root directory to translate in place",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional relative file paths under --root to translate",
    )
    args = parser.parse_args()

    root = (ROOT / args.root).resolve()
    if not root.exists():
        print(f"Root not found: {root}", file=sys.stderr)
        return 1

    cache = load_cache()
    translator = GoogleTranslator(source="en", target="zh-CN")
    progress_counter = [0]

    files = target_files(root, args.paths)
    changed_files = 0

    for path in files:
        rel = path.relative_to(ROOT)
        try:
            if path.suffix.lower() == ".html":
                changed = translate_html_file(path, translator, cache, progress_counter)
            elif path.suffix.lower() == ".txt":
                changed = translate_txt_file(path, translator, cache, progress_counter)
            else:
                changed = False
            if changed:
                changed_files += 1
                print(f"translated {rel}")
        except Exception as exc:
            print(f"failed {rel}: {exc}", file=sys.stderr)

    save_cache(cache)
    print(
        f"done: changed_files={changed_files}, new_translations={progress_counter[0]}, cache_size={len(cache)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
