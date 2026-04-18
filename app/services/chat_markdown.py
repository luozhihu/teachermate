from __future__ import annotations

import html
import re
from typing import List


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UNORDERED_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*\d+\.\s+(.*)$")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*(.+?)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")


def _render_inline(text: str) -> str:
    rendered = html.escape(text, quote=False)
    rendered = _LINK_RE.sub(
        r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>',
        rendered,
    )
    rendered = _INLINE_CODE_RE.sub(r"<code>\1</code>", rendered)
    rendered = _BOLD_RE.sub(r"<strong>\1</strong>", rendered)
    rendered = _ITALIC_RE.sub(r"<em>\1</em>", rendered)
    return rendered


def render_chat_markdown(markdown: str) -> str:
    source = (markdown or "").replace("\r\n", "\n").strip()
    if not source:
        return "<p></p>"

    lines = source.split("\n")
    index = 0
    chunks: List[str] = []

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
            continue

        if stripped.startswith("```"):
            code_lines: List[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            chunks.append("<pre><code>{0}</code></pre>".format(html.escape("\n".join(code_lines))))
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            chunks.append("<h{0}>{1}</h{0}>".format(level, _render_inline(heading.group(2).strip())))
            index += 1
            continue

        if stripped.startswith(">"):
            quote_lines: List[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].strip())
                index += 1
            chunks.append("<blockquote><p>{0}</p></blockquote>".format("<br>".join(_render_inline(item) for item in quote_lines)))
            continue

        unordered = _UNORDERED_RE.match(line)
        if unordered:
            items: List[str] = []
            while index < len(lines):
                match = _UNORDERED_RE.match(lines[index])
                if not match:
                    break
                items.append("<li>{0}</li>".format(_render_inline(match.group(1).strip())))
                index += 1
            chunks.append("<ul>{0}</ul>".format("".join(items)))
            continue

        ordered = _ORDERED_RE.match(line)
        if ordered:
            items = []
            while index < len(lines):
                match = _ORDERED_RE.match(lines[index])
                if not match:
                    break
                items.append("<li>{0}</li>".format(_render_inline(match.group(1).strip())))
                index += 1
            chunks.append("<ol>{0}</ol>".format("".join(items)))
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index]
            candidate_stripped = candidate.strip()
            if not candidate_stripped:
                index += 1
                break
            if (
                candidate_stripped.startswith("```")
                or _HEADING_RE.match(candidate)
                or candidate_stripped.startswith(">")
                or _UNORDERED_RE.match(candidate)
                or _ORDERED_RE.match(candidate)
            ):
                break
            paragraph_lines.append(candidate)
            index += 1
        chunks.append("<p>{0}</p>".format("<br>".join(_render_inline(item.strip()) for item in paragraph_lines)))

    return "\n".join(chunks) if chunks else "<p></p>"
