# ADR-002: Background Job Runner

**Status:** Accepted  
**Date:** 2026-07-07

## Context

The platform needs background processing for:
- Reminder polling (cron, every 60 s) → Telegram push
- Memory consolidation (cron, hourly) → embedding + importance scoring
- Plugin health checks (cron, every 5 min)
- Deferred tool execution for long-running plugin calls

Requirements: asyncio-native, cron support, retry, per-job deduplication, single worker process.

## Decision

**Use arq (Async Redis Queue).**

## Rationale

- **Async-native.** arq is built on asyncio and aioredis. Worker functions are plain Python `async def` functions — no thread pool, no `asyncio.run()` hacks, no sync/async impedance mismatch with FastAPI and SQLAlchemy async.
- **Cron support built in.** `WorkerSettings.cron_jobs` handles the reminder poller and memory consolidation without a separate beat process (unlike Celery which requires `celery beat` as a separate daemon).
- **Reuses Redis.** Redis is already in the stack as the working memory store (session TTL). arq uses the same instance; no extra service needed.
- **Job deduplication.** `job_id` parameter prevents double-firing if the poller overlaps with itself.
- **Simple worker entry point.** `infra/worker/worker_settings.py` defines `WorkerSettings`; the worker process is `python -m arq infra.worker.worker_settings.WorkerSettings`.

## Alternatives Rejected

| Option | Reason rejected |
|---|---|
| Celery | Sync-first; async support (`gevent` or `asgiref`) adds complexity; requires separate `celery beat` for cron; heavier dependency tree |
| APScheduler | In-process scheduler only — no retry, no multi-worker, no job queue; ties scheduler to the API process lifecycle |
| Redis Streams (custom) | Build-your-own retry, DLQ, cron — unnecessary complexity |

## Consequences

- Redis is a hard dependency (also used for working memory — acceptable).
- Worker runs as a separate Docker service sharing the same image as the API (`Dockerfile.worker`).
- arq cron jobs fire on worker check-in, not exactly on schedule. The reminder poller query `WHERE remind_at <= now() AND sent_at IS NULL` is robust to slight timing drift.
- Worker concurrency is controlled by `WorkerSettings.max_jobs` (default: 10).
