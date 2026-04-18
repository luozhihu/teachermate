from pathlib import Path

from app.services.storage import LocalStore
from app.settings import AppSettings


def build_settings(tmp_path: Path) -> AppSettings:
    state_dir = tmp_path / "state"
    return AppSettings(
        root_dir=tmp_path,
        imports_dir=tmp_path / "imports",
        raw_dir=tmp_path / "raw",
        wiki_dir=tmp_path / "wiki",
        artifacts_dir=tmp_path / "artifacts",
        state_dir=state_dir,
        state_imports_dir=state_dir / "imports",
        state_jobs_dir=state_dir / "jobs",
        state_chats_dir=state_dir / "chats",
        state_artifacts_dir=state_dir / "artifacts",
        skills_dir=tmp_path / ".claude" / "skills",
    )


def test_store_import_and_job(tmp_path):
    store = LocalStore(build_settings(tmp_path))
    record = store.save_import("# Demo", "demo.md", "upload")
    assert store.get_import(record.import_id) is not None

    job = store.create_job(record.import_id)
    job.status = "completed"
    job.topic = "physics"
    store.update_job(job)

    jobs = store.list_jobs()
    assert jobs[0].topic == "physics"


def test_store_chat_and_artifact(tmp_path):
    store = LocalStore(build_settings(tmp_path))
    first_session = store.get_active_chat_session()
    assert first_session.session_id == "default"

    turn = store.save_chat_turn(
        question="什么是牛顿第一定律？",
        answer_markdown="惯性定律的总结",
        citations=[{"title": "牛顿定律", "path": "wiki/physics/newton-laws.md"}],
    )
    artifact = store.save_artifact(
        artifact_type="exam",
        title="牛顿定律小测",
        topic="physics",
        markdown="# exam",
        source_turn_id=turn.turn_id,
        citations=turn.citations,
    )
    assert artifact.markdown_path.endswith(".md")
    assert store.latest_chat_turn().turn_id == turn.turn_id


def test_store_multi_chat_session_isolation(tmp_path):
    store = LocalStore(build_settings(tmp_path))
    session = store.create_chat_session("备课会话")
    store.set_active_chat_session(session.session_id)
    store.save_chat_turn(
        question="A",
        answer_markdown="A-1",
        citations=[],
        session_id=session.session_id,
    )
    store.save_chat_turn(
        question="default",
        answer_markdown="default-1",
        citations=[],
        session_id="default",
    )

    session_turns = store.list_chat_turns(session_id=session.session_id)
    default_turns = store.list_chat_turns(session_id="default")
    assert len(session_turns) == 1
    assert session_turns[0].question == "A"
    assert len(default_turns) == 1
    assert default_turns[0].question == "default"
