#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import quote

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_REV = "f624d4b"
DEFAULT_CACHE_PATH = ROOT / ".translate-ai-cache.json"
STEPFUN_API_URL = "https://api.stepfun.com/v1/chat/completions"
DEFAULT_PROVIDER = "claude"
DEFAULT_MODEL = ""
HTML_BANNER_ID = "codex-zh-translation-note"
HTML_BANNER_TEXT = (
    "本页为 AI 汉化版本，保留原链接、代码块与文件结构。"
    "技术术语和细节翻译已尽量统一，但少数地方仍建议结合原文与源码阅读。"
)
TXT_BANNER = (
    "[AI 汉化说明]\n"
    "本文件为 AI 汉化版本，尽量保留原有结构与技术名词。"
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
MAX_BATCH_ITEMS = 12
MAX_BATCH_CHARS = 2400
MAX_TXT_CHUNK_LINES = 48
MAX_TXT_CHUNK_CHARS = 2600
REQUEST_SLEEP = 0.2

SYSTEM_PROMPT = """You are a professional English-to-Simplified-Chinese translator for MIT operating systems course materials.
Return only valid JSON.

Translation rules:
- Keep links, URLs, email addresses, shell commands, source code, file paths, file names, and placeholders unchanged.
- Preserve technical identifiers such as xv6, Unix, Linux, BSD, RISC-V, QEMU, Git, fork, exec, sbrk, mmap, COW, and Piazza unless they appear inside a larger Chinese phrase.
- Use concise, natural, technically precise Chinese suitable for computer science students.
- Keep terminology consistent across items from the same file.

Preferred terminology:
- Operating System Engineering -> 操作系统工程
- Overview -> 概览
- Schedule -> 日程
- Class -> 课程
- Labs -> 实验
- Tools -> 工具
- Guidance -> 指南
- References -> 参考资料
- Administrivia -> 课程说明
- system call -> 系统调用
- page table -> 页表
- trap -> 陷阱
- interrupt -> 中断
- thread -> 线程
- lock -> 锁
- file system -> 文件系统
- network driver -> 网络驱动
- lazy allocation -> 惰性分配
- copy-on-write -> 写时复制
- kernel -> 内核
- user space / user mode -> 用户态
- isolation -> 隔离
- virtual memory -> 虚拟内存
"""

TEXT_BLOCK_SYSTEM_PROMPT = """You are a professional English-to-Simplified-Chinese translator for MIT operating systems course materials.

Translation rules:
- Follow the user's required output format exactly.
- Preserve code, shell commands, file paths, file names, URLs, email addresses, and placeholders.
- Use concise, natural, technically precise Chinese suitable for computer science students.
- Keep terminology consistent.

Preferred terminology:
- isolation -> 隔离
- system call -> 系统调用
- page table -> 页表
- trap -> 陷阱
- interrupt -> 中断
- thread -> 线程
- lock -> 锁
- file system -> 文件系统
- network driver -> 网络驱动
- lazy allocation -> 惰性分配
- copy-on-write -> 写时复制
- kernel -> 内核
- user mode / user space -> 用户态
- virtual memory -> 虚拟内存
"""

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
    "Course Structure": "课程结构",
    "Catalog description": "课程简介",
    "Learning by doing": "边做边学",
    "Top": "返回顶部",
    "Toggle navigation": "切换导航",
    "Creative Commons License": "知识共享许可",
    "Acknowledgements": "致谢",
    "Handin website": "提交网站",
    "xv6 book": "xv6 书",
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


def load_cache(cache_path: Path) -> dict[str, str]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_cache(cache: dict[str, str], cache_path: Path) -> None:
    cache_path.write_text(
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
    if stripped.startswith(("$ ", "# ", "% ", "./", "../")):
        return True
    if re.fullmatch(r"~?/?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?", stripped):
        return True
    if re.fullmatch(r"[A-Za-z0-9_.-]+\.(c|h|S|py|sh|pl|tex|pdf|html|txt|md)", stripped):
        return True
    if re.fullmatch(r"[A-Za-z0-9_./:+?&=%@#-]+", stripped) and any(
        token in stripped for token in ("/", "@", "?", "&", "=", ":")
    ):
        return True
    if re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_]*\(\)\s*){1,3}", stripped):
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
        "Linux": "Linux",
        "RISC-V": "RISC-V",
        "Risc-V": "RISC-V",
        "QEMU": "QEMU",
        "6.828网站": "6.828 网站",
        "6.S081主页": "6.S081 首页",
        "6.1810主页": "6.1810 首页",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.strip()


def target_files(root: Path, only_paths: list[str]) -> list[Path]:
    if only_paths:
        normalized_paths: list[Path] = []
        root_name = root.name
        for rel in only_paths:
            rel_path = Path(rel)
            parts = rel_path.parts
            if parts and parts[0] == root_name:
                rel_path = Path(*parts[1:])
            normalized_paths.append(root / rel_path)
        return normalized_paths
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in TEXT_FILE_SUFFIXES
    ]
    return sorted(files)


def read_source_file_from_git(source_rev: str, rel_path: Path) -> str:
    proc = subprocess.run(
        ["git", "show", f"{source_rev}:{rel_path.as_posix()}"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git show failed for {rel_path}")
    return proc.stdout


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


def extract_html_title(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return collapse_spaces(soup.title.get_text(" ", strip=True))
    return ""


def collect_pending_from_html(html: str, cache: dict[str, str]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    pending: list[str] = []
    seen: set[str] = set()

    for tag in soup.find_all(True):
        for attr in TRANSLATABLE_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str) and should_translate_text(value):
                normalized = collapse_spaces(value)
                if normalized not in cache and normalized not in seen:
                    pending.append(normalized)
                    seen.add(normalized)

    for node in soup.descendants:
        if isinstance(node, (Comment, Doctype)) or not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent is None or parent.name in SKIP_HTML_TAGS:
            continue
        text = str(node)
        if should_translate_text(text):
            normalized = collapse_spaces(text)
            if normalized not in cache and normalized not in seen:
                pending.append(normalized)
                seen.add(normalized)

    return pending


def collect_pending_from_txt(content: str, cache: dict[str, str]) -> list[str]:
    pending: list[str] = []
    seen: set[str] = set()
    for line in content.splitlines():
        if should_translate_line(line):
            normalized = collapse_spaces(line)
            if normalized not in cache and normalized not in seen:
                pending.append(normalized)
                seen.add(normalized)
    return pending


def build_batches(pending: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for item in pending:
        item_cost = len(item) + 40
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


def parse_json_array(text: str) -> list[dict[str, str]]:
    raw = text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\[\s*\{.*\}\s*\]", raw, flags=re.S)
        if not match:
            raise
        data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise ValueError("model output is not a JSON array")
    return data


def chat_complete_stepfun(messages: list[dict[str, str]], model: str) -> str:
    api_key = os.environ.get("STEPFUN_API_KEY")
    if not api_key:
        raise RuntimeError("STEPFUN_API_KEY is not set")

    payload = {
        "model": model or "step-3.5-flash",
        "temperature": 0.1,
        "messages": messages,
    }
    req = urlrequest.Request(
        STEPFUN_API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=120) as response:
        body = json.loads(response.read().decode("utf-8"))
    return body["choices"][0]["message"]["content"]


def chat_complete_claude(system_prompt: str, user_prompt: str, model: str) -> str:
    cmd = [
        "claude",
        "-p",
        "--bare",
        "--effort",
        "low",
        "--output-format",
        "text",
        "--system-prompt",
        system_prompt,
    ]
    if model:
        cmd.extend(["--model", model])
    cmd.append(user_prompt)

    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if proc.returncode != 0:
        details = proc.stderr.strip() or proc.stdout.strip() or "claude command failed"
        raise RuntimeError(details)
    return proc.stdout.strip()


def chat_complete(
    *,
    system_prompt: str,
    user_prompt: str,
    provider: str,
    model: str,
) -> str:
    if provider == "claude":
        return chat_complete_claude(system_prompt, user_prompt, model)
    if provider == "stepfun":
        return chat_complete_stepfun(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
        )
    raise ValueError(f"unsupported provider: {provider}")


def google_translate_text(text: str) -> str:
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=en&tl=zh-CN&dt=t&q={quote(text)}"
    )
    request = urlrequest.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlrequest.urlopen(request, timeout=60) as response:
        payload = response.read().decode("utf-8")
    data = json.loads(payload)
    return "".join(part[0] for part in data[0] if part and part[0] is not None)


def translate_batch_with_ai(
    batch: list[str],
    *,
    rel_path: Path,
    context_title: str,
    provider: str,
    model: str,
) -> list[str]:
    if len(batch) == 1:
        return [
            translate_single_text(
                batch[0],
                rel_path=rel_path,
                context_title=context_title,
                provider=provider,
                model=model,
            )
        ]

    items = [{"id": str(index), "text": text} for index, text in enumerate(batch, start=1)]
    context_lines = [f"File path: {rel_path.as_posix()}"]
    if context_title:
        context_lines.append(f"Page title: {context_title}")
    context_lines.append("Translate each item independently but keep terminology consistent.")

    user_prompt = (
        "\n".join(context_lines)
        + "\n\nReturn only a JSON array of objects with keys id and translation.\n"
        + "Input JSON:\n"
        + json.dumps(items, ensure_ascii=False)
    )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = chat_complete(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                provider=provider,
                model=model,
            )
            parsed = parse_json_array(raw)
            translations_by_id: dict[str, str] = {}
            for item in parsed:
                item_id = str(item["id"])
                translation = postprocess_translation(str(item["translation"]))
                translations_by_id[item_id] = translation

            translated_batch = []
            for index in range(1, len(batch) + 1):
                item_id = str(index)
                if item_id not in translations_by_id:
                    raise ValueError(f"missing translation for id={item_id}")
                translated_batch.append(translations_by_id[item_id])
            return translated_batch
        except (
            KeyError,
            ValueError,
            json.JSONDecodeError,
            urlerror.URLError,
            subprocess.TimeoutExpired,
            RuntimeError,
        ) as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"AI translation failed for {rel_path}") from last_error


def translate_single_text(
    text: str,
    *,
    rel_path: Path,
    context_title: str,
    provider: str,
    model: str,
) -> str:
    context_lines = [f"File path: {rel_path.as_posix()}"]
    if context_title:
        context_lines.append(f"Page title: {context_title}")

    user_prompt = (
        "\n".join(context_lines)
        + "\nTranslate the following text into Simplified Chinese.\n"
        + "Preserve code, shell commands, file paths, file names, URLs, email addresses, and placeholders.\n"
        + "Return only the translated text, with no quotes and no extra commentary.\n\n"
        + text
    )

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            raw = chat_complete(
                system_prompt=TEXT_BLOCK_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                provider=provider,
                model=model,
            )
            return postprocess_translation(raw.strip())
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    # Final fallback for pathological single lines that repeatedly hang in the CLI.
    try:
        return postprocess_translation(google_translate_text(text).strip())
    except Exception as exc:
        raise RuntimeError(f"single-text translation failed for {rel_path}") from exc


def fill_cache_for_file(
    cache: dict[str, str],
    pending: list[str],
    *,
    cache_path: Path,
    rel_path: Path,
    context_title: str,
    provider: str,
    model: str,
) -> None:
    pending = [text for text in pending if text not in cache]
    if not pending:
        return

    for normalized, translated in NORMALIZED_MANUAL_TRANSLATIONS.items():
        if normalized in pending and normalized not in cache:
            cache[normalized] = translated

    pending = [text for text in pending if text not in cache]
    if not pending:
        save_cache(cache, cache_path)
        return

    batches = build_batches(pending)
    total = len(pending)
    done = 0

    for index, batch in enumerate(batches, start=1):
        try:
            translated_batch = translate_batch_with_ai(
                batch,
                rel_path=rel_path,
                context_title=context_title,
                provider=provider,
                model=model,
            )
            for src, dst in zip(batch, translated_batch):
                cache[src] = dst
        except Exception:
            for item in batch:
                translated = translate_batch_with_ai(
                    [item],
                    rel_path=rel_path,
                    context_title=context_title,
                    provider=provider,
                    model=model,
                )[0]
                cache[item] = translated

        done += len(batch)
        save_cache(cache, cache_path)
        print(
            f"translated {rel_path} batch {index}/{len(batches)}: {done}/{total}",
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


def build_txt_chunks(lines: list[str]) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_chars = 0

    for line in lines:
        line_cost = len(line) + 8
        if current and (
            len(current) >= MAX_TXT_CHUNK_LINES
            or current_chars + line_cost > MAX_TXT_CHUNK_CHARS
        ):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(line)
        current_chars += line_cost

    if current:
        chunks.append(current)
    return chunks


def translate_numbered_lines(
    chunk_lines: list[str],
    *,
    rel_path: Path,
    provider: str,
    model: str,
) -> list[str]:
    numbered_lines = [
        f"{index:04d}|{line}" for index, line in enumerate(chunk_lines, start=1)
    ]
    user_prompt = (
        f"File path: {rel_path.as_posix()}\n"
        "Translate the following numbered lines into Simplified Chinese.\n"
        "Rules:\n"
        "- Keep every line prefix like 0001| exactly unchanged.\n"
        "- Return the same number of lines in the same order.\n"
        "- Preserve indentation after the pipe.\n"
        "- Leave code, shell commands, URLs, email addresses, file paths, and file names unchanged.\n"
        "- Return only the numbered lines, with no extra commentary.\n\n"
        + "\n".join(numbered_lines)
    )
    raw = chat_complete(
        system_prompt=TEXT_BLOCK_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        provider=provider,
        model=model,
    )
    out_lines = [line.rstrip("\n") for line in raw.splitlines()]
    parsed = [line for line in out_lines if re.match(r"^\d{4}\|", line)]
    if len(parsed) != len(numbered_lines):
        raise ValueError(
            f"line count mismatch for {rel_path}: expected {len(numbered_lines)}, got {len(parsed)}"
        )

    translated: list[str] = []
    for index, line in enumerate(parsed, start=1):
        prefix = f"{index:04d}|"
        if not line.startswith(prefix):
            raise ValueError(f"prefix mismatch for {rel_path}: expected {prefix}")
        content = line[len(prefix):]
        leading = re.match(r"^\s*", content).group(0)
        trailing = re.search(r"\s*$", content).group(0)
        core = postprocess_translation(content.strip())
        translated.append(f"{leading}{core}{trailing}")
    return translated


def translate_txt_chunks(
    lines: list[str],
    *,
    rel_path: Path,
    provider: str,
    model: str,
) -> list[str]:
    chunks = build_txt_chunks(lines)
    translated_lines: list[str] = []

    for chunk_index, chunk in enumerate(chunks, start=1):
        try:
            translated_chunk = translate_numbered_lines(
                chunk,
                rel_path=rel_path,
                provider=provider,
                model=model,
            )
        except Exception:
            translated_chunk = []
            for line in chunk:
                if should_translate_line(line):
                    leading = re.match(r"^\s*", line).group(0)
                    trailing = re.search(r"\s*$", line).group(0)
                    translated_line = translate_single_text(
                        line.strip(),
                        rel_path=rel_path,
                        context_title="",
                        provider=provider,
                        model=model,
                    )
                    translated_chunk.append(f"{leading}{translated_line}{trailing}")
                else:
                    translated_chunk.append(line)
        translated_lines.extend(translated_chunk)
        print(
            f"translated {rel_path} chunk {chunk_index}/{len(chunks)}: {len(translated_lines)}/{len(lines)}",
            flush=True,
        )
        time.sleep(REQUEST_SLEEP)

    return translated_lines


def render_html(source_html: str, cache: dict[str, str]) -> str:
    soup = BeautifulSoup(source_html, "html.parser")
    if soup.html and not soup.html.get("lang"):
        soup.html["lang"] = "zh-CN"
    ensure_utf8_meta(soup)
    ensure_html_banner(soup)

    for tag in soup.find_all(True):
        for attr in TRANSLATABLE_ATTRS:
            value = tag.get(attr)
            if isinstance(value, str) and should_translate_text(value):
                tag[attr] = translate_text_preserving_padding(value, cache)

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

    return str(soup)


def render_txt(
    source_txt: str,
    *,
    rel_path: Path,
    provider: str,
    model: str,
) -> str:
    lines = source_txt.splitlines()
    translated_lines = translate_txt_chunks(
        lines,
        rel_path=rel_path,
        provider=provider,
        model=model,
    )
    out_lines = TXT_BANNER.splitlines() + [""]

    for line in translated_lines:
        out_lines.append(line)

    output = "\n".join(out_lines)
    if source_txt.endswith("\n"):
        output += "\n"
    return output


def process_file(
    path: Path,
    *,
    source_rev: str,
    cache: dict[str, str],
    cache_path: Path,
    provider: str,
    model: str,
) -> bool:
    rel_path = path.relative_to(ROOT)
    source_text = read_source_file_from_git(source_rev, rel_path)
    title = extract_html_title(source_text) if path.suffix.lower() == ".html" else ""

    if path.suffix.lower() == ".html":
        pending = collect_pending_from_html(source_text, cache)
        fill_cache_for_file(
            cache,
            pending,
            cache_path=cache_path,
            rel_path=rel_path,
            context_title=title,
            provider=provider,
            model=model,
        )
        rendered = render_html(source_text, cache)
    elif path.suffix.lower() == ".txt":
        rendered = render_txt(
            source_text,
            rel_path=rel_path,
            provider=provider,
            model=model,
        )
    else:
        return False

    current = path.read_text(encoding="utf-8", errors="ignore")
    if rendered != current:
        path.write_text(rendered, encoding="utf-8")
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default="official-pages",
        help="Root directory to translate in place",
    )
    parser.add_argument(
        "--cache-path",
        default=str(DEFAULT_CACHE_PATH),
        help="Path to the translation cache JSON file",
    )
    parser.add_argument(
        "--source-rev",
        default=DEFAULT_SOURCE_REV,
        help="Git revision containing the original English files",
    )
    parser.add_argument(
        "--provider",
        default=DEFAULT_PROVIDER,
        choices=["claude", "stepfun"],
        help="AI backend used for translation",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help="Optional provider-specific model name",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional relative file paths under --root to translate",
    )
    args = parser.parse_args()

    if args.provider == "stepfun" and not os.environ.get("STEPFUN_API_KEY"):
        print("STEPFUN_API_KEY is not set", file=sys.stderr)
        return 1

    root = (ROOT / args.root).resolve()
    if not root.exists():
        print(f"Root not found: {root}", file=sys.stderr)
        return 1

    cache_path = Path(args.cache_path)
    if not cache_path.is_absolute():
        cache_path = (ROOT / cache_path).resolve()

    cache = load_cache(cache_path)
    files = target_files(root, args.paths)
    changed_files = 0

    print(
        f"target files: {len(files)}; source_rev={args.source_rev}; provider={args.provider}; model={args.model or 'default'}",
        flush=True,
    )

    for index, path in enumerate(files, start=1):
        rel_path = path.relative_to(ROOT)
        changed = process_file(
            path,
            source_rev=args.source_rev,
            cache=cache,
            cache_path=cache_path,
            provider=args.provider,
            model=args.model,
        )
        if changed:
            changed_files += 1
        save_cache(cache, cache_path)
        print(f"applied {index}/{len(files)} {rel_path}", flush=True)

    print(
        f"done: changed_files={changed_files}, cache_size={len(cache)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
