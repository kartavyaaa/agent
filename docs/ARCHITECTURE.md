# Architecture

## Overview

The platform is an intelligent personal AI backend. It remembers, reasons, plans, researches, uses tools, and can act autonomously. All intelligence lives in the Core Engine. Clients (Telegram, REST API, CLI, and eventually web/mobile/voice) are thin translators with no business logic.

---

## Module Map and Request Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                          CLIENTS (thin)                          │
│  Telegram bot │  FastAPI REST  │  CLI    │  (Web BFF — future)  │
│  (no logic)   │  (routes only) │ (Typer) │                      │
└───────┬───────┴───────┬────────┴───┬─────┘
        └───────────────▼────────────┘
                   Core Engine
              (core/engine.py)
              Validates request, loads user context,
              invokes Planner, manages response
                        │
              ┌─────────▼──────────┐
              │   ReAct Planner     │
              │ (core/planner/      │
              │  react.py)          │
              │ Loop:               │
              │  1. LLM call        │
              │  2. Parse tool call │
              │  3. Execute tool    │
              │  4. Observe result  │
              │  5. Repeat or done  │
              └──┬──────────┬───────┘
                 │          │
        ┌────────▼──┐  ┌────▼────────────┐
        │LLMProvider│  │  Tool Registry  │
        │  (seam)   │  │(core/tools/     │
        │ base.py   │  │ registry.py)    │
        └────┬──────┘  └────┬────────────┘
             │               │
    OpenAIProvider      ┌────▼──────────────────────────────┐
    (Responses API      │           Plugins                  │
     gpt-5.5 default)   │  web_search │ reminders │ file_reader│
                        │  (REAL)     │ (REAL)    │ (REAL)    │
                        │  + stubs (calculator, weather, …)  │
                        └─────┬──────────────────────────────┘
                              │
                     ┌────────▼────────┐
                     │  Integrations   │
                     │  (thin wrappers)│
                     │  Serper, GCal,  │
                     │  local FS       │
                     └────────────────┘

       ┌────────────────────────────────────────────┐
       │             Memory Manager                  │
       │  (core/memory/manager.py)                  │
       │                                             │
       │  working    │ episodic │ semantic │ knowledge│
       │  (Redis TTL)│ (PG)     │ (pgvect) │ (PG)    │
       │                                             │
       │  Embedding: text-embedding-3-small (1536d) │
       └────────────────────────────────────────────┘

       ┌────────────────────────────────────────────┐
       │          Background / Async Layer           │
       │  arq Worker (infra/worker/worker_settings)  │
       │  ├── poll_reminders cron (every 60 s)      │
       │  ├── memory_consolidation cron (hourly)    │
       │  └── Notification Engine                   │
       │       core/notifications/telegram_notifier  │
       └────────────────────────────────────────────┘

       ┌────────────────────────────────────────────┐
       │               Infra                         │
       │  Oracle ARM VM ─── Caddy (HTTPS, auto TLS) │
       │  ├── FastAPI (uvicorn)                      │
       │  ├── arq worker (separate process)          │
       │  ├── Postgres + pgvector extension          │
       │  └── Redis (arq broker + working memory)   │
       └────────────────────────────────────────────┘
```

---

## Synchronous Request Flow (Happy Path)

1. **Client** receives raw input (Telegram message, HTTP POST, CLI arg).
   Constructs `CoreRequest(user_id, content, session_id)`.
   Calls `core.engine.handle_request(request)`. Nothing else.

2. **Core Engine** (`core/engine.py`)
   - Loads user preferences from DB.
   - Asks `MemoryManager` to retrieve working + relevant episodic context.
   - Assembles `ConversationContext`.
   - Delegates to `ReActPlanner`.

3. **ReAct Planner** (`core/planner/react.py`)
   Loop (max `settings.planner_max_iterations`):
   - Build `input[]` array for Responses API.
   - Call `LLMProvider.complete(messages, tools, config)`.
   - If response is a final message → exit loop.
   - If response contains tool calls → dispatch each via `ToolRegistry`.
   - Append tool results as `tool_result` items. Repeat.
   - Stuck-loop detection: same tool + identical args N times → `PlannerStuckLoopError`.

4. **Tool Registry** (`core/tools/registry.py`)
   Routes tool calls to the correct `PluginBase` subclass.
   Validates input against `plugin.input_schema`, validates output against `plugin.output_schema`.

5. **Plugins** (`plugins/`)
   Stateless workers. May call `integrations/` for external API access.
   Stubs raise `PluginNotImplementedError`.

6. **Memory write** (async, after final answer)
   - Working memory: Redis TTL (session context).
   - Episodic: significant turns persisted to DB.
   - Semantic/knowledge: arq background job scores and embeds.

7. **Response**
   Engine returns `CoreResponse`. Client renders it for its transport.

---

## Background Notification Flow

```
arq cron: poll_reminders() — every 60 seconds
  │
  ├── SELECT reminders WHERE remind_at <= now() AND sent_at IS NULL
  ├── For each due reminder:
  │    ├── Look up user.telegram_id
  │    ├── TelegramNotifier.send(telegram_id, message)
  │    │    └── POST api.telegram.org/bot{token}/sendMessage (httpx)
  │    └── UPDATE reminder SET sent_at = now()
  └── (recurrence: if recurrence set, create next reminder row)
```

---

## Boundary Rules

| Layer | Rule |
|---|---|
| **Clients** | Zero business logic. Import only `core.schemas` types. Translate transport ↔ `CoreRequest`/`CoreResponse`. |
| **Core Engine** | Sole entry point for client requests. Owns the planner loop and memory lifecycle. |
| **Plugins** | Stateless. No DB session. No cross-plugin imports. Communicate only via the engine/registry. |
| **Integrations** | Thin HTTP wrappers. No business logic. Only plugins call integrations. |
| **Models** (`models/`) | SQLAlchemy column definitions only. No methods, no logic. |
| **LLMProvider seam** | Every model call goes through `core/llm/base.py`. `openai` imported only in `core/llm/openai_provider.py`. |
| **No product name** | Refer to "the platform" or "the Core Engine" in all code and docs. |

---

## Key Files

| File | Role |
|---|---|
| `core/engine.py` | Orchestrator — sole entry point |
| `core/planner/react.py` | ReAct loop — most complex stateful logic |
| `core/llm/base.py` | LLMProvider seam — central dependency |
| `core/llm/openai_provider.py` | Only `openai` import site |
| `core/memory/manager.py` | All 4 memory layers |
| `core/tools/registry.py` | Plugin dispatch |
| `plugins/base.py` | Plugin contract |
| `core/config.py` | All configuration |
| `core/exceptions.py` | Exception hierarchy |
| `core/logging.py` | Structlog setup |
