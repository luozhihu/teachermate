from pathlib import Path
import time

from fastapi.testclient import TestClient

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


class FakeAgentService:
    async def compile_import(self, record, on_progress=None):
        if on_progress:
            on_progress("fake compile start")
        return {
            "topic": "physics",
            "summary": f"compiled {record.markdown_path}",
            "raw_paths": ["raw/physics/sample.md"],
            "wiki_paths": ["wiki/physics/sample.md"],
        }

    async def answer_question(self, question, claude_session_id=None, on_progress=None):
        if on_progress:
            on_progress("partial ")
            on_progress("answer")
        return {
            "answer_markdown": f"answer for {question}",
            "citations": [{"title": "样例", "path": "wiki/physics/sample.md"}],
            "claude_session_id": claude_session_id or "claude-session-1",
        }

    async def generate_research_topic(self, payload, on_progress=None):
        if on_progress:
            on_progress("topic progress")
        assert "research_objectives" in payload
        return {
            "title": "research-topic",
            "topic": "cardiology",
            "markdown": "# research topic",
            "citations": [{"title": "样例", "path": "wiki/cardiology/sample.md"}],
        }

    async def generate_research_protocol(self, payload, on_progress=None):
        if on_progress:
            on_progress("protocol progress")
        del payload
        return {
            "title": "research-protocol",
            "topic": "cardiology",
            "markdown": "# protocol",
            "citations": [{"title": "样例", "path": "wiki/cardiology/sample.md"}],
        }


def test_import_auto_compile_and_session_chat(monkeypatch, tmp_path):
    import app.main as main_module

    store = LocalStore(build_settings(tmp_path))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "agent_service", FakeAgentService())
    client = TestClient(main_module.app)

    response = client.post("/imports", data={"pasted_text": "牛顿第一定律"})
    assert response.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        imports = store.list_imports()
        if imports and imports[0].status == "compiled":
            break
        time.sleep(0.02)
    imports = store.list_imports()
    jobs = store.list_jobs()
    assert imports[0].status == "compiled"
    assert jobs[0].status == "completed"

    response = client.post("/chat", data={"question": "讲一下重点", "session_id": "default"})
    assert response.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        latest = store.latest_chat_turn("default")
        if latest is not None:
            break
        time.sleep(0.02)
    assert store.latest_chat_turn("default") is not None
    assert store.get_chat_session("default").claude_session_id == "claude-session-1"

    response = client.post("/chat/sessions/create", data={"title": "第二会话"})
    assert response.status_code == 200
    active_session = store.get_active_chat_session()
    assert active_session.title == "第二会话"

    response = client.post("/chat", data={"question": "第二会话问题", "session_id": active_session.session_id})
    assert response.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        latest = store.latest_chat_turn(session_id=active_session.session_id)
        if latest and latest.question == "第二会话问题":
            break
        time.sleep(0.02)
    assert store.latest_chat_turn(session_id=active_session.session_id).question == "第二会话问题"
    assert store.latest_chat_turn(session_id="default").question == "讲一下重点"


def test_upload_file_import_flow(monkeypatch, tmp_path):
    import app.main as main_module

    store = LocalStore(build_settings(tmp_path))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "agent_service", FakeAgentService())
    client = TestClient(main_module.app)

    response = client.post(
        "/imports",
        files={"upload": ("lesson.txt", "牛顿第一定律".encode("utf-8"), "text/plain")},
        data={"center_tab": "import"},
    )
    assert response.status_code == 200
    assert "导入文献" in response.text
    assert "lesson.txt" in response.text

    deadline = time.time() + 2
    while time.time() < deadline:
        imports = store.list_imports()
        if imports and imports[0].source_name == "lesson.txt":
            break
        time.sleep(0.02)
    imports = store.list_imports()
    assert imports
    assert imports[0].source_name == "lesson.txt"


def test_homepage_and_workspace_routes(monkeypatch, tmp_path):
    import app.main as main_module

    store = LocalStore(build_settings(tmp_path))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "agent_service", FakeAgentService())
    client = TestClient(main_module.app)

    homepage = client.get("/")
    assert homepage.status_code == 200
    assert "把科研整理时间" in homepage.text
    assert "试试看，很提效" in homepage.text
    assert 'href="/workspace"' in homepage.text

    workspace = client.get("/workspace")
    assert workspace.status_code == 200
    assert "医学科研知识工作台" in workspace.text

    workspace_chat = client.get("/workspace", params={"center_tab": "chat"})
    assert workspace_chat.status_code == 200
    assert 'id="nav-chat" checked' in workspace_chat.text
    assert "对话知识库" in workspace_chat.text
    assert "event.key === 'Enter'" in workspace_chat.text
    assert 'hx-encoding="multipart/form-data"' in workspace_chat.text

    workspace_partial = client.get(
        "/workspace",
        params={"center_tab": "chat"},
        headers={"HX-Request": "true"},
    )
    assert workspace_partial.status_code == 200
    assert '<div id="workspace">' in workspace_partial.text
    assert "<!DOCTYPE html>" not in workspace_partial.text


def test_generate_without_chat_context(monkeypatch, tmp_path):
    import app.main as main_module

    store = LocalStore(build_settings(tmp_path))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "agent_service", FakeAgentService())
    client = TestClient(main_module.app)

    response = client.post(
        "/generate/research-topic",
        data={
            "research_field": "心血管内科",
            "research_direction": "病理机制",
            "novelty_level": "常规总结",
            "expected_pages": "5-10页",
            "research_objectives": "探索新的治疗靶点",
            "session_id": "default",
        },
    )
    assert response.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        artifacts = store.list_artifacts()
        if artifacts and artifacts[0].artifact_type == "research-topic":
            break
        time.sleep(0.02)
    artifacts = store.list_artifacts()
    assert artifacts and artifacts[0].artifact_type == "research-topic"
    runtime_topic = client.get("/generate/runtime", params={"kind": "research-topic"})
    assert runtime_topic.status_code == 200
    assert "选题文档过程" in runtime_topic.text

    response = client.post(
        "/generate/research-protocol",
        data={
            "research_field": "心血管内科",
            "study_design": "随机对照试验",
            "timeline": "6个月",
            "objectives": "评估疗效",
            "session_id": "default",
        },
    )
    assert response.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        artifacts = store.list_artifacts()
        if any(item.artifact_type == "research-protocol" for item in artifacts):
            break
        time.sleep(0.02)
    artifacts = store.list_artifacts()
    assert any(item.artifact_type == "research-protocol" for item in artifacts)
    runtime_protocol = client.get("/generate/runtime", params={"kind": "research-protocol"})
    assert runtime_protocol.status_code == 200
    assert "研究方案过程" in runtime_protocol.text


def test_chat_runtime_hidden_after_completion(monkeypatch, tmp_path):
    import app.main as main_module

    store = LocalStore(build_settings(tmp_path))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "agent_service", FakeAgentService())
    client = TestClient(main_module.app)

    response = client.post("/chat", data={"question": "测试重复展示", "session_id": "default"})
    assert response.status_code == 200

    deadline = time.time() + 2
    while time.time() < deadline:
        latest = store.latest_chat_turn("default")
        if latest is not None:
            break
        time.sleep(0.02)
    assert store.latest_chat_turn("default") is not None

    runtime = client.get("/chat/runtime", params={"session_id": "default"})
    assert runtime.status_code == 200
    assert "chat-bubble user runtime" not in runtime.text
    assert "chat-bubble assistant runtime" not in runtime.text

    workspace_chat = client.get("/workspace", params={"center_tab": "chat"})
    assert workspace_chat.status_code == 200
    assert 'hx-get="/workspace?center_tab=chat&session_id=default"' in workspace_chat.text

    workspace_import = client.get("/workspace", params={"center_tab": "import"})
    assert workspace_import.status_code == 200
    assert 'hx-get="/workspace?center_tab=chat&session_id=default"' not in workspace_import.text


def test_chat_can_generate_deliverable(monkeypatch, tmp_path):
    import app.main as main_module

    class DeliverableAgent(FakeAgentService):
        async def answer_question(self, question, claude_session_id=None, on_progress=None):
            if on_progress:
                on_progress("stream ")
                on_progress("output")
            return {
                "answer_markdown": f"已根据请求生成文档：{question}",
                "citations": [{"title": "样例", "path": "wiki/cardiology/sample.md"}],
                "claude_session_id": claude_session_id or "claude-session-2",
                "deliverable": {
                    "type": "research-topic",
                    "title": "对话生成选题",
                    "topic": "cardiology",
                    "markdown": "# topic from chat",
                },
            }

    store = LocalStore(build_settings(tmp_path))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "agent_service", DeliverableAgent())
    client = TestClient(main_module.app)

    response = client.post("/chat", data={"question": "请直接生成选题文档", "session_id": "default"})
    assert response.status_code == 200
    deadline = time.time() + 2
    while time.time() < deadline:
        turns = store.list_chat_turns("default")
        artifacts = store.list_artifacts()
        if turns and artifacts:
            break
        time.sleep(0.02)

    artifacts = store.list_artifacts()
    assert artifacts
    assert artifacts[0].artifact_type == "research-topic"


def test_chat_answer_markdown_is_rendered(monkeypatch, tmp_path):
    import app.main as main_module

    class MarkdownAgent(FakeAgentService):
        async def answer_question(self, question, claude_session_id=None, on_progress=None):
            del question
            if on_progress:
                on_progress("stream chunk")
            return {
                "answer_markdown": "# 结果\n\n- 要点A\n\n这是 **重点** 和 `code`。\n\n<script>bad()</script>",
                "citations": [{"title": "样例", "path": "wiki/physics/sample.md"}],
                "claude_session_id": claude_session_id or "claude-session-markdown",
            }

    store = LocalStore(build_settings(tmp_path))
    monkeypatch.setattr(main_module, "store", store)
    monkeypatch.setattr(main_module, "agent_service", MarkdownAgent())
    client = TestClient(main_module.app)

    response = client.post("/chat", data={"question": "渲染 markdown", "session_id": "default"})
    assert response.status_code == 200

    deadline = time.time() + 2
    while time.time() < deadline:
        latest = store.latest_chat_turn("default")
        if latest is not None:
            break
        time.sleep(0.02)
    assert store.latest_chat_turn("default") is not None

    workspace = client.get("/workspace", params={"center_tab": "chat"})
    assert workspace.status_code == 200
    assert "<h1>结果</h1>" in workspace.text
    assert "<li>要点A</li>" in workspace.text
    assert "<strong>重点</strong>" in workspace.text
    assert "&lt;script&gt;bad()&lt;/script&gt;" in workspace.text
