from __future__ import annotations

import asyncio
import os
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.models import ImportRecord
from app.settings import AppSettings


class ClaudeAgentService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings

    def _import_sdk(self):
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed or the Python interpreter is too old. "
                "Use Python 3.11+ and install project dependencies."
            ) from exc
        return ClaudeAgentOptions, query

    def _base_options(
        self,
        allowed_tools: List[str],
        permission_mode: str,
        output_schema: Optional[Dict[str, Any]] = None,
        max_turns: int = 8,
        continue_conversation: bool = False,
        resume: Optional[str] = None,
        include_partial_messages: bool = False,
    ):
        ClaudeAgentOptions, _ = self._import_sdk()
        kwargs: Dict[str, Any] = {
            "cwd": str(self.settings.root_dir),
            "setting_sources": ["user", "project", "local"],
            "allowed_tools": allowed_tools,
            "permission_mode": permission_mode,
            "max_turns": max_turns,
        }
        if continue_conversation:
            kwargs["continue_conversation"] = True
        if resume:
            kwargs["resume"] = resume
        if include_partial_messages:
            kwargs["include_partial_messages"] = True
        if output_schema:
            kwargs["output_format"] = {"type": "json_schema", "schema": output_schema}
        return ClaudeAgentOptions(**kwargs)

    async def _run_query(
        self,
        prompt: str,
        options: Any,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
        _, query = self._import_sdk()
        text_fragments: List[str] = []
        structured_output: Optional[Dict[str, Any]] = None
        stderr_fragments: List[str] = []
        final_result: Optional[str] = None
        session_id: Optional[str] = None

        def on_stderr(text: str) -> None:
            stderr_fragments.append(text)

        options.stderr = on_stderr
        try:
            async for message in query(prompt=prompt, options=options):
                structured = getattr(message, "structured_output", None)
                if structured:
                    structured_output = structured
                result_text = getattr(message, "result", None)
                if isinstance(result_text, str) and result_text.strip():
                    final_result = result_text.strip()
                message_session_id = getattr(message, "session_id", None)
                if isinstance(message_session_id, str) and message_session_id.strip():
                    session_id = message_session_id.strip()
                chunks = self._collect_text(message, text_fragments)
                if on_progress:
                    for chunk in chunks:
                        on_progress(chunk)
        except Exception as exc:
            partial_text = final_result or "\n".join(fragment for fragment in text_fragments if fragment).strip()
            stderr_text = "".join(stderr_fragments).strip()
            detail_blocks = []
            if partial_text:
                detail_blocks.append("partial output:\n{0}".format(partial_text))
            if stderr_text:
                detail_blocks.append("stderr:\n{0}".format(stderr_text))
            if detail_blocks:
                raise RuntimeError(
                    "Claude SDK query failed: {0}\n\n{1}".format(exc, "\n\n".join(detail_blocks))
                ) from exc
            raise RuntimeError("Claude SDK query failed: {0}".format(exc)) from exc
        text_output = final_result or "\n".join(fragment for fragment in text_fragments if fragment).strip()
        if stderr_fragments:
            text_output = "{0}\n\n[stderr]\n{1}".format(text_output, "".join(stderr_fragments).strip()).strip()
        return text_output, structured_output, session_id

    def _collect_text(self, message: Any, fragments: List[str]) -> List[str]:
        chunks: List[str] = []
        if type(message).__name__ != "AssistantMessage":
            return chunks
        if hasattr(message, "content"):
            for block in getattr(message, "content", []):
                text = getattr(block, "text", None)
                if text:
                    fragments.append(text)
                    chunks.append(text)
        text = getattr(message, "text", None)
        if text:
            fragments.append(text)
            chunks.append(text)
        return chunks

    async def _run_claude_cli_print(
        self,
        prompt: str,
        allowed_tools: List[str],
        permission_mode: str,
        max_turns: int,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> str:
        cli_path = shutil.which("claude")
        if not cli_path:
            raise RuntimeError("Claude CLI was not found in PATH.")

        command = [
            cli_path,
            "-p",
            prompt,
            "--permission-mode",
            permission_mode,
            "--allowedTools",
            ",".join(allowed_tools),
            "--setting-sources=user,project,local",
            "--max-turns",
            str(max_turns),
        ]
        model = os.getenv("ANTHROPIC_DEFAULT_SONNET_MODEL") or os.getenv("ANTHROPIC_CUSTOM_MODEL_OPTION")
        if model:
            command.extend(["--model", model])

        env = os.environ.copy()
        if env.get("ANTHROPIC_AUTH_TOKEN") and not env.get("ANTHROPIC_API_KEY"):
            env["ANTHROPIC_API_KEY"] = env["ANTHROPIC_AUTH_TOKEN"]

        if on_progress:
            on_progress("启动 Claude 编译进程（max_turns={0}）".format(max_turns))

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self.settings.root_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout_chunks: List[str] = []
        stderr_chunks: List[str] = []

        async def read_stream(
            stream: Optional[asyncio.StreamReader],
            sink: List[str],
            label: str,
        ) -> None:
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="ignore")
                sink.append(text)
                if on_progress:
                    on_progress("[{0}] {1}".format(label, text.rstrip("\n")))

        await asyncio.gather(
            read_stream(process.stdout, stdout_chunks, "stdout"),
            read_stream(process.stderr, stderr_chunks, "stderr"),
        )
        return_code = await process.wait()
        stdout_text = "".join(stdout_chunks).strip()
        stderr_text = "".join(stderr_chunks).strip()
        if on_progress:
            on_progress("Claude 编译进程结束（exit_code={0}）".format(return_code))
        if return_code != 0:
            blocks = []
            if stderr_text:
                blocks.append("stderr:\n{0}".format(stderr_text))
            if stdout_text:
                blocks.append("stdout:\n{0}".format(stdout_text))
            raise RuntimeError(
                "Claude CLI compile failed with exit code {0}.{1}".format(
                    return_code,
                    "\n\n{0}".format("\n\n".join(blocks))
                    if blocks
                    else "\n\nNo stdout/stderr output captured.",
                )
            )
        return stdout_text

    @staticmethod
    def _is_max_turns_error(exc: Exception) -> bool:
        return "Reached max turns" in str(exc)

    def _compile_max_turns(self) -> int:
        raw_value = os.getenv("TEACHERMATE_COMPILE_MAX_TURNS", "24")
        try:
            return max(6, int(raw_value))
        except ValueError:
            return 24

    def _compile_retry_max_turns(self) -> int:
        raw_value = os.getenv("TEACHERMATE_COMPILE_RETRY_MAX_TURNS", "48")
        try:
            return max(self._compile_max_turns() + 6, int(raw_value))
        except ValueError:
            return 48

    async def _run_compile_with_retry(
        self,
        prompt: str,
        allowed_tools: List[str],
        permission_mode: str,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> str:
        try:
            return await self._run_claude_cli_print(
                prompt=prompt,
                allowed_tools=allowed_tools,
                permission_mode=permission_mode,
                max_turns=self._compile_max_turns(),
                on_progress=on_progress,
            )
        except Exception as first_error:
            if not self._is_max_turns_error(first_error):
                raise
        if on_progress:
            on_progress("首次编译命中 max turns，自动重试。")

        retry_prompt = """
{prompt}

Previous run failed only because max turns were exhausted.
Continue from current repository state.
Do not duplicate existing raw/wiki entries.
Only complete remaining ingest/update work, then output a short summary.
""".strip().format(prompt=prompt)
        try:
            return await self._run_claude_cli_print(
                prompt=retry_prompt,
                allowed_tools=allowed_tools,
                permission_mode=permission_mode,
                max_turns=self._compile_retry_max_turns(),
                on_progress=on_progress,
            )
        except Exception as second_error:
            if self._is_max_turns_error(second_error):
                raise RuntimeError(
                    "Claude compile reached max turns even after retry. "
                    "Increase TEACHERMATE_COMPILE_MAX_TURNS / TEACHERMATE_COMPILE_RETRY_MAX_TURNS."
                ) from second_error
            raise

    async def compile_import(
        self,
        record: ImportRecord,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        def snapshot(directory: Path) -> Dict[str, float]:
            return {
                str(path.relative_to(self.settings.root_dir)): path.stat().st_mtime
                for path in directory.rglob("*.md")
            }

        before_raw = snapshot(self.settings.raw_dir)
        before_wiki = snapshot(self.settings.wiki_dir)
        prompt = (
            "Use the karpathy-llm-wiki skill to ingest {path}. "
            "Read the file, place it into raw/<topic>/..., update wiki/<topic>/..., "
            "and summarize what changed."
        ).format(path=record.markdown_path)
        raw_text = await self._run_compile_with_retry(
            prompt=prompt,
            allowed_tools=["Skill", "Read", "Write", "Edit", "Glob", "Grep", "LS"],
            permission_mode="dontAsk",
            on_progress=on_progress,
        )
        after_raw = snapshot(self.settings.raw_dir)
        after_wiki = snapshot(self.settings.wiki_dir)

        changed_raw = sorted(
            path for path, mtime in after_raw.items() if path not in before_raw or before_raw[path] != mtime
        )
        changed_wiki = sorted(
            path for path, mtime in after_wiki.items() if path not in before_wiki or before_wiki[path] != mtime
        )

        topic = ""
        candidate_paths = changed_raw or changed_wiki
        if candidate_paths:
            parts = Path(candidate_paths[0]).parts
            if len(parts) >= 2:
                topic = parts[1]
        elif raw_text:
            match = re.search(r"(?:raw|wiki)/([A-Za-z0-9_-]+)/[A-Za-z0-9._-]+\.md", raw_text)
            if match:
                topic = match.group(1)

        if not changed_raw and not changed_wiki and not raw_text:
            raise RuntimeError("Claude did not return compile output.")

        summary = raw_text.strip() or "Compile completed with no file changes."
        if "Summary:" in summary:
            summary = summary.split("Summary:")[-1].strip("* \n")
        elif "\n\n" in summary:
            summary = summary.split("\n\n")[-1].strip()

        return {
            "topic": topic,
            "summary": summary,
            "raw_paths": changed_raw,
            "wiki_paths": changed_wiki,
        }

    async def answer_question(
        self,
        question: str,
        claude_session_id: Optional[str] = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        citation_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["title", "path"],
            "additionalProperties": False,
        }
        schema = {
            "type": "object",
            "properties": {
                "answer_markdown": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": citation_schema,
                },
                "deliverable": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["research-topic", "research-protocol"]},
                        "title": {"type": "string"},
                        "topic": {"type": "string"},
                        "markdown": {"type": "string"},
                    },
                    "required": ["type", "title", "topic", "markdown"],
                    "additionalProperties": False,
                },
            },
            "required": ["answer_markdown", "citations"],
            "additionalProperties": False,
        }

        prompt = """
Use the karpathy-llm-wiki skill to answer the user's question grounded in the existing wiki.

Rules:
- Prefer wiki knowledge over general model knowledge.
- Cite every factual section with wiki article references.
- If the wiki is empty or insufficient, say so plainly.
- Keep citations as project-root-relative paths like wiki/topic/article.md.
- If the user asks to generate a research topic proposal or research protocol, also return `deliverable`.
- `deliverable.markdown` must be complete researcher-ready content grounded in wiki + current conversation context.
- If user intent is normal QA, omit `deliverable`.
- Do not write or edit any file.
- Do not ask for permissions.
- Return only structured JSON that matches the schema.

Question:
{question}
""".strip().format(question=question)
        options = self._base_options(
            allowed_tools=["Skill", "Read", "Glob", "Grep", "LS"],
            permission_mode="dontAsk",
            output_schema=schema,
            continue_conversation=bool(claude_session_id),
            resume=claude_session_id,
            include_partial_messages=True,
        )
        raw_text, structured, next_session_id = await self._run_query(prompt, options, on_progress=on_progress)
        if not structured:
            raise RuntimeError("Claude did not return structured chat output.\n{0}".format(raw_text))
        if next_session_id:
            structured["claude_session_id"] = next_session_id
        elif claude_session_id:
            structured["claude_session_id"] = claude_session_id
        return structured

    async def generate_research_topic(
        self,
        payload: Dict[str, Any],
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        citation_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["title", "path"],
            "additionalProperties": False,
        }
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "topic": {"type": "string"},
                "markdown": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": citation_schema,
                },
            },
            "required": ["title", "topic", "markdown", "citations"],
            "additionalProperties": False,
        }
        objectives_block = ""
        if payload.get("research_objectives", "").strip():
            objectives_block = "\nResearch Objectives: {0}".format(payload["research_objectives"].strip())
        context_block = ""
        if payload.get("conversation_context", "").strip():
            context_block = "\nConversation context:\n{0}".format(payload["conversation_context"].strip())
        prompt = """
Create a research topic proposal document in Markdown.

Constraints:
- Base the topic proposal directly on the current wiki knowledge base.
- Read wiki files directly (do not rely on hidden tools) and cite the wiki sources you used.
- Include title, background, significance, literature review outline, and expected outcomes.
- Keep the document editable for a researcher.
- Keep all source references at the end under a "Sources" section.
- Do not write or edit any file.
- Do not ask for permissions.
- Return only the structured JSON that matches the output schema.

Research Field: {research_field}
Research Direction: {research_direction}
Novelty Level: {novelty_level}
Expected Pages: {expected_pages}
{objectives}{context}
""".strip().format(
            research_field=payload["research_field"],
            research_direction=payload["research_direction"],
            novelty_level=payload["novelty_level"],
            expected_pages=payload["expected_pages"],
            objectives=objectives_block,
            context=context_block,
        )
        options = self._base_options(
            allowed_tools=["Read", "Glob", "Grep", "LS"],
            permission_mode="dontAsk",
            output_schema=schema,
            continue_conversation=bool(payload.get("claude_session_id")),
            resume=payload.get("claude_session_id"),
            include_partial_messages=True,
        )
        raw_text, structured, _ = await self._run_query(prompt, options, on_progress=on_progress)
        if not structured:
            raise RuntimeError("Claude did not return structured research-topic output.\n{0}".format(raw_text))
        return structured

    async def generate_research_protocol(
        self,
        payload: Dict[str, Any],
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        citation_schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["title", "path"],
            "additionalProperties": False,
        }
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "topic": {"type": "string"},
                "markdown": {"type": "string"},
                "citations": {
                    "type": "array",
                    "items": citation_schema,
                },
            },
            "required": ["title", "topic", "markdown", "citations"],
            "additionalProperties": False,
        }
        context_block = ""
        if payload.get("conversation_context", "").strip():
            context_block = "\nConversation context:\n{0}".format(payload["conversation_context"].strip())
        prompt = """
Create a research protocol document in Markdown.

Constraints:
- Base the protocol directly on the current wiki knowledge base.
- Read wiki files directly (do not rely on hidden tools) and cite the wiki sources you used.
- Include study objectives, design methodology, sample size estimation, data collection plan, and timeline.
- Keep the protocol practical for a real researcher to edit.
- Keep all source references at the end under a "Sources" section.
- Do not write or edit any file.
- Do not ask for permissions.
- Return only the structured JSON that matches the output schema.

Research Field: {research_field}
Study Design: {study_design}
Timeline: {timeline}
Objectives: {objectives}
{context}
""".strip().format(
            research_field=payload["research_field"],
            study_design=payload["study_design"],
            timeline=payload["timeline"],
            objectives=payload["objectives"],
            context=context_block,
        )
        options = self._base_options(
            allowed_tools=["Read", "Glob", "Grep", "LS"],
            permission_mode="dontAsk",
            output_schema=schema,
            continue_conversation=bool(payload.get("claude_session_id")),
            resume=payload.get("claude_session_id"),
            include_partial_messages=True,
        )
        raw_text, structured, _ = await self._run_query(prompt, options, on_progress=on_progress)
        if not structured:
            raise RuntimeError("Claude did not return structured research-protocol output.\n{0}".format(raw_text))
        return structured
