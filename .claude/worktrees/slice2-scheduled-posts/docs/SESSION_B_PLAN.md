# Session B Plan — Reminder Round-Trip Vertical Slice (Revised)

_Saved 2026-07-07. All 10 corrections from review applied._

---

## Context

Session A produced the repo skeleton: all interfaces, model stubs, docs, and ADRs. Nothing runs yet.

Session B implements the **first working vertical slice**: a reminder round-trip that exercises every layer end-to-end. The goal is a system where a user can say "remind me tomorrow" and:
1. The Core Engine handles the request via the plugin contract.
2. A memory of the reminder is stored with a pgvector embedding.
3. A Reminder row is persisted in Postgres.
4. An arq background worker fires the reminder at `remind_at` via Telegram push.
5. The whole stack boots with `docker compose up` and the quality gate passes.

This is **not** the full ReAct planner loop — that is Phase 2. This slice uses a simplified single-step engine: parse intent → call plugin directly → return.

---

## Scope

| # | Component | Scope |
|---|---|---|
| 1 | Core Engine | Minimal real request/response contract, owns DB session + commit lifecycle, DI wiring, structured logging |
| 2 | LLMProvider (OpenAI) | `complete()` + `embed()` behind the seam. Retries, timeout, embedding cache only. |
| 3 | Memory engine | `write()` + `semantic_search()` with real pgvector persistence. Heuristic importance scoring. |
| 4 | Reminders plugin | Full implementation against `PluginBase` contract; `user_id` injected by registry (not in LLM args). |
| 5 | Scheduler + worker | arq `WorkerSettings` with `poll_reminders` cron job. `TelegramNotifier` delivers via httpx. |
| 6 | Telegram client | Thin translator only — imports `CoreRequest`/`CoreResponse` from `core.schemas`, nothing else. |
| 7 | Alembic migrations | Two: (1) all tables + btree indexes; (2) plain `CREATE INDEX USING hnsw` (no CONCURRENTLY). |
| 8 | Docker | app + postgres/pgvector:pg16 + redis + worker + `.dockerignore`. `GET /health` → 200. |
| 9 | Tests | Unit (no Docker, always run on VM). Integration (testcontainers Postgres, mocked embed + Telegram, skip without Docker). |

**Out of scope this session:** ReAct planner loop, web_search plugin, file_reader plugin, CLI client, Caddy/HTTPS, API key auth, rate limiting, working/knowledge memory layers (stubs only).

---

## Verified API facts (fetched 2026-07-07)

```python
# OpenAI Responses API
response = await client.responses.create(
    model="gpt-5.5",
    input=[{"role": "user", "content": "..."}],
    tools=[{"type": "function", "name": "...", "description": "...", "parameters": {...}}],
)
for item in response.output:
    if item.type == "function_call":
        # item.arguments is a JSON STRING — must json.loads() before use
        args = json.loads(item.arguments)   # raises JSONDecodeError → wrap + log
        # item.call_id, item.name
    elif item.type == "message":
        text = item.content[0].text

# Tool result submission:
response2 = await client.responses.create(
    model="gpt-5.5",
    input=[*response.output, {"type": "function_call_output", "call_id": ..., "output": str(...)}],
)

# Embeddings:
r = await client.embeddings.create(model="text-embedding-3-small", input=[...])
vec = r.data[0].embedding   # list[float], 1536 dims
```

Models: `gpt-5.5` (default), `gpt-5.4-nano` (fast steps).

---

## Corrections applied

| # | Correction |
|---|---|
| 1 | `ReminderOutput` has two distinct fields: `message` (reminder text) and `confirmation` (human-readable string). No duplicate field names. |
| 2 | HNSW migration uses plain `CREATE INDEX ... USING hnsw` — no `CONCURRENTLY`. `CONCURRENTLY` cannot run inside Alembic's transaction block. |
| 3 | Engine owns the session. `handle_request` opens + commits; clients never see a session object. |
| 4 | `CoreEngine.__init__` receives `session_factory: async_sessionmaker`. `handle_request(request: CoreRequest) -> CoreResponse` — no `db` in signature. |
| 5 | `ReminderInput` contains only LLM-supplied fields (`message`, `remind_at`). `user_id` is NOT in the LLM tool schema. `ToolRegistry.execute(name, args, *, user_id, db)` injects trusted context. |
| 6 | `datetime.now(timezone.utc)` everywhere. No `utcnow()`. |
| 7 | `json.loads(item.arguments)` in `openai_provider.py`. `except json.JSONDecodeError` → raise `LLMError`. |
| 8 | System prompt includes current UTC time so relative phrases ("tomorrow", "in 2 hours") resolve correctly. |
| 9 | Cache embeddings only (`_embed_cache: dict[str, list[float]]`). No completion cache for this slice. |
| 10 | `.dockerignore` added. App `Dockerfile` installs only runtime deps (`pip install -e .`, not `[dev]`). |

---

## Files — what to build

### Unchanged from Session A (reuse as-is)
- `core/config.py` — all settings present
- `core/exceptions.py` — exception hierarchy complete
- `core/logging.py` — structlog setup complete
- `core/llm/base.py` — `LLMProvider` ABC + all schemas
- `plugins/base.py` — `PluginBase` ABC
- `models/*.py` — all column definitions correct

### Files to implement (ordered by dependency)

---

### 1. `alembic/env.py`

```python
# async run_migrations_online()
# imports all models/* to populate Base.metadata
# uses create_async_engine from settings DATABASE_URL
# context.configure(target_metadata=Base.metadata, ...)
```

---

### 2. `alembic/versions/0001_initial.py`

```sql
-- op.execute("CREATE EXTENSION IF NOT EXISTS vector")
-- CREATE TYPE memory_type_enum AS ENUM ('working','episodic','semantic','knowledge')
-- CREATE TYPE task_status_enum AS ENUM (...)
-- op.create_table for each model (users, memories, reminders, tasks, projects, files)
-- btree indexes: ix_reminders_user_id, ix_memories_user_id
-- partial index: CREATE INDEX ix_reminders_poll ON reminders (remind_at, sent_at) WHERE sent_at IS NULL
```

---

### 3. `alembic/versions/0002_hnsw_index.py`

```python
# Plain CREATE INDEX — no CONCURRENTLY (cannot run inside a transaction)
op.execute("""
    CREATE INDEX ix_memories_embedding_hnsw
    ON memories USING hnsw (embedding vector_cosine_ops)
    WITH (m=16, ef_construction=64)
""")
```

---

### 4. `docker-compose.yml`

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment: {POSTGRES_DB: agent, POSTGRES_USER: agent, POSTGRES_PASSWORD: "${DB_PASSWORD}"}
    healthcheck: {test: ["CMD", "pg_isready", "-U", "agent"], interval: 5s, retries: 10}
    volumes: [pgdata:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    healthcheck: {test: ["CMD", "redis-cli", "ping"], interval: 5s, retries: 10}

  app:
    build: {context: ., dockerfile: infra/docker/Dockerfile}
    depends_on: {db: {condition: service_healthy}, redis: {condition: service_healthy}}
    environment:
      - DATABASE_URL
      - REDIS_URL
      - OPENAI_API_KEY
      - TELEGRAM_BOT_TOKEN
      - TELEGRAM_WEBHOOK_SECRET
      - ENVIRONMENT=production
    command: >
      sh -c "alembic upgrade head &&
             uvicorn clients.api.main:app --host 0.0.0.0 --port 8000"
    ports: ["8000:8000"]

  worker:
    build: {context: ., dockerfile: infra/docker/Dockerfile.worker}
    depends_on: {db: {condition: service_healthy}, redis: {condition: service_healthy}}
    environment: [DATABASE_URL, REDIS_URL, OPENAI_API_KEY, TELEGRAM_BOT_TOKEN, ENVIRONMENT=production]
    command: python -m arq infra.worker.worker_settings.WorkerSettings

volumes: {pgdata: {}}
```

---

### 5. `infra/docker/Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir -e .
COPY . .
```

### 6. `infra/docker/Dockerfile.worker`

Same as `Dockerfile` — worker runs the same image, different `command`.

---

### 7. `.dockerignore`

```
.git
.env
__pycache__
*.pyc
.mypy_cache
.ruff_cache
.pytest_cache
tests/
docs/
```

---

### 8. `core/schemas.py`

```python
import uuid
from typing import Any
from pydantic import BaseModel, Field

class CoreRequest(BaseModel):
    user_id: uuid.UUID
    content: str
    session_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    metadata: dict[str, Any] = {}

class CoreResponse(BaseModel):
    content: str
    memories_written: int = 0
    tool_calls_made: list[str] = []
    error: str | None = None
```

---

### 9. `core/llm/openai_provider.py` — replace stub

```python
import json
from openai import AsyncOpenAI, RateLimitError, APITimeoutError, NOT_GIVEN
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type
from core.llm.base import LLMProvider, LLMMessage, LLMConfig, LLMResponse, LLMToolCall, TokenUsage
from core.exceptions import LLMRateLimitError, LLMTimeoutError, LLMError

class OpenAIProvider(LLMProvider):
    def __init__(self, *, api_key: str, default_model: str, fast_model: str,
                 timeout: float = 30.0, max_retries: int = 0) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._default_model = default_model
        self._fast_model = fast_model
        self._embed_cache: dict[str, list[float]] = {}   # embeddings only

    @retry(
        retry=retry_if_exception_type(LLMRateLimitError),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def complete(self, messages, tools, config: LLMConfig) -> LLMResponse:
        input_items = [_to_item(m) for m in messages]
        tool_defs = _to_tool_defs(tools) if tools else NOT_GIVEN
        try:
            response = await self._client.responses.create(
                model=config.model,
                input=input_items,
                tools=tool_defs,
                tool_choice=config.tool_choice,
                temperature=config.temperature,
            )
        except RateLimitError as e:
            raise LLMRateLimitError(str(e)) from e
        except APITimeoutError as e:
            raise LLMTimeoutError(str(e)) from e

        tool_calls, content = [], None
        for item in response.output:
            if item.type == "function_call":
                try:
                    args = json.loads(item.arguments)
                except json.JSONDecodeError:
                    raise LLMError(f"malformed tool arguments: {item.arguments!r}")
                tool_calls.append(LLMToolCall(id=item.call_id, name=item.name, arguments=args))
            elif item.type == "message":
                content = "".join(c.text for c in item.content if hasattr(c, "text"))

        usage = TokenUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.total_tokens,
            cached_tokens=getattr(response.usage, "cached_tokens", None),
        )
        return LLMResponse(
            response_type="tool_calls" if tool_calls else "message",
            content=content,
            tool_calls=tool_calls,
            model=response.model,
            usage=usage,
            raw_response_id=response.id,
        )

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        results: list[list[float]] = []
        uncached: list[str] = []
        uncached_idx: list[int] = []
        for i, t in enumerate(texts):
            if t in self._embed_cache:
                results.append(self._embed_cache[t])
            else:
                uncached.append(t)
                uncached_idx.append(i)
                results.append([])
        if uncached:
            r = await self._client.embeddings.create(model=model, input=uncached)
            for j, idx in enumerate(uncached_idx):
                vec = r.data[j].embedding
                self._embed_cache[uncached[j]] = vec
                results[idx] = vec
        return results

    async def stream(self, messages, tools, config): raise NotImplementedError
    async def count_tokens(self, messages) -> int: raise NotImplementedError
    def list_models(self) -> list[str]:
        return ["gpt-5.5", "gpt-5.4-nano", "gpt-5.4-mini"]


def _to_item(msg: LLMMessage) -> dict:
    if msg.role == "tool_result":
        output = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
        return {"type": "function_call_output", "call_id": msg.tool_call_id, "output": output}
    if msg.role == "assistant" and msg.tool_calls:
        return {"role": "assistant", "content": [
            {"type": "function_call", "call_id": tc.id, "name": tc.name,
             "arguments": json.dumps(tc.arguments)}
            for tc in msg.tool_calls
        ]}
    return {"role": msg.role, "content": msg.content}


def _to_tool_defs(tools) -> list[dict]:
    return [{"type": "function", "name": t.name, "description": t.description,
             "parameters": t.parameters} for t in tools]
```

---

### 10. `core/memory/manager.py`

```python
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from core.llm.base import LLMProvider
from core.config import Settings
from models.memory import Memory

class MemoryManager:
    def __init__(self, llm: LLMProvider, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings

    async def write(self, db: AsyncSession, *, user_id, content: str,
                    memory_type: str, metadata=None,
                    importance_score: float | None = None,
                    expires_at=None) -> Memory:
        vecs = await self._llm.embed([content], model=self._settings.openai_embedding_model)
        score = importance_score if importance_score is not None else _heuristic(content, memory_type)
        mem = Memory(
            user_id=user_id, content=content, embedding=vecs[0],
            memory_type=memory_type, importance_score=score,
            metadata_=metadata or {}, expires_at=expires_at,
        )
        db.add(mem)
        return mem   # engine commits

    async def semantic_search(self, db: AsyncSession, *, user_id, query: str,
                               top_k: int = 5, memory_types=None) -> list[Memory]:
        vecs = await self._llm.embed([query], model=self._settings.openai_embedding_model)
        q = select(Memory).where(Memory.user_id == user_id, Memory.embedding.is_not(None))
        if memory_types:
            q = q.where(Memory.memory_type.in_(memory_types))
        q = q.order_by(Memory.embedding.cosine_distance(vecs[0])).limit(top_k)
        result = await db.execute(q)
        rows = result.scalars().all()
        now = datetime.now(timezone.utc)
        for r in rows:
            r.last_accessed_at = now
        return rows


def _heuristic(content: str, memory_type: str) -> float:
    if "reminder" in content.lower():
        return 0.8
    if memory_type == "episodic":
        return 0.6
    return 0.5
```

`core/memory/semantic.py`, `episodic.py` — thin helpers wrapping `MemoryManager`.
`core/memory/working.py`, `knowledge.py` — stubs returning `[]`.

---

### 11. `core/tools/registry.py`

```python
import uuid
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError
from core.llm.base import LLMTool
from core.exceptions import PluginNotFoundError, PluginValidationError
from plugins.base import PluginBase

class ToolRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginBase] = {}

    def register(self, plugin: PluginBase) -> None:
        self._plugins[plugin.name] = plugin

    def get_tools_for_llm(self) -> list[LLMTool]:
        tools = []
        for plugin in self._plugins.values():
            schema = plugin.input_schema.model_json_schema()
            # user_id is never in the LLM-visible schema — it's injected by registry
            schema.get("properties", {}).pop("user_id", None)
            tools.append(LLMTool(name=plugin.name, description=plugin.description,
                                  parameters=schema))
        return tools

    async def execute(self, name: str, raw_args: dict, *,
                      user_id: uuid.UUID, db: AsyncSession) -> dict[str, Any]:
        plugin = self._plugins.get(name)
        if not plugin:
            raise PluginNotFoundError(name)
        try:
            validated = plugin.input_schema(**raw_args)
        except ValidationError as e:
            raise PluginValidationError(str(e))
        result = await plugin.execute(validated, user_id=user_id, db=db)
        return result.model_dump()
```

---

### 12. `plugins/reminders/schemas.py`

```python
import uuid
from datetime import datetime
from pydantic import BaseModel

class ReminderInput(BaseModel):
    # LLM-supplied fields ONLY — no user_id
    message: str
    remind_at: datetime   # absolute UTC datetime; LLM resolves relative → absolute using system prompt

class ReminderOutput(BaseModel):
    reminder_id: uuid.UUID
    message: str           # reminder text (copied from input)
    remind_at: datetime
    confirmation: str      # e.g. "Reminder set for 2026-07-08 09:00 UTC"

class ReminderConfig(BaseModel):
    pass
```

---

### 13. `plugins/reminders/plugin.py`

```python
import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from plugins.base import PluginBase, HealthStatus
from plugins.reminders.schemas import ReminderInput, ReminderOutput, ReminderConfig
from models.reminder import Reminder

class RemindersPlugin(PluginBase):
    name = "create_reminder"
    version = "1.0.0"
    description = "Create a reminder that fires at a specified future UTC time."
    capabilities = ["reminders"]
    permissions = ["db:write"]
    dependencies: list[str] = []
    input_schema = ReminderInput
    output_schema = ReminderOutput
    config_schema = ReminderConfig

    async def execute(self, input: ReminderInput, *,
                      user_id: uuid.UUID, db: AsyncSession) -> ReminderOutput:
        remind_at = (input.remind_at.replace(tzinfo=timezone.utc)
                     if input.remind_at.tzinfo is None else input.remind_at)
        reminder = Reminder(user_id=user_id, message=input.message, remind_at=remind_at)
        db.add(reminder)
        await db.flush()   # assigns reminder.id; engine commits the transaction
        return ReminderOutput(
            reminder_id=reminder.id,
            message=input.message,
            remind_at=remind_at,
            confirmation=f"Reminder set for {remind_at.strftime('%Y-%m-%d %H:%M UTC')}",
        )

    async def health_check(self) -> HealthStatus:
        return HealthStatus(status="healthy", message="ok",
                            checked_at=datetime.now(timezone.utc))
```

---

### 14. `core/engine.py`

```python
import structlog
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import async_sessionmaker
from core.schemas import CoreRequest, CoreResponse
from core.llm.base import LLMProvider, LLMMessage, LLMConfig
from core.memory.manager import MemoryManager
from core.tools.registry import ToolRegistry
from core.config import Settings

SYSTEM_PROMPT = (
    "You are a personal AI assistant. Today's date and time (UTC) is {now}. "
    "When the user wants to set a reminder, call the create_reminder tool with an "
    "absolute UTC datetime. Otherwise respond directly."
)

class CoreEngine:
    def __init__(self, *, llm: LLMProvider, memory: MemoryManager,
                 registry: ToolRegistry, session_factory: async_sessionmaker,
                 settings: Settings) -> None:
        self._llm = llm
        self._memory = memory
        self._registry = registry
        self._session_factory = session_factory
        self._settings = settings

    async def handle_request(self, request: CoreRequest) -> CoreResponse:
        log = structlog.get_logger().bind(user_id=str(request.user_id),
                                          session_id=str(request.session_id))
        async with self._session_factory() as db:
            try:
                result = await self._process(request, db, log)
                await db.commit()
                return result
            except Exception:
                await db.rollback()
                raise

    async def _process(self, request: CoreRequest, db, log) -> CoreResponse:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        llm_resp = await self._llm.complete(
            messages=[
                LLMMessage(role="system", content=SYSTEM_PROMPT.format(now=now_str)),
                LLMMessage(role="user", content=request.content),
            ],
            tools=self._registry.get_tools_for_llm(),
            config=LLMConfig(model=self._settings.openai_default_model),
        )

        tools_called: list[str] = []
        result_content = ""

        if llm_resp.response_type == "tool_calls":
            for tc in llm_resp.tool_calls:
                tools_called.append(tc.name)
                out = await self._registry.execute(
                    tc.name, tc.arguments, user_id=request.user_id, db=db)
                result_content = out.get("confirmation") or out.get("message", str(out))
                log.info("tool.executed", tool=tc.name)
        else:
            result_content = llm_resp.content or ""

        await self._memory.write(
            db,
            user_id=request.user_id,
            content=f"User: {request.content}\nAssistant: {result_content}",
            memory_type="episodic",
            metadata={"session_id": str(request.session_id), "tools": tools_called},
        )
        return CoreResponse(content=result_content, memories_written=1,
                            tool_calls_made=tools_called)
```

---

### 15. `clients/api/main.py`

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from core.config import get_settings
from core.logging import configure_logging
from core.llm.openai_provider import OpenAIProvider
from core.memory.manager import MemoryManager
from core.tools.registry import ToolRegistry
from core.engine import CoreEngine
from plugins.reminders.plugin import RemindersPlugin
from clients.api.routes.reminders import router as reminders_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, s.environment)
    engine = create_async_engine(str(s.database_url), pool_size=s.db_pool_size)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = OpenAIProvider(
        api_key=s.openai_api_key.get_secret_value(),
        default_model=s.openai_default_model,
        fast_model=s.openai_fast_model,
    )
    memory = MemoryManager(llm=llm, settings=s)
    registry = ToolRegistry()
    registry.register(RemindersPlugin())
    core = CoreEngine(llm=llm, memory=memory, registry=registry,
                      session_factory=factory, settings=s)
    app.state.engine = core
    yield
    await engine.dispose()

app = FastAPI(title="Personal AI Platform", lifespan=lifespan)
app.include_router(reminders_router, prefix="/v1")

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

---

### 16. `clients/api/routes/reminders.py`

```python
# POST /v1/reminders
#   body: {content: str, user_id: uuid}
#   → CoreRequest → engine.handle_request → CoreResponse JSON
#
# GET /v1/reminders/{user_id}
#   → direct DB query (no engine), returns list of Reminder rows
```

---

### 17. `core/notifications/telegram_notifier.py`

```python
import httpx

class TelegramNotifier:
    def __init__(self, bot_token: str,
                 http_client: httpx.AsyncClient | None = None) -> None:
        self._token = bot_token
        self._client = http_client   # injected for tests; created at worker startup if None

    async def send(self, telegram_id: int, message: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        r = await self._client.post(
            url, json={"chat_id": telegram_id, "text": message}, timeout=10.0)
        r.raise_for_status()
```

---

### 18. `core/scheduler/jobs.py`

```python
import structlog
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from models.reminder import Reminder
from models.user import User
from core.notifications.telegram_notifier import TelegramNotifier

log = structlog.get_logger()

async def poll_reminders(ctx: dict) -> None:
    session_factory: async_sessionmaker = ctx["session_factory"]
    notifier: TelegramNotifier = ctx["notifier"]
    now = datetime.now(timezone.utc)

    async with session_factory() as db:
        result = await db.execute(
            select(Reminder)
            .where(Reminder.remind_at <= now, Reminder.sent_at.is_(None))
            .with_for_update(skip_locked=True)
        )
        for reminder in result.scalars().all():
            user = await db.get(User, reminder.user_id)
            if user and user.telegram_id:
                try:
                    await notifier.send(user.telegram_id, reminder.message)
                except Exception:
                    log.warning("notify.failed", reminder_id=str(reminder.id))
                    continue
            reminder.sent_at = now
        await db.commit()
```

---

### 19. `infra/worker/worker_settings.py`

```python
from arq import cron
from arq.connections import RedisSettings
from core.config import get_settings
from core.scheduler.jobs import poll_reminders

async def startup(ctx: dict) -> None:
    s = get_settings()
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import httpx
    from core.notifications.telegram_notifier import TelegramNotifier
    engine = create_async_engine(str(s.database_url))
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)
    ctx["http_client"] = httpx.AsyncClient()
    ctx["notifier"] = TelegramNotifier(
        bot_token=s.telegram_bot_token.get_secret_value(),
        http_client=ctx["http_client"],
    )

async def shutdown(ctx: dict) -> None:
    await ctx["http_client"].aclose()

class WorkerSettings:
    functions = [poll_reminders]
    cron_jobs = [cron(poll_reminders, second={0})]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    max_jobs = 10
    job_timeout = 55
```

---

### 20. `clients/telegram/bot.py` + `handlers.py`

```python
# bot.py: create Dispatcher, register handlers, set bot.data["engine"] = core
# handlers.py:
#   ONLY imports: aiogram types, core.schemas.CoreRequest, core.schemas.CoreResponse
#   @router.message() handler:
#     → CoreRequest(user_id=..., content=msg.text)
#     → response = await engine.handle_request(request)
#     → await message.answer(response.content)
```

---

## Tests

### Unit tests (no Docker — always run on VM)

**`tests/core/llm/test_openai_provider.py`**
- `test_complete_message`: mock `responses.create` → message item → `LLMResponse(response_type="message")`
- `test_complete_tool_call`: mock → `function_call` item with JSON string args → `LLMResponse(response_type="tool_calls", tool_calls=[LLMToolCall(arguments=dict)])`
- `test_complete_malformed_args`: non-JSON `item.arguments` → raises `LLMError`
- `test_complete_rate_limit_retry`: `RateLimitError` twice then succeeds → assert called 3× (tenacity)
- `test_embed_cache`: embed same text twice → `embeddings.create` called once
- `test_embed_batch`: two different texts → single API call, two results

**`tests/plugins/test_reminders.py`**
- `test_execute_creates_reminder`: mock `db`; call `plugin.execute(ReminderInput(...), user_id=..., db=mock_db)` → `db.add` called with `Reminder`, `ReminderOutput.confirmation` contains datetime string, `ReminderOutput.message` is reminder text
- `test_output_fields_distinct`: `ReminderOutput.model_fields` contains `message` and `confirmation` (not duplicates)
- `test_health_check_healthy`: returns `HealthStatus(status="healthy")`

**`tests/core/test_memory.py`**
- `test_write_embeds_and_stores`: mock `llm.embed` → `[[0.1]*1536]`; assert `Memory.embedding == [0.1]*1536`
- `test_heuristic_reminder`: content with "reminder" → `importance_score >= 0.7`
- `test_heuristic_episodic`: episodic without "reminder" → score == 0.6
- `test_semantic_search_embed_once`: assert embed called once for query, not once per result

**`tests/core/test_engine.py`**
- `test_handle_request_tool_call`: mock LLM returns `create_reminder` tool call; mock registry; assert `CoreResponse.tool_calls_made == ["create_reminder"]`
- `test_handle_request_message`: mock LLM returns message; assert `CoreResponse.content` set, memory written
- `test_handle_request_commits`: assert `db.commit()` called on success
- `test_handle_request_rollback`: mock registry raises; assert `db.rollback()` called

**`tests/core/test_scheduler.py`**
- `test_poll_sends_due_reminder`: past-due reminder + user with telegram_id → `notifier.send` called with right args, `sent_at` set
- `test_poll_skips_future`: `remind_at` = now + 1h → `notifier.send` NOT called
- `test_poll_skips_already_sent`: `sent_at` already set → NOT called
- `test_poll_skips_no_telegram_id`: user.telegram_id is None → notifier not called, `sent_at` still set

### Integration test (requires Docker)

**`tests/integration/test_full_flow.py`**

```python
pytestmark = pytest.mark.integration

# conftest: detect Docker, auto-skip integration tests if unavailable
# PostgresContainer("pgvector/pgvector:pg16") → run alembic upgrade head

async def test_reminder_round_trip(pg_session_factory, mock_llm, mock_notifier):
    # mock_llm.complete returns tool_call for create_reminder (tomorrow's datetime)
    # mock_llm.embed returns [0.0] * 1536
    # mock_notifier is AsyncMock TelegramNotifier

    # 1. Insert User with telegram_id into real DB
    # 2. engine.handle_request(CoreRequest(user_id=..., content="remind me tomorrow to call Bob"))
    # 3. Assert CoreResponse.tool_calls_made == ["create_reminder"]
    # 4. Assert Memory row exists (memory_type="episodic", content contains "call Bob")
    # 5. Assert Reminder row exists (sent_at is None)
    # 6. Set reminder.remind_at = now - 1s; commit
    # 7. poll_reminders(ctx={session_factory: ..., notifier: mock_notifier})
    # 8. Assert mock_notifier.send called once with (user.telegram_id, reminder.message)
    # 9. Reload reminder; assert sent_at is not None
```

**`conftest.py`** additions:
```python
def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Docker + testcontainers")

def pytest_collection_modifyitems(config, items):
    # auto-skip integration-marked tests when Docker is unavailable
    if not _docker_available():
        skip = pytest.mark.skip(reason="Docker not available")
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip)
```

---

## Implementation order

```
1.  alembic/env.py + 0001_initial + 0002_hnsw
2.  docker-compose.yml + Dockerfiles + .dockerignore
3.  core/schemas.py
4.  core/llm/openai_provider.py
5.  core/memory/manager.py + semantic.py + stubs
6.  core/tools/registry.py
7.  plugins/reminders/schemas.py + plugin.py
8.  core/engine.py
9.  clients/api/main.py + /health
10. clients/api/routes/reminders.py
11. core/notifications/telegram_notifier.py
12. core/scheduler/jobs.py
13. infra/worker/worker_settings.py
14. clients/telegram/bot.py + handlers.py
15. tests/ (unit tests written alongside each component)
16. tests/integration/test_full_flow.py
17. docs/DIARY.md entry
```

---

## Definition of Done

```bash
# Quality gate (must pass on VM before declaring done):
ruff check . --fix
black .
mypy .
pytest tests/ -m "not integration" -v   # all unit tests green

# Integration (graceful skip if no Docker):
pytest tests/ -m integration -v

# Docker smoke test:
docker compose up --build -d
curl http://localhost:8000/health        # → {"status": "ok"}
docker compose logs worker | grep -i cron   # → cron job registered
```

---

## Design constraints summary

| Rule | Enforcement |
|---|---|
| No `openai` import outside `openai_provider.py` | Single adapter file |
| Clients import only `core.schemas` | `handlers.py` has no other core imports |
| Plugins never import each other | `reminders/plugin.py` has no plugin imports |
| No hardcoded secrets | All from `Settings`; `OPENAI_API_KEY` is `SecretStr` |
| `user_id` not in LLM tool schema | Injected by `ToolRegistry.execute(..., user_id=...)` |
| `datetime.now(timezone.utc)` everywhere | No `utcnow()` |
| Engine owns commit/rollback | `handle_request` opens session, commits or rolls back |
| `ReminderOutput` has distinct `message` + `confirmation` | No duplicate field names |
| HNSW migration without `CONCURRENTLY` | Plain `CREATE INDEX ... USING hnsw` |
| App Docker image has no dev deps | `pip install -e .` only |
