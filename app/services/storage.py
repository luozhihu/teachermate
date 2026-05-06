from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.models import ArtifactRecord, ChatSession, ChatTurn, ImportRecord, JobRecord
from app.settings import AppSettings


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str, fallback: str = "document") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug[:80] or fallback


class LocalStore:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.ensure_dirs()

    def ensure_dirs(self) -> None:
        for path in [
            self.settings.imports_dir,
            self.settings.raw_dir,
            self.settings.wiki_dir,
            self.settings.artifacts_dir,
            self.settings.state_imports_dir,
            self.settings.state_jobs_dir,
            self.settings.state_chats_dir,
            self.settings.state_artifacts_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)
        self._initialize_chat_sessions()

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    def _chat_sessions_index_path(self) -> Path:
        return self.settings.state_chats_dir / "sessions.json"

    def _default_chat_session(self) -> ChatSession:
        now = utc_now_iso()
        return ChatSession(
            session_id="default",
            title="默认会话",
            created_at=now,
            updated_at=now,
        )

    def _initialize_chat_sessions(self) -> None:
        index_path = self._chat_sessions_index_path()
        if index_path.exists():
            return
        default_session = self._default_chat_session()
        payload = {
            "active_session_id": default_session.session_id,
            "sessions": [default_session.to_dict()],
        }
        self._write_json(index_path, payload)

    def _load_chat_index(self) -> Dict[str, Any]:
        self._initialize_chat_sessions()
        path = self._chat_sessions_index_path()
        raw_data = self._read_json(path)
        sessions: List[ChatSession] = []
        for item in raw_data.get("sessions", []):
            try:
                sessions.append(ChatSession(**item))
            except TypeError:
                continue
        if not sessions:
            sessions = [self._default_chat_session()]
        by_id = {item.session_id: item for item in sessions}
        active_session_id = raw_data.get("active_session_id")
        if active_session_id not in by_id:
            active_session_id = sessions[0].session_id
        normalized = {
            "active_session_id": active_session_id,
            "sessions": [item.to_dict() for item in sessions],
        }
        self._write_json(path, normalized)
        return normalized

    def _write_chat_index(self, payload: Dict[str, Any]) -> None:
        self._write_json(self._chat_sessions_index_path(), payload)

    def list_chat_sessions(self) -> List[ChatSession]:
        data = self._load_chat_index()
        sessions = [ChatSession(**item) for item in data.get("sessions", [])]
        return sorted(sessions, key=lambda item: item.updated_at, reverse=True)

    def get_chat_session(self, session_id: str) -> Optional[ChatSession]:
        data = self._load_chat_index()
        for item in data.get("sessions", []):
            if item.get("session_id") == session_id:
                return ChatSession(**item)
        return None

    def create_chat_session(self, title: str = "") -> ChatSession:
        now = utc_now_iso()
        session = ChatSession(
            session_id=uuid4().hex,
            title=title.strip() or "新会话",
            created_at=now,
            updated_at=now,
        )
        data = self._load_chat_index()
        sessions = data.get("sessions", [])
        sessions.append(session.to_dict())
        payload = {
            "active_session_id": session.session_id,
            "sessions": sessions,
        }
        self._write_chat_index(payload)
        self._write_json(self.settings.state_chats_dir / f"{session.session_id}.json", {"turns": []})
        return session

    def update_chat_session(self, record: ChatSession) -> None:
        data = self._load_chat_index()
        sessions = data.get("sessions", [])
        updated = False
        for idx, item in enumerate(sessions):
            if item.get("session_id") == record.session_id:
                sessions[idx] = record.to_dict()
                updated = True
                break
        if not updated:
            sessions.append(record.to_dict())
        if data.get("active_session_id") not in {item.get("session_id") for item in sessions}:
            data["active_session_id"] = record.session_id
        payload = {
            "active_session_id": data.get("active_session_id", record.session_id),
            "sessions": sessions,
        }
        self._write_chat_index(payload)

    def set_active_chat_session(self, session_id: str) -> bool:
        data = self._load_chat_index()
        sessions = data.get("sessions", [])
        if session_id not in {item.get("session_id") for item in sessions}:
            return False
        data["active_session_id"] = session_id
        self._write_chat_index(data)
        return True

    def get_active_chat_session_id(self) -> str:
        data = self._load_chat_index()
        return data.get("active_session_id", "default")

    def get_active_chat_session(self) -> ChatSession:
        active_id = self.get_active_chat_session_id()
        session = self.get_chat_session(active_id)
        if session:
            return session
        default_session = self._default_chat_session()
        self.update_chat_session(default_session)
        self.set_active_chat_session(default_session.session_id)
        return default_session

    def set_chat_session_claude_id(self, session_id: str, claude_session_id: str) -> None:
        session = self.get_chat_session(session_id)
        if not session:
            return
        session.claude_session_id = claude_session_id
        session.updated_at = utc_now_iso()
        self.update_chat_session(session)

    def _session_turns_path(self, session_id: str) -> Path:
        return self.settings.state_chats_dir / f"{session_id}.json"

    def _resolve_session_id(self, session_id: Optional[str]) -> str:
        if session_id and self.get_chat_session(session_id):
            return session_id
        return self.get_active_chat_session_id()

    def save_import(self, markdown: str, source_name: str, source_type: str) -> ImportRecord:
        import_id = uuid4().hex
        filename = "{timestamp}-{slug}.md".format(
            timestamp=datetime.now().strftime("%Y%m%d-%H%M%S"),
            slug=slugify(Path(source_name).stem or source_name, fallback="import"),
        )
        markdown_path = self.settings.imports_dir / filename
        markdown_path.write_text(markdown, encoding="utf-8")
        record = ImportRecord(
            import_id=import_id,
            source_name=source_name,
            source_type=source_type,
            markdown_path=str(markdown_path.relative_to(self.settings.root_dir)),
            created_at=utc_now_iso(),
        )
        self._write_json(self.settings.state_imports_dir / f"{import_id}.json", record.to_dict())
        return record

    def list_imports(self) -> List[ImportRecord]:
        records = []
        for path in sorted(self.settings.state_imports_dir.glob("*.json"), reverse=True):
            records.append(ImportRecord(**self._read_json(path)))
        return sorted(records, key=lambda item: item.created_at, reverse=True)

    def get_import(self, import_id: str) -> Optional[ImportRecord]:
        path = self.settings.state_imports_dir / f"{import_id}.json"
        if not path.exists():
            return None
        return ImportRecord(**self._read_json(path))

    def update_import(self, record: ImportRecord) -> None:
        self._write_json(self.settings.state_imports_dir / f"{record.import_id}.json", record.to_dict())

    def create_job(self, import_id: str) -> JobRecord:
        record = JobRecord(
            job_id=uuid4().hex,
            import_id=import_id,
            status="running",
            started_at=utc_now_iso(),
        )
        self._write_json(self.settings.state_jobs_dir / f"{record.job_id}.json", record.to_dict())
        return record

    def update_job(self, record: JobRecord) -> None:
        self._write_json(self.settings.state_jobs_dir / f"{record.job_id}.json", record.to_dict())

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        path = self.settings.state_jobs_dir / f"{job_id}.json"
        if not path.exists():
            return None
        return JobRecord(**self._read_json(path))

    def list_jobs(self) -> List[JobRecord]:
        records = []
        for path in sorted(self.settings.state_jobs_dir.glob("*.json"), reverse=True):
            records.append(JobRecord(**self._read_json(path)))
        return sorted(records, key=lambda item: item.started_at, reverse=True)

    def save_chat_turn(
        self,
        question: str,
        answer_markdown: str,
        citations: List[Dict[str, Any]],
        session_id: Optional[str] = None,
    ) -> ChatTurn:
        target_session_id = self._resolve_session_id(session_id)
        if not self.get_chat_session(target_session_id):
            fallback = self.create_chat_session("新会话")
            target_session_id = fallback.session_id
        turn = ChatTurn(
            turn_id=uuid4().hex,
            created_at=utc_now_iso(),
            question=question,
            answer_markdown=answer_markdown,
            citations=citations,
        )
        path = self._session_turns_path(target_session_id)
        turns = self.list_chat_turns(target_session_id)
        turns.append(turn)
        self._write_json(path, {"turns": [item.to_dict() for item in turns]})
        session = self.get_chat_session(target_session_id)
        if session:
            session.updated_at = turn.created_at
            self.update_chat_session(session)
        return turn

    def list_chat_turns(self, session_id: Optional[str] = None) -> List[ChatTurn]:
        target_session_id = self._resolve_session_id(session_id)
        path = self._session_turns_path(target_session_id)
        if not path.exists():
            return []
        data = self._read_json(path)
        return [ChatTurn(**item) for item in data.get("turns", [])]

    def latest_chat_turn(self, session_id: Optional[str] = None) -> Optional[ChatTurn]:
        turns = self.list_chat_turns(session_id=session_id)
        return turns[-1] if turns else None

    def save_artifact(
        self,
        artifact_type: str,
        title: str,
        topic: str,
        markdown: str,
        source_turn_id: str,
        citations: List[Dict[str, Any]],
    ) -> ArtifactRecord:
        artifact_id = uuid4().hex
        inferred_topic = ""
        for item in citations:
            path = item.get("path", "")
            parts = Path(path).parts
            if len(parts) >= 3 and parts[0] == "wiki":
                inferred_topic = parts[1]
                break
        raw_topic = topic if topic not in ["", "general", "research-topic", "research-protocol"] else inferred_topic or "general"
        topic_slug = slugify(raw_topic, fallback="general")
        target_dir = self.settings.artifacts_dir / topic_slug
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = "{kind}-{stamp}-{slug}.md".format(
            kind=artifact_type,
            stamp=datetime.now().strftime("%Y%m%d-%H%M%S"),
            slug=slugify(title, fallback=artifact_type),
        )
        markdown_path = target_dir / filename
        markdown_path.write_text(markdown, encoding="utf-8")
        record = ArtifactRecord(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            title=title,
            topic=topic_slug,
            markdown_path=str(markdown_path.relative_to(self.settings.root_dir)),
            created_at=utc_now_iso(),
            source_turn_id=source_turn_id,
            citations=citations,
        )
        self._write_json(self.settings.state_artifacts_dir / f"{artifact_id}.json", record.to_dict())
        return record

    def list_artifacts(self) -> List[ArtifactRecord]:
        records = []
        for path in sorted(self.settings.state_artifacts_dir.glob("*.json"), reverse=True):
            records.append(ArtifactRecord(**self._read_json(path)))
        return sorted(records, key=lambda item: item.created_at, reverse=True)
