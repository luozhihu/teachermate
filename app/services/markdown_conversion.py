from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional


def _build_import_document(title: str, source_name: str, source_type: str, body: str) -> str:
    clean_body = body.strip()
    return "\n".join(
        [
            f"# {title}",
            "",
            f"> Imported From: {source_name}",
            f"> Imported At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"> Source Type: {source_type}",
            "",
            clean_body or "(empty document)",
            "",
        ]
    )


def _decode_bytes(raw_bytes: bytes) -> str:
    for encoding in ["utf-8", "utf-8-sig", "gb18030", "latin-1"]:
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="ignore")


def convert_upload_to_markdown(filename: str, raw_bytes: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    title = Path(filename).stem or "Imported Document"

    if suffix in [".txt", ".md"]:
        return _build_import_document(title, filename, "uploaded-file", _decode_bytes(raw_bytes))

    if suffix not in [".pdf", ".docx"]:
        raise ValueError("Only txt, md, pdf, and docx are supported in this MVP.")

    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise RuntimeError("markitdown is not installed. Install project dependencies first.") from exc

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
        handle.write(raw_bytes)
        handle.flush()
        result = MarkItDown().convert(handle.name)
    return _build_import_document(title, filename, "uploaded-file", result.text_content)


def convert_pasted_text_to_markdown(text: str, title: Optional[str] = None) -> str:
    normalized = text.strip()
    if not normalized:
        raise ValueError("Pasted text is empty.")
    first_line = normalized.splitlines()[0].strip("# ").strip()
    chosen_title = title or first_line[:80] or "Pasted Note"
    return _build_import_document(chosen_title, "Pasted Text", "pasted-text", normalized)
