from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.services.chat_markdown import render_chat_markdown
from app.services.claude_agent import ClaudeAgentService
from app.services.markdown_conversion import convert_pasted_text_to_markdown, convert_upload_to_markdown
from app.services.storage import LocalStore, utc_now_iso
from app.settings import get_settings

settings = get_settings()
store = LocalStore(settings)
agent_service = ClaudeAgentService(settings)

app = FastAPI(title="MediMate MVP")
app.mount("/static", StaticFiles(directory=str(settings.root_dir / "app" / "static")), name="static")
app.mount("/imports", StaticFiles(directory=str(settings.imports_dir)), name="imports")
app.mount("/raw", StaticFiles(directory=str(settings.raw_dir)), name="raw")
app.mount("/wiki", StaticFiles(directory=str(settings.wiki_dir)), name="wiki")
app.mount("/artifacts", StaticFiles(directory=str(settings.artifacts_dir)), name="artifacts")
templates = Jinja2Templates(directory=str(settings.root_dir / "app" / "templates"))

MAX_RUNTIME_LINES = 500
LOG_WINDOW_LINES = 36
_compile_runtime_lock = Lock()
_compile_runtime: Dict[str, Dict[str, Any]] = {}
MAX_CHAT_RUNTIME_CHARS = 20000
_chat_runtime_lock = Lock()
_chat_runtime: Dict[str, Dict[str, Any]] = {}
_generation_runtime_lock = Lock()
_generation_runtime: Dict[str, Dict[str, Any]] = {}
ALLOWED_CENTER_TABS = {"import", "topic", "protocol", "chat"}


def _initialize_compile_runtime(job_id: str, import_id: str, source_name: str) -> None:
    with _compile_runtime_lock:
        _compile_runtime[job_id] = {
            "job_id": job_id,
            "import_id": import_id,
            "source_name": source_name,
            "status": "running",
            "updated_at": utc_now_iso(),
            "lines": [
                "任务已创建：{0}".format(job_id),
                "开始处理导入：{0}".format(source_name),
            ],
        }


def _append_compile_runtime(job_id: str, text: str) -> None:
    normalized = text.strip()
    if not normalized:
        return
    with _compile_runtime_lock:
        if job_id not in _compile_runtime:
            _compile_runtime[job_id] = {
                "job_id": job_id,
                "import_id": "",
                "source_name": "",
                "status": "running",
                "updated_at": utc_now_iso(),
                "lines": [],
            }
        entry = _compile_runtime[job_id]
        lines = entry.get("lines", [])
        lines.append(normalized)
        if len(lines) > MAX_RUNTIME_LINES:
            lines = lines[-MAX_RUNTIME_LINES:]
        entry["lines"] = lines
        entry["updated_at"] = utc_now_iso()


def _finish_compile_runtime(job_id: str, status: str) -> None:
    with _compile_runtime_lock:
        entry = _compile_runtime.get(job_id)
        if not entry:
            return
        entry["status"] = status
        entry["updated_at"] = utc_now_iso()


def _snapshot_compile_runtime(job_id: str) -> Optional[Dict[str, Any]]:
    with _compile_runtime_lock:
        entry = _compile_runtime.get(job_id)
        if not entry:
            return None
        return {
            "job_id": entry.get("job_id", ""),
            "status": entry.get("status", ""),
            "updated_at": entry.get("updated_at", ""),
            "source_name": entry.get("source_name", ""),
            "lines": list(entry.get("lines", [])),
        }


def _initialize_chat_runtime(session_id: str, question: str) -> None:
    with _chat_runtime_lock:
        _chat_runtime[session_id] = {
            "task_id": uuid4().hex,
            "session_id": session_id,
            "question": question,
            "status": "running",
            "updated_at": utc_now_iso(),
            "answer_markdown": "",
            "error": "",
            "last_chunk": "",
        }


def _append_chat_runtime_text(session_id: str, chunk: str) -> None:
    normalized = chunk.strip()
    if not normalized:
        return
    with _chat_runtime_lock:
        entry = _chat_runtime.get(session_id)
        if not entry:
            return
        if normalized == entry.get("last_chunk"):
            return
        previous = entry.get("answer_markdown", "")
        if normalized == previous:
            return
        if normalized.startswith(previous):
            next_text = normalized
        elif previous.startswith(normalized):
            next_text = previous
        elif previous.endswith(normalized):
            next_text = previous
        else:
            overlap = 0
            window = min(len(previous), len(normalized), 2000)
            for idx in range(window, 0, -1):
                if previous.endswith(normalized[:idx]):
                    overlap = idx
                    break
            next_text = previous + normalized[overlap:]
        if len(next_text) > MAX_CHAT_RUNTIME_CHARS:
            next_text = next_text[-MAX_CHAT_RUNTIME_CHARS:]
        entry["answer_markdown"] = next_text
        entry["last_chunk"] = normalized
        entry["updated_at"] = utc_now_iso()


def _set_chat_runtime_answer(session_id: str, answer_markdown: str) -> None:
    with _chat_runtime_lock:
        entry = _chat_runtime.get(session_id)
        if not entry:
            return
        entry["answer_markdown"] = answer_markdown[-MAX_CHAT_RUNTIME_CHARS:]
        entry["updated_at"] = utc_now_iso()


def _finish_chat_runtime(session_id: str, status: str, error: str = "") -> None:
    with _chat_runtime_lock:
        entry = _chat_runtime.get(session_id)
        if not entry:
            return
        entry["status"] = status
        entry["error"] = error
        entry["updated_at"] = utc_now_iso()


def _snapshot_chat_runtime(session_id: str) -> Optional[Dict[str, Any]]:
    with _chat_runtime_lock:
        entry = _chat_runtime.get(session_id)
        if not entry:
            return None
        return {
            "task_id": entry.get("task_id", ""),
            "session_id": entry.get("session_id", ""),
            "question": entry.get("question", ""),
            "status": entry.get("status", ""),
            "updated_at": entry.get("updated_at", ""),
            "answer_markdown": entry.get("answer_markdown", ""),
            "error": entry.get("error", ""),
        }


def _generation_kind_meta(kind: str) -> Dict[str, str]:
    if kind == "research-protocol":
        return {
            "label": "研究方案",
            "kind_key": "research_protocol",
        }
    return {
        "label": "选题文档",
        "kind_key": "research_topic",
    }


def _initialize_generation_runtime(kind: str) -> None:
    meta = _generation_kind_meta(kind)
    with _generation_runtime_lock:
        _generation_runtime[kind] = {
            "task_id": uuid4().hex,
            "kind": kind,
            "kind_key": meta["kind_key"],
            "label": meta["label"],
            "status": "running",
            "updated_at": utc_now_iso(),
            "error": "",
            "lines": ["{0}生成任务已启动。".format(meta["label"])],
        }


def _append_generation_runtime(kind: str, text: str) -> None:
    normalized = text.strip()
    if not normalized:
        return
    with _generation_runtime_lock:
        if kind not in _generation_runtime:
            meta = _generation_kind_meta(kind)
            _generation_runtime[kind] = {
                "task_id": uuid4().hex,
                "kind": kind,
                "kind_key": meta["kind_key"],
                "label": meta["label"],
                "status": "running",
                "updated_at": utc_now_iso(),
                "error": "",
                "lines": [],
            }
        entry = _generation_runtime[kind]
        lines = entry.get("lines", [])
        lines.append(normalized)
        if len(lines) > MAX_RUNTIME_LINES:
            lines = lines[-MAX_RUNTIME_LINES:]
        entry["lines"] = lines
        entry["updated_at"] = utc_now_iso()


def _finish_generation_runtime(kind: str, status: str, error: str = "") -> None:
    with _generation_runtime_lock:
        entry = _generation_runtime.get(kind)
        if not entry:
            return
        entry["status"] = status
        entry["error"] = error
        entry["updated_at"] = utc_now_iso()


def _snapshot_generation_runtime(kind: str) -> Dict[str, Any]:
    meta = _generation_kind_meta(kind)
    with _generation_runtime_lock:
        entry = _generation_runtime.get(kind)
        if not entry:
            return {
                "kind": kind,
                "kind_key": meta["kind_key"],
                "label": meta["label"],
                "status": "idle",
                "updated_at": "",
                "error": "",
                "lines": [],
                "polling": False,
                "has_data": False,
            }
        return {
            "kind": kind,
            "kind_key": meta["kind_key"],
            "label": meta["label"],
            "status": entry.get("status", "idle"),
            "updated_at": entry.get("updated_at", ""),
            "error": entry.get("error", ""),
            "lines": list(entry.get("lines", []))[-LOG_WINDOW_LINES:],
            "polling": entry.get("status") == "running",
            "has_data": True,
        }


def resolve_chat_session_id(session_id: str = "") -> str:
    candidate = session_id.strip()
    if candidate and store.get_chat_session(candidate):
        store.set_active_chat_session(candidate)
        return candidate
    active_session = store.get_active_chat_session()
    store.set_active_chat_session(active_session.session_id)
    return active_session.session_id


def resolve_compile_job_id(job_id: str = "") -> str:
    candidate = job_id.strip()
    if candidate and store.get_job(candidate):
        return candidate
    for item in store.list_jobs():
        if item.status == "running":
            return item.job_id
    jobs = store.list_jobs()
    if jobs:
        return jobs[0].job_id
    return ""


def normalize_center_tab(center_tab: str = "") -> str:
    candidate = (center_tab or "").strip().lower()
    if candidate in ALLOWED_CENTER_TABS:
        return candidate
    return "chat"


def build_compile_runtime_context(job_id: str = "") -> Dict[str, Any]:
    active_job_id = resolve_compile_job_id(job_id)
    runtime = _snapshot_compile_runtime(active_job_id) if active_job_id else None
    job = store.get_job(active_job_id) if active_job_id else None
    status = runtime["status"] if runtime else (job.status if job else "")
    lines = runtime["lines"] if runtime else []
    return {
        "compile_runtime_job_id": active_job_id,
        "compile_runtime_status": status or "idle",
        "compile_runtime_lines": lines[-LOG_WINDOW_LINES:],
        "compile_runtime_polling": status == "running",
        "compile_runtime_source": runtime["source_name"] if runtime else "",
        "compile_runtime_has_data": bool(active_job_id and (lines or job)),
    }


def build_chat_runtime_context(session_id: str = "") -> Dict[str, Any]:
    target_session_id = resolve_chat_session_id(session_id)
    runtime = _snapshot_chat_runtime(target_session_id)
    runtime_status = runtime["status"] if runtime else "idle"
    runtime_visible = bool(runtime and runtime_status in {"running", "failed", "completed"})
    runtime_answer_markdown = runtime["answer_markdown"] if runtime else ""
    return {
        "chat_runtime_session_id": target_session_id,
        "chat_runtime_has_data": runtime_visible,
        "chat_runtime_status": runtime_status,
        "chat_runtime_polling": bool(runtime and runtime_status == "running"),
        "chat_runtime_question": runtime["question"] if runtime else "",
        "chat_runtime_answer_markdown": runtime_answer_markdown,
        "chat_runtime_answer_html": render_chat_markdown(runtime_answer_markdown),
        "chat_runtime_error": runtime["error"] if runtime else "",
    }


def build_generation_runtime_context() -> Dict[str, Any]:
    return {
        "generation_runtime": {
            "research_topic": _snapshot_generation_runtime("research-topic"),
            "research_protocol": _snapshot_generation_runtime("research-protocol"),
        }
    }


def _status_label_and_tone(import_status: str, job_status: str = "") -> Tuple[str, str]:
    key = (job_status or import_status or "").strip().lower()
    mapping = {
        "running": ("整理中", "running"),
        "compiling": ("整理中", "running"),
        "completed": ("已整理", "success"),
        "compiled": ("已整理", "success"),
        "failed": ("整理失败", "failed"),
        "pending": ("待整理", "pending"),
    }
    return mapping.get(key, ("状态未知", "pending"))


def _source_type_label(source_type: str) -> str:
    if source_type == "upload":
        return "上传文献"
    if source_type == "paste":
        return "粘贴文本"
    return source_type or "未知来源"


def _clip_text(value: str, limit: int = 220) -> str:
    text = (value or "").strip()
    if len(text) <= limit:
        return text
    return "{0}...".format(text[:limit])


def build_import_activity_rows(imports: List[Any], jobs: List[Any], limit: int = 3) -> List[Dict[str, Any]]:
    latest_job_by_import: Dict[str, Any] = {}
    job_by_id: Dict[str, Any] = {}
    for job in jobs:
        job_by_id[job.job_id] = job
        if job.import_id not in latest_job_by_import:
            latest_job_by_import[job.import_id] = job

    rows: List[Dict[str, Any]] = []
    for record in imports[:limit]:
        job = None
        if record.compiled_job_id:
            job = job_by_id.get(record.compiled_job_id)
        if job is None:
            job = latest_job_by_import.get(record.import_id)

        status_label, status_tone = _status_label_and_tone(
            import_status=record.status,
            job_status=job.status if job else "",
        )
        if job:
            if job.status == "completed":
                compile_result = job.summary or "编译完成，未返回摘要。"
            elif job.status == "failed":
                compile_result = job.error or "编译失败，未返回错误信息。"
            else:
                compile_result = "编译进行中，稍后将显示编译结果。"
        else:
            if record.status == "compiled":
                compile_result = record.note or "编译已完成。"
            elif record.status == "failed":
                compile_result = record.note or "编译失败。"
            elif record.status == "compiling":
                compile_result = "编译任务已创建，等待输出。"
            else:
                compile_result = "等待自动编译启动。"

        rows.append(
            {
                "source_name": record.source_name,
                "source_type_label": _source_type_label(record.source_type),
                "status_label": status_label,
                "status_tone": status_tone,
                "compile_result": _clip_text(compile_result),
                "markdown_path": record.markdown_path,
                "created_at": record.created_at.replace("T", " ").replace("Z", ""),
            }
        )
    return rows


def build_workspace_context(
    error: str = "",
    success: str = "",
    session_id: str = "",
    compile_job_id: str = "",
    center_tab: str = "chat",
) -> Dict[str, Any]:
    imports = store.list_imports()
    jobs = store.list_jobs()
    active_session_id = resolve_chat_session_id(session_id)
    turns = store.list_chat_turns(session_id=active_session_id)
    artifacts = store.list_artifacts()
    latest_turn = turns[-1] if turns else None
    sessions = store.list_chat_sessions()
    chat_turns: List[Dict[str, Any]] = []
    for turn in turns[-12:]:
        chat_turns.append(
            {
                "turn_id": turn.turn_id,
                "created_at": turn.created_at,
                "question": turn.question,
                "answer_markdown": turn.answer_markdown,
                "answer_html": render_chat_markdown(turn.answer_markdown),
                "citations": turn.citations,
            }
        )
    context = {
        "imports": imports,
        "jobs": jobs,
        "chat_sessions": sessions,
        "active_session_id": active_session_id,
        "active_session": store.get_chat_session(active_session_id),
        "chat_turns": chat_turns,
        "latest_turn": latest_turn,
        "artifacts": artifacts[:8],
        "import_activity_rows": build_import_activity_rows(imports=imports, jobs=jobs, limit=3),
        "active_center_tab": normalize_center_tab(center_tab),
        "error": error,
        "success": success,
    }
    context.update(build_compile_runtime_context(job_id=compile_job_id))

    # Clean up completed chat runtime entry before building context,
    # so the workspace render shows the saved turn directly (not the runtime overlay).
    with _chat_runtime_lock:
        entry = _chat_runtime.get(active_session_id)
        if entry and entry.get("status") == "completed":
            del _chat_runtime[active_session_id]

    context.update(build_chat_runtime_context(session_id=active_session_id))
    context.update(build_generation_runtime_context())
    return context


def infer_topic_from_citations(citations: List[Dict[str, Any]]) -> str:
    for item in citations:
        path = item.get("path", "")
        parts = Path(path).parts
        if len(parts) >= 3 and parts[0] == "wiki":
            return parts[1]
    return "general"


def render_workspace(
    request: Request,
    error: str = "",
    success: str = "",
    session_id: str = "",
    compile_job_id: str = "",
    center_tab: str = "chat",
) -> HTMLResponse:
    context = build_workspace_context(
        error=error,
        success=success,
        session_id=session_id,
        compile_job_id=compile_job_id,
        center_tab=center_tab,
    )
    context["request"] = request
    return templates.TemplateResponse(name="partials/workspace.html", request=request, context=context)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    context = {"request": request}
    return templates.TemplateResponse(name="index.html", request=request, context=context)


@app.get("/workspace", response_class=HTMLResponse)
async def workspace(request: Request, center_tab: str = "chat") -> HTMLResponse:
    context = build_workspace_context(center_tab=center_tab)
    context["request"] = request
    if request.headers.get("HX-Request", "").lower() == "true":
        return templates.TemplateResponse(name="partials/workspace.html", request=request, context=context)
    return templates.TemplateResponse(name="workspace_page.html", request=request, context=context)


@app.get("/compile/runtime", response_class=HTMLResponse)
async def compile_runtime(request: Request, job_id: str = "") -> HTMLResponse:
    context = build_compile_runtime_context(job_id=job_id)
    context["request"] = request
    return templates.TemplateResponse(name="partials/compile_runtime.html", request=request, context=context)


@app.get("/chat/runtime", response_class=HTMLResponse)
async def chat_runtime(request: Request, session_id: str = "", center_tab: str = "chat") -> HTMLResponse:
    context = build_chat_runtime_context(session_id=session_id)
    context["request"] = request
    context["active_center_tab"] = normalize_center_tab(center_tab)
    return templates.TemplateResponse(name="partials/chat_runtime.html", request=request, context=context)


@app.get("/generate/runtime", response_class=HTMLResponse)
async def generate_runtime(request: Request, kind: str = "research-topic") -> HTMLResponse:
    target_kind = "research-protocol" if kind == "research-protocol" else "research-topic"
    context = {
        "request": request,
        "runtime": _snapshot_generation_runtime(target_kind),
    }
    return templates.TemplateResponse(name="partials/generation_runtime.html", request=request, context=context)


async def _run_compile_job(import_id: str, job_id: str) -> None:
    record = store.get_import(import_id)
    if not record:
        _append_compile_runtime(job_id, "导入记录不存在，无法编译。")
        _finish_compile_runtime(job_id, "failed")
        job = store.get_job(job_id)
        if job:
            job.status = "failed"
            job.error = "Import record not found."
            job.finished_at = utc_now_iso()
            store.update_job(job)
        return

    def on_progress(line: str) -> None:
        _append_compile_runtime(job_id, line)

    _append_compile_runtime(job_id, "开始执行 karpathy-llm-wiki 编译。")
    try:
        try:
            result = await agent_service.compile_import(record, on_progress=on_progress)
        except TypeError:
            result = await agent_service.compile_import(record)
        job = store.get_job(job_id)
        if job:
            job.status = "completed"
            job.topic = result.get("topic", "")
            job.summary = result.get("summary", "")
            job.raw_paths = result.get("raw_paths", [])
            job.wiki_paths = result.get("wiki_paths", [])
            job.finished_at = utc_now_iso()
            store.update_job(job)

        record = store.get_import(import_id)
        if record:
            record.status = "compiled"
            record.compiled_job_id = job_id
            record.note = "Compiled into topic: {0}".format(result.get("topic", ""))
            store.update_import(record)
        _append_compile_runtime(job_id, "编译完成。")
        _finish_compile_runtime(job_id, "completed")
    except Exception as exc:
        job = store.get_job(job_id)
        if job:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = utc_now_iso()
            store.update_job(job)

        record = store.get_import(import_id)
        if record:
            record.status = "failed"
            record.compiled_job_id = job_id
            record.note = "Compile failed: {0}".format(exc)
            store.update_import(record)
        _append_compile_runtime(job_id, "编译失败：{0}".format(exc))
        _finish_compile_runtime(job_id, "failed")


async def start_compile_import_record(import_id: str) -> Tuple[bool, str, str]:
    record = store.get_import(import_id)
    if not record:
        return False, "未找到要编译的导入记录。", ""
    if record.status == "compiled":
        return True, "该导入资料已经编译过。", record.compiled_job_id or ""
    if record.status == "compiling" and record.compiled_job_id:
        return True, "该导入资料正在编译。", record.compiled_job_id

    job = store.create_job(import_id)
    record.status = "compiling"
    record.compiled_job_id = job.job_id
    record.note = "Compiling..."
    store.update_import(record)

    _initialize_compile_runtime(job.job_id, import_id, record.source_name)
    asyncio.create_task(_run_compile_job(import_id=import_id, job_id=job.job_id))
    return True, "编译任务已启动，正在执行中。", job.job_id


async def _run_chat_task(session_id: str, question: str, claude_session_id: Optional[str]) -> None:
    def on_progress(chunk: str) -> None:
        _append_chat_runtime_text(session_id, chunk)

    try:
        try:
            result = await agent_service.answer_question(
                question=question,
                claude_session_id=claude_session_id,
                on_progress=on_progress,
            )
        except TypeError:
            result = await agent_service.answer_question(
                question=question,
                claude_session_id=claude_session_id,
            )

        answer_markdown = result["answer_markdown"]
        citations = result["citations"]
        deliverable = result.get("deliverable")
        turn = store.save_chat_turn(
            question=question,
            answer_markdown=answer_markdown,
            citations=citations,
            session_id=session_id,
        )
        if isinstance(deliverable, dict):
            deliverable_type = deliverable.get("type")
            if deliverable_type in {"research-topic", "research-protocol"}:
                store.save_artifact(
                    artifact_type=deliverable_type,
                    title=deliverable.get("title", "{0} draft".format(deliverable_type)),
                    topic=deliverable.get("topic", infer_topic_from_citations(citations)),
                    markdown=deliverable.get("markdown", ""),
                    source_turn_id=turn.turn_id,
                    citations=citations,
                )

        next_claude_session_id = result.get("claude_session_id")
        if next_claude_session_id:
            store.set_chat_session_claude_id(session_id, next_claude_session_id)
        _set_chat_runtime_answer(session_id, answer_markdown)
        _finish_chat_runtime(session_id, "completed")
    except Exception as exc:
        _finish_chat_runtime(session_id, "failed", error=str(exc))


def _chat_running(session_id: str) -> bool:
    runtime = _snapshot_chat_runtime(session_id)
    return bool(runtime and runtime.get("status") == "running")


def _generation_running(kind: str) -> bool:
    runtime = _snapshot_generation_runtime(kind)
    return runtime.get("status") == "running"


async def _run_generate_topic_task(active_session_id: str, payload: Dict[str, Any]) -> None:
    del active_session_id

    def on_progress(text: str) -> None:
        _append_generation_runtime("research-topic", text)

    _append_generation_runtime("research-topic", "开始读取知识库并生成选题文档。")
    try:
        result = await agent_service.generate_research_topic(payload, on_progress=on_progress)
        citations = result.get("citations", [])
        artifact = store.save_artifact(
            artifact_type="research-topic",
            title=result["title"],
            topic=result["topic"] if result["topic"] not in ["", "general", "research-topic", "research-protocol"] else infer_topic_from_citations(citations),
            markdown=result["markdown"],
            source_turn_id="direct-generation",
            citations=citations,
        )
        _append_generation_runtime("research-topic", "已生成选题文档：{0}".format(artifact.markdown_path))
        _finish_generation_runtime("research-topic", "completed")
    except Exception as exc:
        _append_generation_runtime("research-topic", "生成失败：{0}".format(exc))
        _finish_generation_runtime("research-topic", "failed", error=str(exc))


async def _run_generate_protocol_task(active_session_id: str, payload: Dict[str, Any]) -> None:
    del active_session_id

    def on_progress(text: str) -> None:
        _append_generation_runtime("research-protocol", text)

    _append_generation_runtime("research-protocol", "开始读取知识库并生成研究方案。")
    try:
        result = await agent_service.generate_research_protocol(payload, on_progress=on_progress)
        citations = result.get("citations", [])
        artifact = store.save_artifact(
            artifact_type="research-protocol",
            title=result["title"],
            topic=result["topic"] if result["topic"] not in ["", "general", "research-topic", "research-protocol"] else infer_topic_from_citations(citations),
            markdown=result["markdown"],
            source_turn_id="direct-generation",
            citations=citations,
        )
        _append_generation_runtime("research-protocol", "已生成研究方案：{0}".format(artifact.markdown_path))
        _finish_generation_runtime("research-protocol", "completed")
    except Exception as exc:
        _append_generation_runtime("research-protocol", "生成失败：{0}".format(exc))
        _finish_generation_runtime("research-protocol", "failed", error=str(exc))


@app.post("/imports", response_class=HTMLResponse)
async def create_import(
    request: Request,
    upload: UploadFile = File(None),
    pasted_text: str = Form(""),
    center_tab: str = Form("chat"),
) -> HTMLResponse:
    active_tab = normalize_center_tab(center_tab)
    try:
        if upload and upload.filename:
            raw_bytes = await upload.read()
            markdown = convert_upload_to_markdown(upload.filename, raw_bytes)
            record = store.save_import(markdown, upload.filename, "upload")
            ok, message, job_id = await start_compile_import_record(record.import_id)
            if ok:
                return render_workspace(
                    request,
                    success="资料已导入，{0}".format(message),
                    compile_job_id=job_id,
                    center_tab=active_tab,
                )
            return render_workspace(request, error=message, center_tab=active_tab)
        if pasted_text.strip():
            markdown = convert_pasted_text_to_markdown(pasted_text)
            record = store.save_import(markdown, "pasted-text.md", "paste")
            ok, message, job_id = await start_compile_import_record(record.import_id)
            if ok:
                return render_workspace(
                    request,
                    success="粘贴文本已导入，{0}".format(message),
                    compile_job_id=job_id,
                    center_tab=active_tab,
                )
            return render_workspace(request, error=message, center_tab=active_tab)
        return render_workspace(request, error="请上传文件或粘贴文本。", center_tab=active_tab)
    except Exception as exc:
        return render_workspace(request, error=str(exc), center_tab=active_tab)


@app.post("/compile", response_class=HTMLResponse)
async def compile_import(
    request: Request,
    import_id: str = Form(...),
    center_tab: str = Form("chat"),
) -> HTMLResponse:
    active_tab = normalize_center_tab(center_tab)
    ok, message, job_id = await start_compile_import_record(import_id)
    if ok:
        return render_workspace(request, success=message, compile_job_id=job_id, center_tab=active_tab)
    return render_workspace(request, error=message, center_tab=active_tab)


@app.post("/chat/sessions/select", response_class=HTMLResponse)
async def select_chat_session(
    request: Request,
    session_id: str = Form(...),
    center_tab: str = Form("chat"),
) -> HTMLResponse:
    active_tab = normalize_center_tab(center_tab)
    resolved = session_id.strip()
    if not resolved:
        return render_workspace(request, error="会话ID不能为空。", center_tab=active_tab)
    if not store.set_active_chat_session(resolved):
        return render_workspace(request, error="未找到指定会话。", center_tab=active_tab)
    return render_workspace(request, success="已切换到指定会话。", session_id=resolved, center_tab=active_tab)


@app.post("/chat/sessions/create", response_class=HTMLResponse)
async def create_chat_session(
    request: Request,
    title: str = Form(""),
    center_tab: str = Form("chat"),
) -> HTMLResponse:
    active_tab = normalize_center_tab(center_tab)
    session = store.create_chat_session(title)
    store.set_active_chat_session(session.session_id)
    return render_workspace(
        request,
        success="已创建新会话。",
        session_id=session.session_id,
        center_tab=active_tab,
    )


@app.post("/chat", response_class=HTMLResponse)
async def chat(
    request: Request,
    question: str = Form(...),
    session_id: str = Form(""),
    center_tab: str = Form("chat"),
) -> HTMLResponse:
    active_tab = normalize_center_tab(center_tab)
    if not question.strip():
        return render_workspace(request, error="问题不能为空。", session_id=session_id, center_tab=active_tab)
    try:
        active_session_id = resolve_chat_session_id(session_id)
        if _chat_running(active_session_id):
            return render_workspace(
                request,
                error="当前会话仍在生成回答，请等待完成后再提问。",
                session_id=active_session_id,
                center_tab=active_tab,
            )
        active_session = store.get_chat_session(active_session_id)
        _initialize_chat_runtime(active_session_id, question.strip())
        asyncio.create_task(
            _run_chat_task(
                session_id=active_session_id,
                question=question.strip(),
                claude_session_id=active_session.claude_session_id if active_session else None,
            )
        )
        return render_workspace(
            request,
            success="问答任务已启动，正在流式生成。",
            session_id=active_session_id,
            center_tab=active_tab,
        )
    except Exception as exc:
        return render_workspace(
            request,
            error="问答失败：{0}".format(exc),
            session_id=session_id,
            center_tab=active_tab,
        )


@app.post("/generate/research-topic", response_class=HTMLResponse)
async def generate_research_topic(
    request: Request,
    research_field: str = Form(...),
    research_direction: str = Form(...),
    novelty_level: str = Form(...),
    expected_pages: str = Form(...),
    research_objectives: str = Form(""),
    session_id: str = Form(""),
    center_tab: str = Form("topic"),
) -> HTMLResponse:
    active_tab = normalize_center_tab(center_tab)
    active_session_id = resolve_chat_session_id(session_id)
    if _generation_running("research-topic"):
        return render_workspace(
            request,
            error="选题文档正在生成中，请稍后。",
            session_id=active_session_id,
            center_tab=active_tab,
        )
    active_session = store.get_chat_session(active_session_id)
    payload = {
        "research_field": research_field,
        "research_direction": research_direction,
        "novelty_level": novelty_level,
        "expected_pages": expected_pages,
        "research_objectives": research_objectives,
        "claude_session_id": active_session.claude_session_id if active_session else None,
    }
    _initialize_generation_runtime("research-topic")
    asyncio.create_task(_run_generate_topic_task(active_session_id=active_session_id, payload=payload))
    return render_workspace(
        request,
        success="选题文档生成任务已启动。",
        session_id=active_session_id,
        center_tab=active_tab,
    )


@app.post("/generate/research-protocol", response_class=HTMLResponse)
async def generate_research_protocol(
    request: Request,
    research_field: str = Form(...),
    study_design: str = Form(...),
    timeline: str = Form(...),
    objectives: str = Form(...),
    session_id: str = Form(""),
    center_tab: str = Form("protocol"),
) -> HTMLResponse:
    active_tab = normalize_center_tab(center_tab)
    active_session_id = resolve_chat_session_id(session_id)
    if _generation_running("research-protocol"):
        return render_workspace(
            request,
            error="研究方案正在生成中，请稍后。",
            session_id=active_session_id,
            center_tab=active_tab,
        )
    active_session = store.get_chat_session(active_session_id)
    payload = {
        "research_field": research_field,
        "study_design": study_design,
        "timeline": timeline,
        "objectives": objectives,
        "claude_session_id": active_session.claude_session_id if active_session else None,
    }
    _initialize_generation_runtime("research-protocol")
    asyncio.create_task(_run_generate_protocol_task(active_session_id=active_session_id, payload=payload))
    return render_workspace(
        request,
        success="研究方案生成任务已启动。",
        session_id=active_session_id,
        center_tab=active_tab,
    )
