# CLAUDE.md — Personal AI Platform

## Project overview
A personal AI platform: an intelligent backend ("the Core Engine") that remembers, reasons,
plans, researches, uses tools, and can act autonomously. Clients (Telegram first, then web,
mobile, voice, CLI, REST) are thin translators over the Core Engine. This is a real product,
not a demo or an LLM wrapper.

**Name: TBD.** Do NOT invent or use a product name anywhere. Refer to it as "the platform" or
"the Core Engine". Use generic package names: `app`, `core`, `plugins`, `clients`, `integrations`.

## Environment (important)
- Code is authored on a VM (Python 3.11) with NO network access to GitHub. Git is LOCAL-ONLY
  here: commit freely, but do NOT run push / pull / fetch / clone — they are blocked. Never treat
  a failed remote/network op as a code bug; note it and move on.
- **The project targets Python 3.12, but this VM runs 3.11.** Any `/verify` run here is a
  PROVISIONAL pre-check only. mypy/black/pytest under 3.11 can pass while 3.12-real behavior
  differs. The AUTHORITATIVE gate runs on the PC (Python 3.12 + Docker) — see "Verification".
- Code moves off this VM by zipping the repo (see the playbook's transfer section). The PC is
  the authoritative copy for verified/pushed code; the VM is where new code is written and then
  synced back to the PC for verification. Keep the repo clean and `.gitignore` honored.

## Non-negotiable architecture rules
- **No business logic in clients.** Telegram/API/CLI only translate input → a Core Engine
  request and render the response. A client module may import from `core` ONLY the public
  request/response types — nothing else.
- **One LLM provider seam.** All model access goes through a single `LLMProvider` interface.
  Swapping providers/models must touch exactly ONE file. No vendor imports outside that adapter.
- **Every capability is a plugin.** Plugins never import each other; they talk via the Core
  Engine / tool registry. A new plugin installs without editing the Core Engine.
- **Plugin contract** (each exposes): capabilities, description, Pydantic input schema, output
  schema, permissions, dependencies, config, health check, version.
- **Trusted context is never a tool argument.** LLM-facing tool schemas contain only
  model-supplied fields. `user_id`, DB sessions, and other trusted context are injected by the
  engine/registry, never taken from tool-call arguments.
- Config is environment-driven via a typed settings object. No hardcoded secrets, ever.

## Schema: single source of truth
- The models (`models/*.py`) and the Alembic migrations are two definitions of the same schema
  and MUST stay consistent. When you change one, change the other, and verify they agree.
  [Resolve early: either autogenerate migrations from models, or keep a schema-equivalence test.]
- Postgres enums: define enum columns with `postgresql.ENUM(..., name="<type>", create_type=False)`
  and create the types explicitly once in the migration. `sa.Enum(..., create_type=False)` does
  NOT reliably suppress creation on the pg dialect. Enum literals in SQL (e.g. partial-index
  `WHERE status = 'pending'`) must be cast: `'pending'::task_status`.
- `models/__init__.py` MUST import every model so cross-table foreign keys resolve at flush time.

## Code quality
Small files, single responsibility. Full type hints; mypy must pass. Dependency injection over
globals. Meaningful exceptions and graceful failure. Retries + caching + rate limiting on all
external calls. Structured logging with context; no bare print. Use `datetime.now(timezone.utc)`
— never the deprecated `datetime.utcnow()`; columns are `TIMESTAMPTZ`.

## Testing & verification (every time, not just when asked)
Unit tests mock the DB and do not parse `.env` — a green unit suite is NECESSARY BUT NOT
SUFFICIENT. Two levels:

**Provisional (VM, fast, every change):** run `/verify` — ruff, black, mypy, `pytest -m "not
integration"`. Fix everything before committing. Commit only when green, in small logical commits.

**Authoritative (PC, Python 3.12 + Docker) — REQUIRED for any change touching a migration, a
model, `pyproject.toml`, `config.py`, `.env.example`, `docker-compose.yml`, or dependencies:**
1. Clean install from the manifest in a fresh venv: `pip install -e ".[dev]"` (catches
   build-backend and missing-dependency bugs mocks can't).
2. Full suite INCLUDING the real-DB integration test with Docker running: `pytest -v` (0 skipped).
   New migrations must apply via `alembic upgrade head` against a REAL Postgres — never assume a
   migration works because unit tests pass; unit tests mock the DB.
3. For packaging/compose changes: `docker compose up --build` boots and `/health` returns 200,
   and `docker compose logs worker` shows the worker did not crash-loop.

If a step can't run for an environmental reason (e.g. no Docker on the VM), say so explicitly and
defer it to the PC gate — never fake a pass, and never report a migration/config change "done"
on the strength of mocked tests alone.

## Working style
- For anything ~2+ hours of work, plan first and get my approval before coding.
- If a decision is ambiguous or costly-to-reverse, ask rather than guess.
- Prefer clean abstractions with honest stubs over half-built real features. A stub implements
  the interface and raises NotImplementedError (or returns a clearly-marked placeholder) — never
  silently fakes success.
- Don't invent facts about external APIs. Verify the current OpenAI API / model names against
  live docs before writing/adapting the adapter; record the version in an ADR. Model/API specifics
  in ADR-004 are provisional until re-verified at implementation time.

## Secrets & git hygiene
- `.env` holds real secrets: gitignored, NEVER committed. `.env.example` holds PLACEHOLDERS only
  (e.g. `OPENAI_API_KEY=sk-your-key-here`) and IS committed. They mirror each other in structure,
  never in values.
- `.gitignore` must exclude `.venv/`, `__pycache__/`, `*.pyc`, all `*_cache/`, `.env`, `dist/`,
  `build/`. Confirm `git status` never stages those.
- Never bypass a secret-scanner or push-protection block by "allowing" the secret — fix the file.

## Diary
After each completed task, append to `docs/DIARY.md`: what you tried, what failed and why, what
worked, key insight. Also record any deferred decisions (e.g. the schema single-source-of-truth
choice, the `_process` multi-tool/memory-count fix for the ReAct phase).
