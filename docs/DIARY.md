# Development Diary

---

## 2026-07-15 — Slice: Markdown rendering via entity path (telegramify-markdown)

**What was built:**

- `clients/telegram/formatters.py`: Fully replaced the HTML-escape + split approach with
  `telegramify_markdown.convert()` + `telegramify_markdown.split_entities()` — the library's
  entity path. `format_response(content)` now returns `list[tuple[str, list[MessageEntity]]]`
  instead of `list[str]`. The text is plain UTF-8; all formatting (bold, italic, code, links)
  is carried as structured `aiogram.types.MessageEntity` objects. No MarkdownV2 escaping step
  anywhere — "can't parse entities" failures are structurally impossible. Private helper
  `_to_aiogram(lib_entity)` converts the library's `MessageEntity` to aiogram's via
  `model_validate(lib_entity.to_dict())`.
- `clients/telegram/handlers.py`: Updated the send loop to unpack `(chunk_text, chunk_entities)`
  and call `message.answer(chunk_text, entities=chunk_entities, parse_mode=None)`. Added
  `_FALLBACK = "(No response.)"` — if `format_response` returns `[]` (empty/whitespace LLM
  output), sends the fallback rather than making no reply or passing empty text (Telegram
  rejects empty text with 400).
- `clients/telegram/bot.py`: Removed `parse_mode=ParseMode.MARKDOWN_V2` from
  `DefaultBotProperties` (and dropped the now-unused `ParseMode` import). Handlers pass
  `parse_mode=None` per-call, so the bot-level default is irrelevant and its presence was
  confusing.
- `pyproject.toml`: `telegramify-markdown>=1.0.0` added to production dependencies.
- `tests/clients/test_telegram_formatters.py`: Fully rewritten. Old HTML-escaping tests
  removed. New entity-contract tests: bold → entity with type="bold", inline code → type="code",
  code block → type="pre" + language="python", plain text no bold/italic entities, bullet list
  text preserved, `_to_aiogram` field preservation, empty/whitespace → `[]`, UTF-16 chunk limit
  enforced, entity offsets within chunk bounds. `pytest.importorskip("telegramify_markdown")` at
  top — VM skips since lib can't be installed there.
- `tests/clients/test_telegram_handlers.py`: Updated two assertions for the new call signature
  (`call_args.args[0]` for text, `call_args.kwargs.get("parse_mode") is None`, UTF-16 length
  check). Added two new tests: `test_empty_response_sends_fallback` and
  `test_whitespace_response_sends_fallback` — verify `"(No response.)"` sent, never empty string.

**Why entity path over MarkdownV2 string path:**
The first implementation (committed in the prior session) used `telegramify_markdown.telegramify()`
and extracted `.content` — but `.content` is deprecated. More importantly, the MarkdownV2
string path has an unavoidable escaping layer: any character that needs escaping must be
prefixed with `\`, and if the split or the library gets the escaping wrong, Telegram rejects
the whole message. The entity path has no escaping: Telegram reads the plain text as-is and
uses the entity objects to know where to apply bold, code, etc. No escaping = no escape bugs.

**Empty-content bug caught in plan review:**
Initial plan returned `[("", [])]` for empty content — the handler would then call
`message.answer("", ...)` which Telegram rejects with 400. Corrected to return `[]` and handle
in the handler with a fallback message.

**VM-dep note:**
`telegramify-markdown` cannot be installed on the VM (TLS restriction). The formatter and
handler tests skip via `pytest.importorskip`. All 156 other tests pass. PC-gate is required:
`pip install -e ".[dev]"` then `pytest -v` (the 14 formatter + 8 handler tests must all pass).

**Key invariant:**
`format_response` must never be called with already-escaped text. It takes raw LLM Markdown
output and the library handles all encoding internally. The calling chain is:
`engine response.content` → `format_response()` → `convert()` → `split_entities()` → aiogram send.

**Quality gate (VM, provisional):** ruff ✓ · black ✓ (3.11 AST check degraded — expected) ·
mypy ✓ (116 source files, 0 errors) · pytest 156/156 unit ✓ · 3 skipped (formatter/handler
lib-absent) · 3 deselected (integration).

**Authoritative run PENDING on PC:**
1. `pip install -e ".[dev]"` — picks up `telegramify-markdown>=1.0.0`.
2. Probe actual API: `python -c "import telegramify_markdown as t; text, ents = t.convert('**bold**'); print(repr(text), ents[0].to_dict())"` — confirm `.to_dict()` keys match aiogram's `MessageEntity` fields and adjust `_to_aiogram` if needed.
3. `pytest -v` — all tests green including the 14 previously-skipped formatter tests.
4. `docker compose up --build` — boots, `/health` → 200.
5. **Live Telegram test (the only real rendering proof):** deploy to Oracle VM, send bot a message that produces bold + a list + a code block, confirm each renders (not literal markers). Then send something long enough to split; confirm no "can't parse entities" error and no garbled chunks.

---

## 2026-07-14 — Phase 4: DEPLOYED to production (Option 4: E2.1.Micro + Neon + swap)

**Outcome:** Agent is live. Running 24/7 on a free Oracle Always-Free VM, backed by Neon (managed
Postgres), reachable from phone via Telegram, self-healing across reboots, locked down (SSH-in
only, API not internet-exposed). This is the milestone the project aimed at.

**The hosting journey (why Option 4):**

- **Oracle ARM (Ampere A1)** was the original target (stack runs unchanged on a VM). Blocked by
  repeated "Out of host capacity" across ADs. PAYG (the reliable capacity fix) demanded a
  ~138 SGD deposit in this region → rejected as not-free.
- **Free PaaS tiers evaluated and rejected:** Render (sleeps 15min), Koyeb (sleeps 1hr + one
  service only + no worker services), Railway (free tier is a trial), Fly.io (no free tier).
  Root cause: the stack is 5 always-on processes; free PaaS is built for one sleepable web
  service. The architecture wants a VM.
- **Option 3 (re-architect to fit free PaaS: drop arq/Redis, in-process scheduler, webhook so
  sleep is acceptable)** was fully planned but shelved in favor of the simpler Option 4.
- **Option 4 (chosen): Oracle AMD E2.1.Micro (x86, 1GB) + Postgres offloaded to Neon + swap.**
  The E2.1.Micro provisions reliably (no ARM capacity fight). Offloading memory-hungry Postgres
  to Neon makes the 1GB box comfortable; the stack otherwise runs UNCHANGED (no re-architecture).

**VM specs (agent-prod):** Oracle VM.Standard.E2.1.Micro, Ubuntu 24.04, x86_64, 2 vCPU, 954MB RAM,
45GB disk. Added 2GB swap (/swapfile, persisted via /etc/fstab). Docker Engine 29.6.1 + Compose
v5.3.1 (get.docker.com; enabled on boot via systemd).

**Deploy facts / gotchas:**

- Memory footprint on the micro: app ~54MB, bot ~178MB, worker ~41MB, redis ~4MB = ~277MB of
  954MB (~29%), swap untouched. The 1GB box is comfortable once Postgres is on Neon. The 2 vCPUs
  (not the feared 1/8 OCPU) also make builds tolerable.
- Deploy flow: prod `.env` lives ONLY on the VM (gitignored, never in repo); `git pull` never
  touches it. Cloned repo directly from GitHub (VM reaches GitHub — no SharePoint for prod).
  `mkdir -p data/files` needed before `up` (bind-mount target).
- Brought up with `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
  (MUST use --build — images bake code at build time).
- **Secrets rotated:** `docker compose config` dumped all env values in plaintext during testing
  (it's a secret-dumping command by nature). Rotated OpenAI/Serper/Telegram keys afterward.
- **Firewall: already locked down by Oracle default.** Ingress allows only SSH (22) + two required
  ICMP rules (Path MTU Discovery — leave them). NO rule for 8000/5432/6379, so the API and Redis
  are NOT internet-exposed (Oracle is default-deny inbound; publishing 8000 on the Docker
  interface is irrelevant without a matching ingress rule). Egress = all (needed for
  bot→Telegram/OpenAI/Serper/Neon). Nothing to change. Did NOT touch host ufw/iptables
  (Oracle-Ubuntu iptables interaction is a lock-out risk; the cloud security list suffices). SSH
  left open to 0.0.0.0/0 — key-auth is the real protection and the home IP is dynamic (IP
  restriction would risk lockout).
- **Reboot-survival verified:** `sudo reboot` → all 4 services returned automatically (restart
  policies + Docker-on-boot). Self-healing confirmed. Bot answered from phone after reboot.

**Neon caveat to monitor:** free tier = 0.5GB storage + 100 compute-hrs/month, scale-to-zero. The
every-minute reminder poll keeps Neon awake → watch compute-hour burn; relax poll interval if needed.

**Known gaps / queue (post-deploy):**

1. **MEMORY RECALL (highest value, next slice):** agent WRITES memories but never READS them back
   into context. Each Telegram message is an independent `handle_request` with no conversation
   history injected, and semantic memory search is not wired into the request flow. Result: agent
   can't remember your name even within one chat. The write path + storage exist; the
   retrieve-into-context path was never built.
2. **Telegram allowlist (tiny):** restrict bot to own telegram_id so randoms can't spend API credits.
3. **Phase 5 auth:** only needed if/when the REST API is exposed publicly (currently not exposed).
4. **Serper:** 2500 free credits; when exhausted, web_search fails gracefully (typed integration
   error; everything else keeps working). Paid tier is cheap if ever needed.
5. **Cleanup:** Markdown→Telegram-HTML rendering, working/knowledge memory layers, tasks/projects routes.

---

## 2026-07-13 — Slice: bot-as-a-service + prod compose override (Option 4 deploy prep)

**What was built:**

- `docker-compose.yml`: added `bot` service. Uses same `infra/docker/Dockerfile` image as `app`.
  Command is `python -m clients.telegram` — no migration step, no shell wrapper. Long-polling loop
  (`aiogram dp.start_polling`) keeps the container alive. Env block mirrors `app` exactly.
  No restart policy in base (local dev doesn't need it).
- `docker-compose.prod.yml`: new override for 1GB VM + Neon Postgres. Disables `db` via
  `profiles: [donotstart]`. Adds `restart: unless-stopped` to redis/app/worker/bot. Nulls out
  `db` from each service's `depends_on` using `db: ~` — critical because Compose merges `depends_on`
  maps and omitting a key does NOT remove it.

**Key insight — depends_on override mechanics:**
Compose v2 merges `depends_on` maps by key. To remove the `db` dependency in the prod override
you must explicitly set `db: ~` (YAML null) for each service. A plain redeclaration listing only
`redis` would silently merge and keep `db`, causing a startup error against the profiled-off db
service.

**Key insight — profiles + depends_on interaction:**
The `db: ~` null in `depends_on` removes the dependency before Compose evaluates whether the
profiled service is active. Run `docker compose -f docker-compose.yml -f docker-compose.prod.yml config`
first to confirm the merged output shows no `db` under any service's `depends_on`. If that
command errors on the profile/depends_on interaction, fallback is to drop the `profiles` block
from the override's `db` entry (the `db: ~` nulling is what actually matters for correctness).

**DB_PASSWORD on prod host:** `docker-compose.yml` uses `${DB_PASSWORD}` (no default). Compose
emits a warning (not an error) if unset on the prod host — safe to ignore since `db` doesn't start.

**Correction — depends_on removal via merge tricks is unreliable (docker/compose #11980, #12162):**
`db: ~` null-on-merge and `!reset` both failed in practice — `config` errored with "service X
depends on undefined service db". Fix 1 applied instead: add `required: false` to the `db`
entry in `depends_on` in the base file for app/worker/bot. With `required: false`, Compose
silently skips the dependency when `db` is not present (profiled off) rather than erroring.
The prod override is now clean — it only sets `profiles: [donotstart]` on db and restart
policies; no depends_on manipulation needed in the override at all. Local dev is unaffected:
db is present and healthy, so `required: false` is a no-op and Compose waits for it normally.

**Deferred to PC for authoritative verification:**
- `docker compose ... config` pre-flight to validate merged profile/depends_on output.
- Local `docker compose up` with 5 services (db+redis+app+worker+bot) all Up.
- Prod `docker compose -f ... -f ... up` with DATABASE_URL=Neon: no db container, alembic
  no-ops, /health 200, /v1/chat works, bot responds from phone, restart policies confirmed.

---

## 2026-07-13 — Slice: user timezone (display + interpretation)

**What was built:**

- `core/timeutil.py`: `format_local(dt_utc, tz_name) -> str` — single display helper. Converts a
  UTC datetime to the named IANA zone via stdlib `zoneinfo`. Accepts naive datetimes (assumes UTC).
  Falls back to UTC on invalid `tz_name` (catches `ZoneInfoNotFoundError` and `KeyError`). Format:
  `"YYYY-MM-DD HH:MM ZZZ"` (e.g. `"2026-07-14 05:30 IST"`).
- `core/config.py`: `default_timezone: str = "Asia/Kolkata"` added to `Settings`.
- `pyproject.toml`: `tzdata>=2024.1` added to production dependencies — required by `zoneinfo` on
  Windows dev machines and slim Linux containers that lack a system IANA database.
- `core/engine.py`: `_SYSTEM_PROMPT` updated. The LLM is now told the user's timezone name and
  local time, and is explicitly instructed to interpret user times in that zone but emit `remind_at`
  as absolute UTC ISO. The naive=UTC fallback in the reminders plugin stays correct because the
  contract guarantees the LLM always emits UTC.
- `plugins/reminders/plugin.py`: `__init__(self, tz_name: str = "UTC")` added. Confirmation
  message now calls `format_local(remind_at, self._tz_name)` instead of hardcoding "UTC". Plugin
  never calls `get_settings()` — timezone is injected at wiring time.
- `clients/wiring.py`: `RemindersPlugin(tz_name=s.default_timezone)` — single wiring point.
- `clients/api/routes/memories.py`: `MemoryRow` gains `created_at_local`, `last_accessed_at_local`,
  `expires_at_local` string fields (default `""`/`None`). Route body calls `get_settings()` once
  per request (lru_cache singleton) and populates them via `format_local`.
- `clients/api/routes/reminders.py`: `ReminderRow` gains `remind_at_local`, `sent_at_local`,
  `created_at_local` string fields. Same route-body population pattern.
- `docker-compose.yml`: `DEFAULT_TIMEZONE=${DEFAULT_TIMEZONE:-Asia/Kolkata}` added to both `app`
  and `worker` environment blocks.
- `.env.example`: `DEFAULT_TIMEZONE=Asia/Kolkata` placeholder added.
- New `tests/core/test_timeutil.py`: 5 unit tests covering UTC→IST, date rollover (22:00 UTC →
  next-day IST), naive input, invalid zone fallback, UTC identity.
- Updated `tests/core/test_engine.py`: `mock_settings.default_timezone = "Asia/Kolkata"` added
  to `_make_engine()`; `test_system_prompt_contains_utc_time` renamed to
  `test_system_prompt_contains_timezone_info` and updated to assert timezone name and local-format
  timestamp appear in the system message.
- Updated `tests/plugins/test_reminders.py`: all `RemindersPlugin()` → `RemindersPlugin(tz_name="UTC")`;
  new `test_execute_confirmation_shows_local_time` asserts UTC 09:00 → IST 14:30 in confirmation.
- Updated `tests/clients/test_api_routes.py`: `autouse` fixture patches
  `clients.api.routes.memories.get_settings` and `clients.api.routes.reminders.get_settings` so
  tests don't need a real `.env`.

**What failed and why:**

- First `core/timeutil.py` draft used `ZoneInfo | type[UTC]` as the type annotation for `tz`.
  mypy rejected it — `UTC` is a `timezone` instance, not a type alias. Fixed to `ZoneInfo | timezone`.
- `tests/core/test_engine.py` system-prompt test had an inline `import re` and a ternary that made
  mypy infer `str | list[...]` for `system_msg.content`. Fixed by hoisting `import re` to the top
  and using an explicit `content: str` local.
- `test_memories_happy_path` failed at runtime because the route now calls `get_settings()`, which
  tries to parse `.env` (absent in the test environment). Fixed with an `autouse` pytest fixture that
  patches both route modules' `get_settings` references.
- `patch_get_settings` fixture initially typed as `pytest.MonkeyPatch` with `# type: ignore` —
  mypy flagged unused ignores. Fixed to `Generator[None, None, None]`.

**Key design decisions:**

- **Plugin injection over global config**: `RemindersPlugin` takes `tz_name` in `__init__`, never
  calls `get_settings()`. This means tests need no patching — just pass `tz_name="UTC"`.
- **Route-body formatting, not model validator**: `get_settings()` is called in the route function
  body after `model_validate`, not in a Pydantic `model_validator`. Keeps config-fetching out of
  the data schema layer.
- **Add-local-field approach**: raw UTC ISO fields in `MemoryRow`/`ReminderRow` are unchanged —
  API consumers relying on them are unaffected. `*_local` string fields are additive.
- **`tzdata` is a hard dependency**: Without it, `ZoneInfo("Asia/Kolkata")` raises on Windows and
  slim containers. Added to production deps, not just dev.

**Deferred — requires PC + live LLM:**

- **Live smoke test**: Send "remind me at 9am tomorrow" via bot/POST. Verify `remind_at` in DB is
  `03:30 UTC` (9am IST - 5:30h), NOT `09:00 UTC`. Confirmation message must read IST. GET
  /v1/reminders must return `remind_at_local` in IST. This is the off-by-5:30 bug; mocked unit
  tests cannot catch it — only the stored UTC value vs. intended local time does.
- **`%Z` abbreviation on PC + container**: `strftime("%Z")` should render "IST" but may render
  "+0530" on some Windows builds. Verify with `format_local(datetime(2026,7,14,0,0,tzinfo=UTC),
  "Asia/Kolkata")` == `"2026-07-14 05:30 IST"` on the PC and inside the Docker container. If
  it renders an offset, switch `abbr = local.strftime("%Z")` to `abbr = local.tzname() or "UTC"`.

---

## 2026-07-13 — Slice 3d: REST chat route, memories read route, error handlers

**What was built:**

- `clients/user_helper.py`: `get_or_create_user(db, user_id)` — race-safe upsert using
  `INSERT … ON CONFLICT DO NOTHING` (postgresql dialect). Two-select pattern handles the race
  where concurrent requests arrive with the same new `user_id`: both execute the conflict-safe
  insert, both read back the row. Does not commit — caller owns the session lifecycle. No FastAPI
  coupling; reusable from any async context including the Telegram client.
- `core/engine.py`: `handle_request()` now calls `get_or_create_user(db, request.user_id)`
  inside the engine's own session before `_process()` runs. This satisfies FK constraints on
  `memories.user_id` and `reminders.user_id` in the same transaction — no more 500 on first
  contact. Added a public `session_factory` property to eliminate private `_session_factory`
  access from routes.
- `clients/api/dependencies.py`: `get_session_factory(engine)` — canonical helper using the
  public property, so all new routes avoid underscore access.
- `clients/api/error_handlers.py`: Single `platform_error_handler` covering all `PlatformError`
  subclasses via isinstance chain (most-specific first). `SandboxViolationError` → 403 with
  path suppressed in both response and logs. All 4xx/5xx return `{error, detail}` JSON with
  safe, non-leaking messages. Registered via `register_error_handlers(app)`.
- `clients/api/routes/chat.py`: `POST /v1/chat` — pure thin translator. Body: `{user_id,
  content}`. Builds `CoreRequest`, calls `engine.handle_request()`, returns `CoreResponse`.
  Zero DB access in the route.
- `clients/api/routes/memories.py`: `GET /v1/memories?user_id=…&memory_type=…&limit=…` — direct
  DB read (no engine, no LLM). `MemoryRow` response model explicitly excludes `embedding` (pgvector
  `Vector(1536)` is not JSON-serializable). `memory_type` typed as `Literal[…]` — FastAPI
  validates before the query. Orders by `created_at desc`, limit 1–100 (default 20).
- `tests/clients/test_api_routes.py`: 11 route-level tests. All error-mapping tests raise
  exceptions from `mock_engine.handle_request` (not injected directly in the route), validating
  the full dispatch path through the engine boundary. Sandbox violation test asserts the secret
  path does not appear anywhere in the response body.

**What failed and why:**

- `_make_memory()` in tests constructed `Memory(...)` without `created_at`. The ORM has only a
  `server_default=func.now()` (no Python-side default), so the field was `None`. Pydantic's
  `model_validate` rejected it. Fixed by passing `created_at=datetime.now(UTC)` explicitly.
- Ruff UP017: `timezone.utc` → `UTC` alias. Auto-fixed by `ruff --fix`.
- ggshield pre-commit hook requires GitGuardian API key (not available on VM). Commit staged
  but not pushed. Will go through on PC after `ggshield auth login` or with `--no-verify`
  (no secrets in these files — pure logic).

**Key design decisions:**

- **Engine owns the session, not the route.** Moving `get_or_create_user` into
  `handle_request()` keeps the FK guarantee in one transaction and keeps the route a pure
  thin translator. The alternative (route opens its own session, commits user, then calls engine)
  would be business logic in the client and requires fragile two-session ordering.
- **`get_or_create_user` in `clients/`, not `core/`.** It is a shared client-layer utility, not
  core business logic. The engine imports it; core does not depend on clients. This is an
  intentional layering choice: the function handles the HTTP/Telegram concern of "what user is
  this?" and the engine invokes it. If this feels wrong at Phase 5 (when real auth arrives),
  migrate to `core/auth.py` — but for now the dependency direction is acceptable.
- **`metadata_` serialization.** SQLAlchemy maps `metadata_` (Python) → `"metadata"` (DB
  column). Pydantic `from_attributes=True` reads the Python attribute name directly — no alias
  needed. Field named `metadata_` in `MemoryRow` serializes to `"metadata_"` in the JSON
  response (not `"metadata"`). This is correct for API consumers; renaming would require a
  Pydantic alias if the API contract requires `"metadata"`. Deferred.
- **`user_id` is caller-trusted until Phase 5.** `get_or_create_user` auto-creates a `User`
  row for any UUID that arrives. This is intentional: with no auth, there is no meaningful way
  to reject a `user_id`. Phase 5 adds API key verification and ties the key to a known `User`.

**Deferred (with rationale):**

- `clients/api/routes/tasks.py` — `Task` model and DB table exist, but there is no tasks
  plugin, no task manager, and no business logic above the ORM. A route without the layer above
  it violates the thin-route rule. Defer to a slice that implements `TaskManager` +
  `CreateTaskPlugin` together with the route.
- `clients/api/routes/projects.py` — same situation as tasks. Defer.
- `POST /v1/memories` — writing memories directly via REST must go through `MemoryManager.write()`
  for the embedding pipeline (OpenAI call + importance scoring). A direct `db.add(Memory(...))`
  bypasses this and leaves records without embeddings. No product requirement yet. Defer.

**Quality gate (VM, provisional):** ruff ✓ (3 auto-fixes: import order, UP017) · black ✓
(3.11 AST check degraded — expected) · mypy ✓ (106 source files, 0 errors) ·
pytest 113/113 unit ✓ · 4 skipped (integration, Docker not on VM) · commit PENDING (ggshield
hook needs auth on PC).

**Authoritative run PENDING on PC:**
```bash
# Commit (ggshield auth should work on PC):
git commit -m "feat: REST chat route, memories read route, error handlers (Slice 3d)"

pytest -v   # incl. integration + schema equivalence — must still show 0 drift
docker compose up --build   # /health → 200; worker must not crash-loop

# Smoke tests (first end-to-end over HTTP):
curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "00000000-0000-0000-0000-000000000001", "content": "search the web for Python 3.13 features"}' \
  | jq '{content, tool_calls_made}'
# expect: tool_calls_made includes "web_search"

curl -s -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id": "00000000-0000-0000-0000-000000000001", "content": "read ../../../../etc/passwd"}' \
  | jq '{error, detail}'
# expect: {"error": "access_denied", "detail": "Access denied."} — NOT 500, path NOT in response

curl -s "http://localhost:8000/v1/memories?user_id=00000000-0000-0000-0000-000000000001" \
  | jq 'length'
# expect: >= 1 (engine wrote episodic memory for the chat above)
```

---

## 2026-07-13 — Slice 3c: Telegram client (long-polling)

**What was built:**

- `clients/user_helper.py`: Added `get_or_create_user_by_telegram_id(db, telegram_id) -> uuid.UUID`.
  Race-safe: SELECT → INSERT ON CONFLICT DO NOTHING (on `telegram_id`) → re-SELECT **by
  `telegram_id`**. The re-SELECT key is the critical difference from the UUID helper: if this
  request lost the insert race, the locally-generated `new_id` was discarded by ON CONFLICT and
  will not exist; only `telegram_id` is guaranteed to be present in the winning row.
- `clients/telegram/formatters.py`: Three functions: `escape_html` (& first, then < and >),
  `split_message` (newline-preferred split; hard-split fallback), `format_response`
  (escape-then-split — order is load-bearing: `&` → `&amp;` expands 1→5 chars, so splitting on
  unescaped length would produce chunks that exceed 4096 after escaping).
- `clients/telegram/handlers.py`: Replaced `telegram_user_map` dict stub. Handler now opens its
  own mini-session, calls `get_or_create_user_by_telegram_id`, commits, then calls
  `engine.handle_request` (same path as `/v1/chat`). `format_response` applied to all outgoing
  content. Removed "account not linked" error branch — auto-creation makes it obsolete.
- `clients/telegram/bot.py`: `run_polling(engine, session_factory)` — removed `telegram_user_map`
  parameter; `session_factory` injected via Dispatcher workflow_data kwargs instead.
- `clients/wiring.py`: New shared engine builder `build_engine(s) -> (sql_engine, factory, core,
  serper_client)`. Extracted from `main.py`'s lifespan so both FastAPI and the Telegram bot
  construct the engine identically without duplication. Returns the serper client separately so
  callers can call `.aclose()` on shutdown.
- `clients/api/main.py`: Lifespan now delegates to `build_engine()` — no logic duplication.
- `clients/telegram/__main__.py`: Entry point: `python -m clients.telegram`. Calls `build_engine`
  then `run_polling`. Wires the bot for local smoke testing without any plumbing in the REPL.
- `clients/telegram/middleware.py`: Left as placeholder — no concrete cross-cutting concern for
  the long-polling MVP. Rate limiting and per-request logging are Phase 5.
- Tests: `tests/clients/test_user_helper.py` (5 tests — covers both UUID and telegram_id helpers,
  including a test that documents the 3-call re-SELECT contract); `test_telegram_handlers.py` (5
  tests — including commit-before-engine ordering test); `test_telegram_formatters.py` (13 tests
  — including the escape-then-split ordering regression test with 1024 `&` chars).

**What failed and why:**

- `assert result is existing` in test_user_helper used `SimpleNamespace` mocks. mypy strict mode
  flags identity checks between `User` and `SimpleNamespace` as `[comparison-overlap]`. Fixed by
  switching `_make_user` helper to return `MagicMock()` (typed `Any` by mypy).
- ruff auto-fixed 6 unused imports across the three new test files (the `call` import from
  `unittest.mock` and `pytest` redundant marks).
- black reformatted 3 files (trailing-comma and blank-line style).

**Key design decisions:**

- **Re-SELECT by `telegram_id`, not `new_id`**: The UUID helper re-selects by `id` because the
  caller supplies the `id` and that IS the conflict target. Here the conflict target is
  `telegram_id`; on a race loss the `new_id` is discarded by postgres and doesn't exist. This is
  a subtle but critical difference.
- **Two mini-sessions**: Handler commits the user upsert in its own session before calling
  `engine.handle_request`. The engine opens a second session for its own `get_or_create_user`
  call (finds the already-committed row by UUID). The extra PK lookup is trivially cheap and
  keeps the engine interface client-agnostic.
- **escape-then-split order**: `format_response` escapes first so that split boundaries are
  computed against the final Telegram-bound byte count. Inverting this order is a subtle bug where
  content within the limit before escaping can exceed it after.
- **`clients/wiring.py` as shared builder**: Both the FastAPI lifespan and the Telegram
  `__main__.py` now call `build_engine()`. This eliminates copy-paste drift between the two
  entry points and ensures plugin registration is always identical.

**Deferred (with rationale):**

- **Markdown → Telegram HTML rendering**: The LLM returns Markdown (`**bold**`, backtick code,
  `[link](url)`). With `ParseMode.HTML`, these render as literal asterisks and brackets. Safe and
  non-crashing for this slice. A converter that turns `**text**` → `<b>text</b>` etc. is a
  polish item — do not build until there's a concrete product requirement.
- **Webhook mode + TELEGRAM_WEBHOOK_SECRET validation**: Phase 4. `telegram_webhook_url = None`
  means long-polling until Caddy is live on the Oracle VM.
- **Bot containerization in docker-compose**: Phase 4. The bot is a third long-running process;
  add it as a service when webhook mode is implemented.
- **`middleware.py` implementation**: Phase 5. No concrete cross-cutting concern yet.

**Quality gate (VM, provisional):** ruff ✓ (6 auto-fixes: unused imports) · black ✓ (3.11
degraded check — expected) · mypy ✓ (113 source files, 0 errors) · pytest 154/154 unit ✓ ·
1 skipped (symlink test, env limitation) · 3 deselected (integration, Docker not on VM).

**Authoritative run PENDING on PC:**
```bash
pytest -v   # incl. integration + schema equivalence — must still show 0 drift
docker compose up --build   # /health → 200; worker must not crash-loop

# Local smoke test (long-polling):
python -m clients.telegram
# Message from phone: "search the web for Python 3.13 features"
# Message: "remind me to call Bob in 5 minutes"
# Message: "what is 2 + 2" with <html> tags to verify escape doesn't break replies
```

---

## 2026-07-13 — Multi-turn tool-call serialization fix (Responses API)

**Bug found and fixed post-Slice-3d during live end-to-end testing.**

**What failed:**
The second LLM turn of a ReAct loop was rejected by OpenAI with:
`400: Invalid value: 'function_call'. Supported values are 'input_text', 'input_image'...`

**Root cause:**
`_to_item` serialized an assistant message with tool calls as a regular message item with the
tool call embedded in a content block. The Responses API does NOT accept `function_call` as a
content-block type — prior function calls must be SEPARATE TOP-LEVEL items with
`{"type": "function_call", ...}`. This is fundamentally different from Chat Completions, where
tool calls live inside a message object. Tool outputs (`function_call_output`) were already
correct; only the prior function calls were wrong.

**Why mocks hid this:**
Unit tests mock `responses.create` entirely and never validate the shape of `input[]`. The broken
path only triggers on iteration 2+ of a ReAct loop — invisible until a real two-step API call.

**What was fixed:**
- Renamed `_to_item` to `_to_items` (returns `list[dict]`): an assistant turn with N tool calls
  expands to N separate `function_call` items in the flat `input[]` array.
- Added reasoning item collection and verbatim echo-back for reasoning models
  (`LLMMessage.raw_item`, `LLMResponse.reasoning_items`, `role="reasoning"` in `LLMMessage`).
- Response parsing now checks `getattr(c, "type", None) == "output_text"` instead of
  `hasattr(c, "text")` — refusal items also have `.text` and must not be concatenated.
- `temperature=NOT_GIVEN` when `config.temperature is None` (GPT-5 family rejects the param).

**Tests added (8 new):**
- `test_to_items_*` sync tests: no mock API, just `_to_items()` output shape verification.
  Cover function_call expansion, call_id pairing, reasoning passthrough, EasyInputMessage shape.
- `test_complete_reasoning_items_collected` and `test_complete_unknown_output_items_ignored`:
  async tests with realistic Responses API response shapes.
- Fixed `_make_message_response` helper: content items now carry `type="output_text"` to match
  real API shape (old mock had no `.type` attr; new parser requires it).

**Key insight for future work on openai_provider.py:**
This file is the highest-risk file for mock-hidden bugs. Every Responses API shape detail
(input item types, output item types, content sub-types) is invisible to mocks. Real multi-step
API calls are the only reliable gate for serialization correctness. Prefer adding sync
serialization-only tests (`_to_items` calls) over relying on mock-`complete()` integration tests.

**Authoritative gate (PC required):**
pytest -v (127+ tests), docker compose up --build, then POST /v1/chat with a web_search prompt
and verify tool_calls_made == ["web_search"] with non-empty content.

---

## 2026-07-12 — Slice 3b: file_reader plugin + local_fs integration

**What was built:**

- `core/exceptions.py`: Added `FileReaderError(IntegrationError)` base and five subclasses:
  `SandboxViolationError`, `FileNotFoundInSandboxError`, `PathIsDirectoryError`,
  `FileTooLargeError`, `FileDecodeError`. All inherit `FileReaderError` — uniform base, no raw
  stdlib exceptions escape the integration layer.
- `integrations/local_fs.py`: `LocalFsClient` with `read(requested_path)` and `health_check()`.
  Security guard: join path as-is, call `.resolve()`, then assert `is_relative_to(root)` — this
  is the *only* containment check. No string sanitization (fragile). Containment check runs
  BEFORE `exists()`/`stat()` so out-of-sandbox paths reveal no filesystem information. Covers
  `..`, absolute paths, symlinks, encoded traversal. File I/O runs in `asyncio.to_thread`.
- `plugins/file_reader/schemas.py`: `FileReaderInput` (`path`, `summarize`), `FileReaderOutput`,
  `FileReaderConfig`. No `user_id` in input schema.
- `plugins/file_reader/plugin.py`: `FileReaderPlugin(PluginBase)` — reads via `LocalFsClient`,
  calls `llm.complete()` with fast model for summarisation when content > 500 chars and
  `summarize=True`. No direct openai import; goes through `LLMProvider` seam. `health_check()`
  delegates to `client.health_check()`.
- `core/config.py`: Added `file_reader_root: Path | None = None` and
  `file_reader_max_bytes: int = 1_048_576`.
- `clients/api/main.py`: Conditional registration of `FileReaderPlugin` mirroring web_search
  pattern — warns and disables if `FILE_READER_ROOT` is unset.
- `docker-compose.yml`: `FILE_READER_ROOT=/app/files` + `FILE_READER_MAX_BYTES` env vars on
  both app and worker services; `./data/files:/app/files:ro` bind-mount (read-only — container
  cannot write back).
- `.env.example`: Added `SERPER_API_KEY` placeholder (was missing) and `FILE_READER_ROOT`,
  `FILE_READER_MAX_BYTES` placeholders.
- `data/files/.gitkeep`: Sandbox directory skeleton committed so the bind-mount source exists.
- `tests/integration/test_local_fs.py`: 11 tests — real files in `tmp_path`, no mocking.
  Security tests cover `..`, absolute paths, multi-hop traversal, symlink escape, nonexistent
  file, directory-not-file, oversized file, non-UTF-8 bytes. Symlink test skipped on Windows
  with explicit reason if `os.symlink` raises `OSError` (privilege restriction).
- `tests/plugins/test_file_reader.py`: 14 tests — mocked client + LLM. Schema contract
  (no `user_id`), short content (no LLM call), long content (LLM called with fast model, correct
  prompt), summarize=False (LLM never called), error propagation, health check, planner
  integration (mocked LLM → tool call → synthesis → asserts `read_file` in `tool_calls_made`).

**What failed and why:**

- One test assertion (`test_execute_long_content_llm_message_contains_path_and_content`) checked
  for `"doc.txt"` in the LLM prompt, but `_make_client` defaulted `path="test.txt"` in the
  returned `FileReadResult`. The plugin uses `result.path` (the resolved relative path from the
  client) in the prompt, not the raw input path. Fixed by passing `path="doc.txt"` to
  `_make_client` in that test.
- Ruff auto-fixed 3 import-order issues in the new test files.
- Black reformatted 4 files (trailing comma placement, line wraps). 3.11 AST warning is expected
  noise — Docker gate on 3.12 is authoritative.

**Key design decisions:**

- **Resolve-then-contain, no string sanitization**: stripping `..` or `/` from the path string
  before joining is fragile and can be bypassed by encoded or mixed-separator traversal.
  `Path.resolve()` + `is_relative_to()` is sufficient and correct.
- **Containment before existence**: rejecting before `exists()`/`stat()` prevents the client
  from being used as an oracle to probe the host filesystem outside the sandbox.
- **All errors are `FileReaderError` subclasses**: avoids leaking raw `FileNotFoundError`/
  `IsADirectoryError` from stdlib, keeps the exception hierarchy uniform.
- **Read-only Docker mount** (`./data/files:/app/files:ro`): container cannot write back to the
  host directory — defence in depth beyond the sandbox root check.
- **`data/files/` committed with `.gitkeep`**: the bind-mount source must exist on the host
  before `docker compose up` or Docker will create it as root-owned and the mount may fail.

**Quality gate (VM, provisional):** ruff ✓ (3 auto-fixes) · black ✓ (3.11 AST check degraded —
expected) · mypy ✓ (104 source files, 0 errors) · pytest 102/102 unit ✓ · 1 skipped (symlink
test, Windows privilege) · 3 integration tests deselected (Docker not available on VM).

**Authoritative run PENDING on PC:**
```
pytest -v   # incl. integration + schema equivalence — must still show 0 drift
docker compose up --build   # /health → 200; worker must not crash-loop
# Smoke test: place a text file in ./data/files/hello.txt, send
# "read hello.txt" through the engine — verify FileReaderPlugin fires
```

**Note on `.env.example`:** `SERPER_API_KEY` placeholder was previously absent from the file
(only present in docker-compose env list). Added in this slice. Verify on PC with
`grep -i serper .env.example` and `grep -i file_reader .env.example`.

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

---

## 2026-07-08 — Schema single source of truth: drift detection test

**Decision:** Option A — schema equivalence integration test. Option B (autogenerate) was
rejected: pgvector `Vector(1536)`, the HNSW index (raw DDL), three partial indexes with
raw-SQL `WHERE` clauses, and the `create_type=False` enum pattern all require custom
autogenerate hooks to reproduce correctly — autogenerate would silently DROP+RECREATE them.

**What was built:** `tests/integration/test_schema_equivalence.py`

Two independent `pgvector/pgvector:pg16` testcontainer instances — full isolation avoids
enum type-name collisions and search_path confusion that a two-schema approach would cause:
- Container 1 (alembic): `alembic upgrade head` via subprocess (same pattern as `test_full_flow.py`)
- Container 2 (models): `CREATE EXTENSION` + manual `CREATE TYPE` ×4 (create_type=False suppresses
  them in create_all) + `await conn.run_sync(Base.metadata.create_all)` — all-async, no psycopg2
- Container 3 (drift): throwaway `MetaData` with one extra column on `users`, proves guard fires

**What is compared:**
- Columns: `udt_name`, `is_nullable`, normalised `column_default` (strips `::type_casts`, outer
  quotes; normalises `now()` / `CURRENT_TIMESTAMP`; enum defaults compared after cast stripping)
- Indexes: `pg_indexes.indexdef` normalised (whitespace collapse); `ix_memories_embedding_hnsw`
  excluded from diff (legitimately absent from model side per `models/memory.py`), asserted
  present on alembic side separately
- ENUMs: `pg_enum JOIN pg_type` — type name + label list in sort order

**`pyproject.toml`:** Added `testcontainers>=4.0` to dev deps (no `[postgres]` extra in 4.x;
was already imported in `test_full_flow.py` but undeclared).

**Quality gate:** ruff ✓ · black ✓ (3.11 VM, AST check degraded — expected) · mypy ✓
(102 source files, 0 errors) · pytest 43/43 unit ✓ · 2 new integration tests deselected
(Docker not available on VM — **authoritative run PENDING on PC**).

**How to run the check (PC):**
```
pytest tests/integration/test_schema_equivalence.py -v
```
Expected: `test_schema_equivalence` PASSES, `test_drift_is_detected` PASSES.

**To trigger a failure deliberately:** introduce any column type change, enum label addition,
or new index in either `models/` or `alembic/versions/` without updating the other side,
then run the test — it will print the full diff and call `pytest.fail`.

---

## 2026-07-08 — Fix real drift found by schema equivalence test

**What the test found (11 items, PC run):**

1. **Nullability (8 columns):** `users.preferences`, `projects.status`, `projects.metadata`,
   `tasks.status`, `tasks.priority`, `plugin_registry.enabled`, `plugin_registry.config`,
   `plugin_registry.health_status` — all `NOT NULL` in models (have `server_default`, so nullable
   is semantically wrong) but migration omitted `nullable=False`, defaulting to nullable in pg.
2. **`users.telegram_id` type:** model inferred `int4` (no explicit column type); migration
   correctly declared `BigInteger` (`int8`). Telegram IDs exceed 2^31 — `int8` is correct.
3. **Server defaults (2 columns):** `memories.importance_score` and `tasks.priority` had
   Python-side `default=` only; migration had DB-level `DEFAULT`. Added `server_default=` to
   both model columns to match.
4. **Missing indexes (2):** `ix_projects_user_id` and `ix_reminders_user_id` existed in the
   migration but were absent from `__table_args__`. Added to `models/project.py` and
   `models/reminder.py`.

**Authoritative side per item:** Models right for nullability (migration fixed). Migration right
for `telegram_id` type and the two indexes (models fixed). Models needed `server_default=` added.

**Files changed:** `alembic/versions/0001_initial.py`, `models/user.py`, `models/memory.py`,
`models/task.py`, `models/project.py`, `models/reminder.py`.

**Quality gate (VM, provisional):** ruff ✓ · black ✓ · mypy ✓ (102 files) · pytest 43/43 unit ✓.
**Authoritative run PENDING on PC:** `pytest tests/integration/test_schema_equivalence.py -v`
must show 2 passed (zero drift + drift guard still live).

---

## 2026-07-08 — Session C: Phase 2 — ReAct Planner Loop

**What was built:**

- `core/planner/base.py`: `PlannerBase` ABC + `PlannerResult` dataclass (content, tool_calls_made, iterations). Plain dataclass, not Pydantic — internal return value, never serialised.
- `core/planner/react.py`: `ReActPlanner` — full ReAct loop. Each iteration: LLM call → if tool_calls: append assistant msg (with all tool calls), execute each tool, append tool_result per tool, loop; if message: return. Raises `PlannerMaxIterationsError` at cap, `PlannerStuckLoopError` on repeated identical batch.
- `core/engine.py`: `_process()` replaced to delegate to `ReActPlanner`. `_SYSTEM_PROMPT` expanded to list all registered tools by name dynamically. `memories_written` now derived from actual `memory.write()` return value (not hardcoded 1).
- `tests/core/test_planner.py`: 13 unit tests (direct message, single tool, multi-tool accumulation, history format, max-iterations cap, stuck-loop detection with same/different args/tools/nested values, user_id injection, empty tools, three-turn sequence).
- `tests/core/test_engine.py`: added `planner_max_iterations=8` and `planner_default_temperature=0.7` to mock settings; changed tool-call mock to `side_effect=[tool_call_resp, synthesis]` so the two-step loop gets a terminal message on the second call.
- `tests/integration/test_full_flow.py`: `_mock_llm()` switched from `return_value` to `side_effect=[tool_call_resp, synthesis_resp]`; settings mock gains planner attrs.

**What failed and why:**

- `mypy` flagged `# type: ignore[return-value]` on `_format_tool_result` as unused (mypy on 3.11 inferred `Any` return from the `or` chain). Fixed by removing the ignore and wrapping in `str(...)`.
- `ruff` auto-fixed 2 import-order issues in `react.py` (LLMTool moved alongside other LLM imports).
- `black` reformatted `test_planner.py` (trailing comma placement in one function call). No logic changes.

**Key design decisions:**

- **Stuck-loop signature**: `json.dumps(sorted(...))` rather than `frozenset(sorted(items()))` — the latter crashes on unhashable nested dict/list argument values. JSON serialisation is safe for all value types.
- **Provider-layer gap**: confirmed none. `openai_provider.py`'s `_to_item()` already correctly translates `role="assistant"` + `tool_calls` → `function_call` content block, and `role="tool_result"` + `tool_call_id` → `function_call_output`. No adapter changes needed. The PC integration test exercises the real adapter path.
- **`memories_written`**: `memory.write()` already returns a `Memory` ORM object (not None) on success. Engine now counts `1 if mem is not None else 0`. In practice always 1 on the success path, but derived from the real call rather than hardcoded.
- **Engine never catches planner exceptions**: `PlannerMaxIterationsError` / `PlannerStuckLoopError` propagate to `handle_request()`'s bare `except`, which rolls back and re-raises. No partial commit on planner failure.

**Quality gate (VM, provisional):** ruff ✓ · black ✓ (3.11, AST check degraded — expected) · mypy ✓ (102 files, 0 errors) · pytest 56/56 unit ✓ · 3 integration tests deselected (Docker not available on VM).

**Authoritative run PENDING on PC:**
```
pytest -v   # must include integration + schema-equivalence, 0 skipped
docker compose up --build   # app + worker must boot; /health → 200
```

---

## 2026-07-08 — Session D: Slice 3a — web_search plugin + Serper integration

**What was built:**

- `core/exceptions.py`: Added `IntegrationRateLimitError(IntegrationError)` — HTTP 429 sentinel,
  never retried.
- `integrations/serper.py`: `SerperResult` dataclass (title, link, snippet). `SerperClient` with
  injected `httpx.AsyncClient` (testability), tenacity retry on 5xx/timeout via `_is_retryable`
  predicate, explicit 429 check before `raise_for_status()` to ensure rate-limit errors escape the
  retry decorator, `health_check()` as `bool(api_key)` (no network call, no quota drain).
- `plugins/web_search/schemas.py`: `WebSearchInput` (query + max_results with ge/le bounds),
  `SearchResult`, `WebSearchOutput`, `WebSearchConfig`. No `user_id` in input schema.
- `plugins/web_search/plugin.py`: `WebSearchPlugin(PluginBase)` — stateless, delegates to
  `SerperClient`. Accepts `user_id`/`db` per contract but does not use them.
- `clients/api/main.py`: Conditional registration (`if s.serper_api_key is not None`) with
  startup `log.warning` when key absent. `serper_client.aclose()` called in lifespan teardown
  to prevent connection-pool leak.
- `tests/integrations/test_serper.py` (new): 10 tests using `pytest-httpx`. Covers happy path,
  header verification, empty/missing organic key, 429 no-retry (call count asserted == 1),
  401 no-retry, 500 retries ×3, timeout retries ×3, health_check true/false.
- `tests/plugins/test_web_search.py` (replaced placeholder): 13 tests. Schema shape, bounds
  validation, execute happy path, max_results forwarding, empty results, rate-limit propagation,
  health_check delegation, planner integration (mocked LLM + real registry + mocked SerperClient
  — proves tool_calls_made and final synthesis).

**What failed and why:**

- `pytest_httpx` not installed on VM — `pip install pytest-httpx` with `--trusted-host` flags.
  (Already in `pyproject.toml` dev deps; just not in the VM's venv yet.)
- Two test assertions used `keyword` form (`query="..."`) but `plugin.execute` calls
  `client.search(data.query, num_results=...)` positionally. Fixed assertions to positional form.

**Key design decisions:**

- `_is_retryable` predicate catches only `httpx.TimeoutException` and `httpx.HTTPStatusError`
  with `status_code >= 500`. Because `IntegrationRateLimitError` is raised by explicit `if`
  check *before* `raise_for_status()`, tenacity never sees it — 429 is never retried.
- `health_check()` is `bool(self._api_key)` — no HTTP, no quota. A 429 on a health probe would
  give a false unhealthy; a billed search per health probe is wasteful. Key presence is
  the actionable signal.
- `serper_client = None` initialized before the `if` branch so the teardown guard
  (`if serper_client is not None: await serper_client.aclose()`) is always valid regardless of
  whether the key was present at startup.

**Quality gate (VM, provisional):** ruff ✓ (2 import-order auto-fixes) · black ✓ (3.11 AST
check degraded — expected) · mypy ✓ (104 source files, 0 errors) · pytest 79/79 unit ✓ ·
3 integration tests skipped (Docker not available on VM).

**Authoritative run PENDING on PC:**
```
pytest -v   # incl. integration + schema equivalence — must still show 0 drift
docker compose up --build   # /health → 200; worker must not crash-loop
```

**Note on `.env.example`:** The file exists but is unreadable in the VM session (permissions
restriction). Verify `grep -i serper .env.example` on PC; add `SERPER_API_KEY=your-serper-key-here`
if absent.

---

## 2026-07-12 — Fix: structlog/PrintLogger crash + compose env gap (post-3a PC verification)

**What broke on PC:** `docker compose up` crashed at startup with `TypeError: PrintLogger.msg()
got an unexpected keyword argument 'extra'`. Root cause: `configure_logging()` used
`ProcessorFormatter.wrap_for_formatter` as the final processor — this is the stdlib bridge and
produces an `extra=` kwarg intended for `logging.LogRecord`. `PrintLoggerFactory` produces
`PrintLogger` whose `msg()` takes no keyword args. The two modes are incompatible; every log call
crashed. Latent since Session A because unit tests run before `configure_logging()` is called, so
structlog's default config (which works) was in effect during testing.

**Additional issue:** `docker-compose.yml` app and worker services were missing `SERPER_API_KEY`
in their environment lists — the key from `.env` was never injected into containers.

**Fix:** Rewrote `configure_logging()` to keep `PrintLoggerFactory` throughout. The renderer
(`ConsoleRenderer` or `JSONRenderer`) is now the terminal processor in the chain directly — no
`wrap_for_formatter`, no `ProcessorFormatter`. stdlib routing preserved via a plain
`StreamHandler` on the root logger. Added `SERPER_API_KEY` to both app and worker env lists.

**PC sync changes:** `tests/integration/test_serper.py` (moved from `tests/integrations/` into
the existing `tests/integration/` directory to keep all integration-style tests together).
`tests/core/test_logging.py` removed on PC (logging verified through container boot instead).
Slice 3a DIARY entry trimmed in sync — restored here.

**Quality gate (PC, authoritative):** pytest -v ✓ (all integration tests ran including schema
equivalence and full flow) · docker compose up --build ✓ · /health → 200 · web_search smoke
test with real SERPER_API_KEY returned results.

**Neon free tier validated as production DB** — pgvector + HNSW migration applies cleanly, asyncpg connects (direct endpoint, no sslmode param), embedding write/read verified against Neon. Watch: scale-to-zero + 100 compute-hrs/month; every-minute reminder poll keeps DB awake — monitor usage.
