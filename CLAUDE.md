# CLAUDE.md — Personal AI Platform

## Project overview
A personal AI platform: an intelligent backend ("the Core Engine") that remembers, reasons,
plans, researches, uses tools, and can act autonomously. Clients (Telegram first, then web,
mobile, voice, CLI, REST) are thin translators over the Core Engine. This is a real product,
not a demo or an LLM wrapper.

**Name: TBD.** Do NOT invent or use a product name anywhere. Refer to it as "the platform" or
"the Core Engine". Use generic package names: `app`, `core`, `plugins`, `clients`, `integrations`.

## Environment (important)
- This runs on a VM with NO network access to GitHub. Git is LOCAL-ONLY here: commit freely,
  but do NOT run push / pull / fetch / clone or otherwise try to reach a remote — they are
  blocked. Never treat a failed remote/network op as a code bug; just note it and move on.
- Code is moved off this VM by zipping the repo; keep the repo clean and .gitignore honored so
  transfers stay small.

## Tech stack
Python 3.12 · FastAPI · PostgreSQL (+ pgvector) · Redis · SQLAlchemy 2.x (async) · Pydantic v2 ·
Alembic · arq (or Celery) for background jobs · Docker + Docker Compose · pytest · ruff · black ·
mypy · structlog.

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
- Config is environment-driven via a typed settings object. No hardcoded secrets, ever.

## Code quality
Small files, single responsibility. Full type hints; mypy must pass. Dependency injection over
globals. Meaningful exceptions and graceful failure. Retries + caching + rate limiting on all
external calls. Structured logging with context; no bare print.

## Testing & verification (every time, not just when asked)
Before calling any change done, run the quality gate (also available as /verify):
1. ruff check . (fix issues)  2. black .  3. mypy .  4. pytest — run relevant tests then full suite.
Only commit when green, in small logically-scoped commits with clear messages. If tests can't
run for an environmental reason, say so explicitly — never fake a pass.

## Working style
- For anything ~2+ hours of work, plan first and get my approval before coding.
- If a decision is ambiguous or costly-to-reverse, ask rather than guess.
- Prefer clean abstractions with honest stubs over half-built real features. A stub implements
  the interface and raises NotImplementedError (or returns a clearly-marked placeholder) — never
  silently fakes success.
- Don't invent facts about external APIs. Verify the current OpenAI API / model names against
  live docs before writing the adapter; record the version in an ADR.

## Diary
After each completed task, append to docs/DIARY.md: what you tried, what failed and why, what
worked, key insight.