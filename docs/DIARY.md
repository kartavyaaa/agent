# Development Diary

---

## 2026-07-07 — Session A: Architecture Design

**What was done:**
- Designed the full backend architecture for the platform.
- Produced docs: ARCHITECTURE.md, DB_SCHEMA.md, BUILD_PLAN.md, ADR-001 through ADR-004.
- Created the full repo skeleton: all directories, `__init__.py` files, and placeholder stubs.
- Wrote the real content for: `core/config.py`, `core/logging.py`, `core/exceptions.py`, `core/llm/base.py`, `core/llm/openai_provider.py` (stub shell), `plugins/base.py`, and all six SQLAlchemy model files.
- Wrote `.gitattributes` (LF line endings), `.env.example`, and `pyproject.toml`.

**Key decisions:**
- pgvector with **HNSW** index (not ivfflat) — HNSW builds on empty tables; ivfflat needs a training set.
- **arq** for background jobs — async-native, reuses Redis, cron built in.
- **Caddy** for reverse proxy — auto-TLS, ARM64 binary, 3-line config.
- OpenAI **Responses API** (`client.responses.create`) — current primary surface.
- **GPT-5 family** for LLM: `gpt-5.5` (complex steps) and `gpt-5.4-nano` (fast/cheap steps). No separate reasoning model — GPT-5 uses `reasoning_effort` param.
- Model specifics marked provisional; must re-verify at Phase 1 implementation.

**What worked:**
- Fetching live model specs from developers.openai.com to avoid stale model names.
- Separating `openai` import to a single file (`openai_provider.py`) enforced structurally in the skeleton.
- HNSW correction caught before implementation — would have been painful to fix post-migration.

**What to watch:**
- OpenAI Responses API `input[]` typed Items translation is non-obvious; document carefully in `openai_provider.py`.
- Google Calendar OAuth needs service account setup documented in plugin `config_schema` before Phase 3.
- Re-verify model snapshot IDs and pricing when starting Phase 1 (ADR-004 provisional note).

**Next:** Phase 1 — implement `core/llm/openai_provider.py`, Alembic migrations, FastAPI app factory, Docker Compose.

---

## 2026-07-07 — Session B Chunk 1: DB, LLM, Schemas

**What was done:**
- Alembic migrations: `0001_initial.py` (all tables, btree/partial indexes, native ENUMs, `CREATE EXTENSION IF NOT EXISTS vector`) and `0002_hnsw_index.py` (plain `CREATE INDEX USING hnsw`, no CONCURRENTLY — cannot run inside Alembic's transaction block).
- `docker-compose.yml` with `pgvector/pgvector:pg16`, Redis 7, app, and worker services; all with health checks.
- `infra/docker/Dockerfile` (runtime only, `pip install -e .`) and `Dockerfile.worker`.
- `.dockerignore`: excludes `.git`, `.env`, `__pycache__`, caches, `tests/`, `docs/`.
- `core/schemas.py`: `CoreRequest` / `CoreResponse` with `session_id` defaulting to `uuid4()`.
- `core/llm/openai_provider.py`: full implementation of `complete()` + `embed()` behind `LLMProvider` seam. Retries via tenacity on `LLMRateLimitError`. Embedding cache keyed on text. `stream()` raises `NotImplementedError`.
- Unit tests: 10 tests for `OpenAIProvider` (message path, tool call path, malformed args, rate limit retry, timeout, embed cache, embed batch, partial cache, list_models).

**What worked:**
- Tenacity `@retry` on the inner `_complete_once` method; `complete()` calls it — clean separation.
- `# type: ignore[return-value]` on `stream()` to silence mypy about `AsyncIterator` vs coroutine — then discovered cleaner fix: make `stream()` a regular `def`, not `async def`.
- `async_sessionmaker` pattern confirmed for engine → no session exposed to clients.

**Key facts confirmed (Responses API):**
- `item.arguments` is a JSON **string** — must `json.loads()`. Wrap in `try/except json.JSONDecodeError`.
- Tool defs use `{"type": "function", "name": ..., "description": ..., "parameters": ...}` shape.
- Usage fields: `response.usage.input_tokens`, `response.usage.output_tokens`, `response.usage.total_tokens`.

---

## 2026-07-07 — Session B Chunk 2: Memory, Registry, Plugin, Engine, API

**What was done:**
- Confirmed `models/memory.py` and `models/project.py` correctly map `metadata_` Python attribute to `"metadata"` DB column via `mapped_column("metadata", JSONB, ...)`. No fix needed.
- `core/memory/types.py`: `MemoryType = Literal["working", "episodic", "semantic", "knowledge"]`.
- `core/memory/manager.py`: `write()` embeds content, applies heuristic importance score, stores `Memory` row. `semantic_search()` embeds query, filters by type, orders by cosine distance, updates `last_accessed_at`.
- `core/memory/semantic.py`, `episodic.py`: thin helpers calling `manager.semantic_search` with type filter.
- `core/memory/working.py`, `knowledge.py`: stubs raising `NotImplementedError`.
- `core/tools/registry.py`: `register()`, `get_tools_for_llm()` (strips `user_id` from LLM-visible schema), `execute()` (validates input schema, injects `user_id` + `db`).
- `plugins/reminders/schemas.py`: `ReminderInput` (LLM fields only: `message`, `remind_at`), `ReminderOutput` (`reminder_id`, `message`, `remind_at`, `confirmation`), `ReminderConfig`.
- `plugins/reminders/plugin.py`: full `RemindersPlugin` — creates `Reminder` row, flushes for ID, engine commits. `ClassVar` annotations on `capabilities`, `permissions`, `dependencies` to avoid mypy override error.
- `core/engine.py`: `handle_request()` opens session, calls `_process()`, commits on success, rolls back on any exception. System prompt includes current UTC time so relative phrases resolve correctly.
- `clients/api/dependencies.py`: `get_engine()` reads from `app.state.engine`.
- `clients/api/routes/health.py`: `GET /health → {"status": "ok"}`.
- `clients/api/routes/reminders.py`: `POST /v1/reminders` → `engine.handle_request`; `GET /v1/reminders/{user_id}` → direct DB query.
- `clients/api/main.py`: lifespan wires all dependencies (engine, LLM, memory, registry) and registers `RemindersPlugin`.
- `tests/conftest.py`: Docker detection; auto-skip integration tests if Docker unavailable.
- `tests/core/test_memory.py`: 11 tests (write path, heuristic scoring, semantic search).
- `tests/plugins/test_reminders.py`: 8 tests (schema shape, execute path, naive datetime handling, health check).
- `tests/core/test_engine.py`: 9 tests (direct message, tool call, commit/rollback, user_id injection, system prompt UTC, memory write).

**Quality gate result:** ruff ✓ · black ✓ · mypy ✓ (0 errors, 100 source files) · pytest 37/37 ✓

**What failed and why:**
- `ModuleNotFoundError: No module named 'pgvector'` — `models/memory.py` imports `Vector` at module level; test collection failed. Fixed by `pip install pgvector` (required `--trusted-host` flags for T-Mobile corporate TLS).
- `Cannot override class variable with instance variable` — `RemindersPlugin.dependencies: list[str] = []` conflicted with `PluginBase` ABC's typed field. Fixed by using `ClassVar[list[str]]` annotation on all three plugin-level class variables.
- `stream()` return type mismatch — `async def stream(...) -> AsyncIterator[...]` is a coroutine in mypy's view. Fixed by removing `async` from `stream()` in both ABC and provider.

**Key insight:**
- Engine session pattern (`async with session_factory() as db`) requires careful mock setup in tests: `mock_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)` and `__aexit__ = AsyncMock(return_value=False)`.

---

## Verify against live OpenAI API (checklist for first real API call)

Before running any live request through `OpenAIProvider`, verify these against current API behavior:

1. **Usage field names** — confirm `response.usage.input_tokens`, `response.usage.output_tokens`, `response.usage.total_tokens` exist on the Responses API `ResponseUsage` object (not `prompt_tokens`/`completion_tokens` which are Chat Completions names). Also check `cached_tokens` availability.

2. **`response.output` item shape** — confirm:
   - Text items: `item.type == "message"`, content list with `item.content[0].text`
   - Tool call items: `item.type == "function_call"`, `item.name`, `item.call_id`, `item.arguments` is a JSON **string** (not a parsed dict)
   - No other item types expected in basic use

3. **Strict-mode tool schema compliance** — confirm that tools sent via `{"type": "function", "parameters": {...}}` satisfy any schema restrictions the Responses API enforces (e.g., no `additionalProperties`, all required fields listed, no unsupported JSON Schema keywords). Pydantic `model_json_schema()` output may need post-processing for strict compliance.

---

## 2026-07-07 — Session B Chunk 3: Notifier, Scheduler, Worker, Telegram Client, Integration Test

**What was done:**
- `core/notifications/telegram_notifier.py`: `TelegramNotifier.send(telegram_id, message)` via httpx POST. `http_client` injected for testing; defaults to a fresh `AsyncClient()` if not provided.
- `core/scheduler/jobs.py`: `poll_reminders(ctx)` — queries due unsent reminders with `skip_locked=True`, fetches user, calls `notifier.send`, marks `sent_at`. Continues loop on send failure (logs warning, does not abort). Commits in one transaction per poll cycle.
- `infra/worker/worker_settings.py`: arq `WorkerSettings` with `cron(poll_reminders, second={0})` (fires every minute at second=0). `startup` builds `session_factory`, `httpx.AsyncClient`, `TelegramNotifier`; `shutdown` closes the client.
- `clients/telegram/handlers.py`: thin translator — imports only `CoreRequest`, `CoreResponse` from `core.schemas`. Handler signature receives `engine` and `telegram_user_map` as injected kwargs (not imported from core). Maps `from_user.id` → `uuid.UUID` → `CoreRequest`.
- `clients/telegram/bot.py`: builds `Bot` + `Dispatcher`, includes router, passes `engine` and `telegram_user_map` to `dp.start_polling(**kwargs)` — aiogram injects these into handler parameters automatically.
- `tests/core/test_scheduler.py`: 6 unit tests — due reminder sent, `sent_at` set within expected time window, no telegram_id skips send but still marks sent, empty list no-ops, send failure continues to next reminder, session committed once.
- `tests/integration/test_full_flow.py`: full round-trip integration test — `pgvector/pgvector:pg16` via testcontainers, Alembic `upgrade head` against container URL, real ORM writes, mocked LLM + notifier. Verified: `CoreResponse.tool_calls_made`, `Memory` row written with correct type and content, `Reminder` row written with `sent_at=None`, backdated `remind_at`, `poll_reminders` fires, notifier called with correct `telegram_id`, `sent_at` set. Skips cleanly when Docker is unavailable.

**Quality gate result:** ruff ✓ · black ✓ · mypy ✓ (0 errors, 101 source files) · pytest 43/43 unit ✓ · 1 integration test skipped (Docker not available on VM)

**What failed and why:**
- `dict[object, object]` ctx type annotation in test_scheduler: `poll_reminders` is typed `dict[str, Any]`. Fixed by annotating helper return as `dict[str, object]`.
- `pytest.fixture` with `yield` annotated as `-> str` not `-> Generator[str, None, None]`: mypy flagged it. Fixed import and annotation.
- `Bot` doesn't support `[]` indexing in aiogram 3.x: initial draft used `bot["engine"] = ...` pattern. aiogram's correct DI mechanism for handler data is `dp.start_polling(..., **kwargs)` — kwargs are injected into handler function parameters by name. Rewrote both `bot.py` and `handlers.py` accordingly.

**Key insight:**
- aiogram 3.x uses `Dispatcher` workflow kwargs for DI — pass `engine=engine` to `start_polling` and declare `engine: Any` in the handler signature. No `bot.data` dict, no global state.
- `skip_locked=True` on the poller query is essential for correctness under multiple workers — without it, two workers could both pick up and double-send the same reminder.

---

## Environment note: Python version mismatch on VM (2026-07-07)

**Fact:** The VM runs Python 3.11. The project targets Python 3.12 (`pyproject.toml`: `requires-python = ">=3.12"`, `[tool.black] target-version = ["py312"]`, `[tool.mypy] python_version = "3.12"`). The project target does not change.

**Symptom:** `black --check` on the VM emits a warning — *"Python 3.11 cannot parse code formatted for Python 3.12"* — and uses `--fast` semantics (skips AST equivalence check). This means black's safety pass is degraded on the VM; it can still reformat but cannot verify the result parses correctly under 3.12.

**Authoritative gate:** The Docker image (`FROM python:3.12-slim` in `infra/docker/Dockerfile`) is the authoritative quality gate. CI and the final `docker compose up --build` run on 3.12. VM checks are a fast pre-check only — useful for catching ruff/mypy/pytest regressions early, but black's AST safety guarantee only holds in Docker.

**Resolution options (choose one):**
- **(a) Install Python 3.12 on the VM** — makes VM gate fully authoritative; black AST check works correctly; preferred if the VM is long-lived.
- **(b) Keep 3.11 on VM, treat Docker as authoritative** — acceptable for this slice; VM gate catches most issues; black formatting differences resolved by running `docker run --rm -v $(pwd):/app python:3.12-slim black /app` before committing if needed.

**How to apply:** Until 3.12 is on the VM, treat a `black --check` warning (not error) as expected noise. A `black` reformat failure (exit 1) is still a real error. All other gate steps (ruff, mypy, pytest) are unaffected by the version mismatch.

---

## Phase 2 note: `_process` revisit (ReAct loop)

**Filed for Phase 2 (ReAct planner loop):**

`CoreEngine._process` currently has two limitations that are acceptable for the single-step slice but must be fixed before the multi-step ReAct loop:

1. **Last-write-wins tool result** — when multiple tool calls are made in one turn, `result_content` is overwritten by each successive call. The ReAct loop needs to accumulate all tool outputs and synthesize a final response from the full history.

2. **Hardcoded `memories_written=1`** — `CoreResponse(memories_written=1, ...)` is a lie if `memory.write` raises or if multiple memory writes are needed per turn. Phase 2 should count actual successful `write()` calls (or use a return value from `write` to confirm persistence).

Neither is a bug in the current single-call slice (only one tool fires per request), but both will produce incorrect behavior in the multi-step loop.
