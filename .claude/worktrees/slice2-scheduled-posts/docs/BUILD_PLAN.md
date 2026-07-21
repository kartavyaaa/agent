# Build Plan

## MoSCoW — v1 Scope

| Feature | Priority |
|---|---|
| ReAct planner loop (multi-step, max_iterations guard) | Must |
| LLMProvider seam — OpenAI Responses API (`gpt-5.5` / `gpt-5.4-nano`) | Must |
| All 4 memory layers (working / episodic / semantic / knowledge) | Must |
| `web_search` plugin — real (Serper.dev) | Must |
| `reminders` plugin — real (CRUD + arq notification) | Must |
| `file_reader` plugin — real (local file + LLM summarise) | Must |
| Telegram client | Must |
| FastAPI REST API | Must |
| arq background worker | Must |
| Telegram push notifications (arq cron → TelegramNotifier) | Must |
| Caddy + Oracle HTTPS | Must |
| `core/config.py`, `core/logging.py`, `core/exceptions.py` | Must |
| `.gitattributes` (LF line endings) | Must |
| `pyproject.toml` only — no `requirements.txt` | Must |
| Plugin stubs (`calculator`, `weather`, `note_taker`) | Should |
| CLI client (Typer) | Should |
| API key authentication | Should |
| Rate limiting | Could |
| Web frontend BFF routes | Won't (v1) |
| Multi-provider LLM (Anthropic, Ollama) | Won't (v1) |
| Plugin marketplace / hot-reload | Won't (v1) |

---

## Phases

### Phase 1 — Foundation

**Goal:** Runnable skeleton. DB connected. LLM seam in place.

**Key files:**
- `core/config.py`, `core/logging.py`, `core/exceptions.py`
- `models/*.py` + Alembic migration (pgvector ext + all tables + HNSW index)
- `core/llm/base.py`, `core/llm/openai_provider.py` (implement Responses API adapter)
- `plugins/base.py`
- `clients/api/main.py` (FastAPI app factory, lifespan, `/health`)
- `docker-compose.yml`

**Definition of Done:**
- `docker compose up` starts Postgres+pgvector, Redis, API.
- `GET /health` returns 200.
- DB tables created via Alembic; HNSW index present.
- `ruff`, `black`, `mypy` all pass.
- Re-verify OpenAI model IDs, context windows, and pricing against live docs before writing the adapter (ADR-004: specifics are provisional).

---

### Phase 2 — Core Loop

**Goal:** ReAct planner works end-to-end with one tool.

**Key files:**
- `core/engine.py` (full implementation)
- `core/planner/react.py` (ReAct loop)
- `core/tools/registry.py`
- `core/memory/manager.py` + `working.py`, `episodic.py`, `semantic.py`, `knowledge.py`

**Definition of Done:**
- `POST /v1/chat` triggers planner → LLM call → tool dispatch → memory write → response.
- Mocked LLM in tests; real LLM call in integration test.
- Stuck-loop detection raises `PlannerStuckLoopError`.

---

### Phase 3 — Clients and Real Plugins

**Goal:** Telegram bot live. Three real plugins working.

**Key files:**
- `clients/telegram/` (handlers, formatters, middleware)
- `plugins/web_search/`, `plugins/reminders/`, `plugins/file_reader/`
- `integrations/serper.py`, `integrations/google_calendar.py`, `integrations/local_fs.py`
- `clients/api/routes/` (chat, tasks, reminders, projects, memories)

**Definition of Done:**
- Telegram message → engine → plugin → Telegram response.
- Reminder created via `POST /v1/reminders`.
- Web search returns results via Serper.
- File reader summarises a local file.
- Plugin tests green.

---

### Phase 4 — Notifications and Infra

**Goal:** Oracle VM deployment. Real push notifications.

**Key files:**
- `infra/worker/worker_settings.py` (arq WorkerSettings + cron jobs)
- `core/scheduler/jobs.py` (`poll_reminders`, `memory_consolidation`)
- `core/notifications/telegram_notifier.py`
- `infra/docker/Dockerfile`, `infra/caddy/Caddyfile`

**Definition of Done:**
- arq worker polls reminders and fires Telegram push at `remind_at`.
- HTTPS live on Oracle VM with auto-TLS via Caddy.
- Webhook mode active (not polling).

---

### Phase 5 — Polish

**Goal:** Auth, rate limiting, CLI, stub plugins, full test suite green.

**Key files:**
- `clients/api/auth.py` (API key verification)
- `clients/cli/main.py` (Typer)
- `plugins/calculator/`, `plugins/weather/`, `plugins/note_taker/` (stubs with proper error)
- `tests/integration/test_full_flow.py`

**Definition of Done:**
- All routes require valid API key.
- Stub plugins return `PluginNotImplementedError` (not silent fake).
- Full test suite green.
- `docs/DIARY.md` up to date.

---

## Critical Path

```
core/config.py → core/exceptions.py → core/logging.py
       ↓
models/*.py → Alembic migration (pgvector + tables + HNSW)
       ↓
core/llm/base.py → core/llm/openai_provider.py
       ↓
plugins/base.py → core/tools/registry.py
       ↓
core/memory/*.py  ←  openai_provider.embed()
       ↓
core/planner/react.py  (needs: llm, tools, memory)
       ↓
core/engine.py  (orchestrates everything above)
       ↓
clients/*  (import only core.schemas types)
       ↓
infra/  (Docker wraps app + worker)
```

---

## Estimated Effort (solo)

| Phase | Estimate |
|---|---|
| 1 — Foundation | 3–4 days |
| 2 — Core Loop | 4–5 days |
| 3 — Clients + Plugins | 4–5 days |
| 4 — Notifications + Infra | 3–4 days |
| 5 — Polish | 2–3 days |
| **Total** | **16–21 developer-days (~4–5 weeks)** |

---

## Git Commit Discipline

- Commit after each logical unit: infra files, model files, interfaces, docs are separate commits.
- Use `git add -A && git commit` — git is local-only (no push/pull/fetch/clone on the Oracle VM).
- Message format: `feat: <what>` / `fix: <what>` / `docs: <what>`.
- Keep the working tree clean before transferring the repo (zip + copy off VM).
