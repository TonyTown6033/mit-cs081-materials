#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString


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
SEPARATOR = "\n<CODEX_ZH_SEP_20260409>\n"
MAX_BATCH_CHARS = 3200
MAX_BATCH_ITEMS = 40
REQUEST_SLEEP = 0.2

MANUAL_TRANSLATIONS = {
    "Schedule": "日程",
    "Overview": "概览",
    "Class": "课程",
    "Labs": "实验",
    "Tools": "工具",
    "Guidance": "指南",
    "References": "参考资料",
    "News": "课程动态",
    "Administrivia": "课程说明",
    "Piazza": "Piazza",
    "Handin website": "提交网站",
    "Top": "顶部",
    "Toggle navigation": "切换导航",
    "Creative Commons License": "知识共享许可",
    "Acknowledgements": "致谢",
    "Catalog description": "课程简介",
    "Course Structure": "课程结构",
    "Learning by doing": "边做边学",
    "Git user's manual": "Git 用户手册",
    "lab tools page": "实验工具页面",
    "Grading policy": "评分政策",
    "Lab: Xv6 and Unix utilities": "实验：xv6 与 Unix 工具",
    "Lab: System calls": "实验：系统调用",
    "Lab: Page tables": "实验：页表",
    "Lab: Traps": "实验：陷阱",
    "Lab: Lazy allocation": "实验：惰性分配",
    "Lab: Copy-on-Write Fork for xv6": "实验：xv6 的写时复制 fork",
    "Lab: threading": "实验：线程",
    "Lab: locks": "实验：锁",
    "Lab: file system": "实验：文件系统",
    "Lab: mmap": "实验：mmap",
    "Lab: network driver": "实验：网络驱动",
    "Lab Utilities": "实验：Unix 工具",
    "Lab System calls": "实验：系统调用",
    "Lab Page tables": "实验：页表",
    "Lab Traps": "实验：陷阱",
    "Lab Lazy allocation": "实验：惰性分配",
    "Lab Copy on-write": "实验：写时复制",
    "Lab Multithreading": "实验：多线程",
    "Lab Lock": "实验：锁",
    "Lab File system": "实验：文件系统",
    "Lab mmap": "实验：mmap",
    "Lab network driver": "实验：网络驱动",
    "6.S081: Operating System Engineering": "6.S081：操作系统工程",
    "6.1810: Operating System Engineering": "6.1810：操作系统工程",
}


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


NORMALIZED_MANUAL_TRANSLATIONS = {
    collapse_spaces(key): value for key, value in MANUAL_TRANSLATIONS.items()
}


def looks_like_code(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.startswith(("http://", "https://", "mailto:")):
        return True
    if stripped.startswith(("$", "#", "%", ">", "<", "//")):
        return True
    if re.fullmatch(r"[A-Za-z0-9_./:+?&=%@#-]+", stripped) and any(
        token in stripped for token in "/._:+?&=%@#-"
    ):
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
        "Piazza": "Piazza",
        "Git": "Git",
        "Unix": "Unix",
        "RISC-V": "RISC-V",
        "Risc-V": "RISC-V",
        "6.828网站": "6.828 网站",
        "S6.081首页": "S6.081 首页",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text


def build_batches(pending: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for item in pending:
        item_cost = len(item) + len(SEPARATOR)
        if current and (
            len(current) >= MAX_BATCH_ITEMS
            or current_chars + item_cost > MAX_BATCH_CHARS
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_cost

    if current:
        batches.append(current)
    return batches


def request_translation(joined_text: str) -> str:
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=en&tl=zh-CN&dt=t&q={quote(joined_text)}"
    )
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=60) as response:  # pragma: no cover - network-bound
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    return "".join(part[0] for part in data[0] if part and part[0] is not None)


def translate_many_texts(batch: list[str]) -> list[str]:
    joined = SEPARATOR.join(batch)
    translated = request_translation(joined)
    parts = translated.split(SEPARATOR)
    if len(parts) != len(batch):
        raise RuntimeError(
            f"Separator split mismatch: expected {len(batch)}, got {len(parts)}"
        )
    return parts


def fill_cache_with_batch(cache: dict[str, str], pending: list[str]) -> None:
    pending = [text for text in pending if text not in cache]
    if not pending:
        print("no pending translations", flush=True)
        return

    for normalized, translated in NORMALIZED_MANUAL_TRANSLATIONS.items():
        if normalized in pending and normalized not in cache:
            cache[normalized] = translated

    pending = [text for text in pending if text not in cache]
    batches = build_batches(pending)
    total = len(pending)
    done = 0

    print(
        f"translating unique strings: {total} across {len(batches)} batches",
        flush=True,
    )

    for index, batch in enumerate(batches, start=1):
        try:
            translated_batch = translate_many_texts(batch)
            for src, dst in zip(batch, translated_batch):
                cache[src] = postprocess_translation(dst)
        except Exception:
            for item in batch:
                last_error: Exception | None = None
                for attempt in range(3):
                    try:
                        cache[item] = postprocess_translation(
                            request_translation(item)
                        )
                        break
                    except Exception as exc:  # pragma: no cover - network-bound
                        last_error = exc
                        time.sleep(1.5 * (attempt + 1))
                else:
                    raise RuntimeError(f"Translation failed for text: {item}") from last_error

        done += len(batch)
        save_cache(cache)
        print(
            f"batch {index}/{len(batches)} done: {done}/{total}",
            flush=True,
        )
        time.sleep(REQUEST_SLEEP)


def translate_text_preserving_padding(text: str, cache: dict[str, str]) -> str:
    if not should_translate_text(text):
        return text
    leading = re.match(r"^\s*", text).group(0)
    trailing = re.search(r"\s*$", text).group(0)
    normalized = collapse_spaces(text)
    translated = cache.get(normalized, normalized)
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


def collect_pending_from_html(path: Path, cache: dict[str, str]) -> list[str]:
    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    pending: list[str] = []

    for tag in soup.find_all(True):
        for attr in TRANSLATABLE_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str) and should_translate_text(value):
                normalized = collapse_spaces(value)
                if normalized not in cache:
                    pending.append(normalized)

    for node in soup.descendants:
        if isinstance(node, (Comment, Doctype)) or not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in SKIP_HTML_TAGS:
            continue
        text = str(node)
        if should_translate_text(text):
            normalized = collapse_spaces(text)
            if normalized not in cache:
                pending.append(normalized)

    return pending


def collect_pending_from_txt(path: Path, cache: dict[str, str]) -> list[str]:
    pending: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if should_translate_line(line):
            normalized = collapse_spaces(line)
            if normalized not in cache:
                pending.append(normalized)
    return pending


def translate_html_file(path: Path, cache: dict[str, str]) -> bool:
    original = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(original, "html.parser")

    if soup.html and not soup.html.get("lang"):
        soup.html["lang"] = "zh-CN"

    ensure_utf8_meta(soup)
    ensure_html_banner(soup)

    changed = False

    for tag in soup.find_all(True):
        for attr in TRANSLATABLE_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str) and should_translate_text(value):
                translated = translate_text_preserving_padding(value, cache)
                if translated != value:
                    tag[attr] = translated
                    changed = True

    for node in list(soup.descendants):
        if isinstance(node, (Comment, Doctype)) or not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in SKIP_HTML_TAGS:
            continue
        text = str(node)
        translated = translate_text_preserving_padding(text, cache)
        if translated != text:
            node.replace_with(translated)
            changed = True

    translated_html = str(soup)
    if translated_html != original:
        path.write_text(translated_html, encoding="utf-8")
        return True
    return changed


def translate_txt_file(path: Path, cache: dict[str, str]) -> bool:
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
        if should_translate_line(line):
            leading = re.match(r"^\s*", line).group(0)
            translated = cache.get(collapse_spaces(line), line.strip())
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
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in TEXT_FILE_SUFFIXES
    ]
    return sorted(files)


def collect_pending_strings(files: list[Path], cache: dict[str, str]) -> list[str]:
    pending: list[str] = []
    for path in files:
        if path.suffix.lower() == ".html":
            pending.extend(collect_pending_from_html(path, cache))
        elif path.suffix.lower() == ".txt":
            pending.extend(collect_pending_from_txt(path, cache))
    return sorted(set(pending))


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
    files = target_files(root, args.paths)

    print(f"target files: {len(files)}", flush=True)
    pending = collect_pending_strings(files, cache)
    fill_cache_with_batch(cache, pending)

    changed_files = 0
    for index, path in enumerate(files, start=1):
        rel = path.relative_to(ROOT)
        try:
            if path.suffix.lower() == ".html":
                changed = translate_html_file(path, cache)
            elif path.suffix.lower() == ".txt":
                changed = translate_txt_file(path, cache)
            else:
                changed = False
            if changed:
                changed_files += 1
                print(f"applied {index}/{len(files)} {rel}", flush=True)
        except Exception as exc:
            print(f"failed {rel}: {exc}", file=sys.stderr, flush=True)

    save_cache(cache)
    print(
        f"done: changed_files={changed_files}, cache_size={len(cache)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
