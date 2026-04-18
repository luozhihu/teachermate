from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ImportRecord:
    import_id: str
    source_name: str
    source_type: str
    markdown_path: str
    created_at: str
    status: str = "pending"
    compiled_job_id: Optional[str] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class JobRecord:
    job_id: str
    import_id: str
    status: str
    started_at: str
    finished_at: Optional[str] = None
    topic: str = ""
    summary: str = ""
    raw_paths: List[str] = field(default_factory=list)
    wiki_paths: List[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Citation:
    title: str
    path: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChatTurn:
    turn_id: str
    created_at: str
    question: str
    answer_markdown: str
    citations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChatSession:
    session_id: str
    title: str
    created_at: str
    updated_at: str
    claude_session_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ArtifactRecord:
    artifact_id: str
    artifact_type: str
    title: str
    topic: str
    markdown_path: str
    created_at: str
    source_turn_id: str
    citations: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
