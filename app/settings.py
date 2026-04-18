from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    root_dir: Path
    imports_dir: Path
    raw_dir: Path
    wiki_dir: Path
    artifacts_dir: Path
    state_dir: Path
    state_imports_dir: Path
    state_jobs_dir: Path
    state_chats_dir: Path
    state_artifacts_dir: Path
    skills_dir: Path


def get_settings() -> AppSettings:
    root_dir = Path(__file__).resolve().parent.parent
    state_dir = root_dir / "state"
    return AppSettings(
        root_dir=root_dir,
        imports_dir=root_dir / "imports",
        raw_dir=root_dir / "raw",
        wiki_dir=root_dir / "wiki",
        artifacts_dir=root_dir / "artifacts",
        state_dir=state_dir,
        state_imports_dir=state_dir / "imports",
        state_jobs_dir=state_dir / "jobs",
        state_chats_dir=state_dir / "chats",
        state_artifacts_dir=state_dir / "artifacts",
        skills_dir=root_dir / ".claude" / "skills",
    )
