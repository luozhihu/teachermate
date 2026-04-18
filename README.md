# TeacherMate MVP

## One-command deployment

For a server deployment with `systemd + nginx + Basic Auth`, run:

```bash
export ANTHROPIC_API_KEY="your_real_api_key"
export BASIC_AUTH_PASSWORD="choose_a_strong_password"
bash deploy/install.sh
```

The full deployment guide is in [`deploy/README.md`](deploy/README.md).

Single-user teacher knowledge base website built with FastAPI, HTMX, MarkItDown, Claude Agent SDK, and the `karpathy-llm-wiki` skill.

## What it does

- Import files or pasted text into an `imports/` staging area
- Convert `pdf` and `docx` to Markdown via MarkItDown
- Auto-compile each import into `raw/` and `wiki/` through `karpathy-llm-wiki`
- Show compile process output in the web UI (ephemeral, in-memory only)
- Stream QA generation output in the chat panel (ephemeral, in-memory only)
- Show exam/lesson generation logs below each generation button (ephemeral, in-memory only)
- Ask grounded questions against the compiled wiki with switchable chat sessions
- Generate exam drafts and lesson-plan drafts directly from wiki (independent from chat)
- Support optional exam focus field for exam generation
- Support generating exam/lesson-plan directly from chat intent

## Requirements

- Python 3.11+
- Claude credentials available for `claude-agent-sdk`

Official references:

- Claude Agent SDK for Python: https://platform.claude.com/docs/en/agent-sdk/python
- Agent Skills in the SDK: https://platform.claude.com/docs/en/agent-sdk/skills
- Structured outputs: https://platform.claude.com/docs/en/agent-sdk/structured-outputs
- MarkItDown: https://github.com/microsoft/markitdown

## Run

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

Optional compile tuning:

- `TEACHERMATE_COMPILE_MAX_TURNS` (default `24`)
- `TEACHERMATE_COMPILE_RETRY_MAX_TURNS` (default `48`)

These control the wiki compile step when `karpathy-llm-wiki` needs more turns.

## Directory layout

```text
imports/        staged markdown sources before wiki ingest
raw/            final raw sources written by karpathy-llm-wiki
wiki/           compiled wiki maintained by karpathy-llm-wiki
artifacts/      generated exams and lesson plans
state/          json metadata for imports, jobs, chat turns, and artifacts
.claude/skills/ project-local Claude skills
```

## Deployment

For a single-server production-style deployment using `systemd + nginx + Basic Auth`, see [`deploy/README.md`](deploy/README.md).
