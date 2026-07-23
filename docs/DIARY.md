# Development Diary

---

## 2026-07-22 — Slice 5A: Multi-item scheduled content plan

### What was built

- **`models/content_plan.py`**: New `ContentPlan` model (id, user_id, status, timestamps). `status='approved'` only in 5A — no pre-approval DB row.
- **`models/scheduled_post.py`**: Extended with `post_type` (VARCHAR 'single'|'carousel', NOT NULL DEFAULT 'single'), `image_urls` (JSONB nullable), `plan_id` (FK → content_plans SET NULL nullable). `image_url` made nullable (carousel rows have NULL there).
- **`alembic/versions/0005_content_plan.py`**: Migration safe on populated tables — `post_type` gets server default 'single' at ALTER time, `image_urls`/`plan_id` are nullable. `image_url` NOT NULL dropped. Order: create content_plans first (FK target), then ADD COLUMNs.
- **`plugins/build_content_plan/`**: New plugin package — `plugin.py`, `schemas.py`, `list.py`, `cancel.py`.
  - `BuildContentPlanPlugin`: `requires_approval=True`, `needs_hosted_images=True`. LLM schema uses `image_indices` (NOT URLs — trusted context invariant maintained). `execute()` bulk-creates `ContentPlan` + N `ScheduledPost` rows. Single-item → `post_type='single'` + `image_url`; multi-item → `post_type='carousel'` + `image_urls`.
  - `build_preview(cls, args)` classmethod produces a human-readable plan summary (item count, Single/Carousel label, caption preview, human-formatted time). Includes "Each post will ask for final confirmation when it's time" so the user knows this is scheduling, not silent auto-posting.
  - `ListContentPlansPlugin` / `CancelContentPlanPlugin`: mirror the `list_scheduled_posts` / `cancel_scheduled_post` pattern. Cancel only touches `status='scheduled'` rows (triggered posts have their own Cancel button).
- **`plugins/base.py`**: Added `build_preview(cls, args) -> str` classmethod with a sensible default. Plugins override for richer preview text.
- **`core/planner/react.py`**: Updated approval-sentinel branch to call `plugin.build_preview(out["args"])` instead of hardcoding the raw args dump. Falls back gracefully if `get_plugin` returns None.
- **`core/scheduler/jobs.py`**: Worker Phase 2 now branches on `post.post_type`: carousel → `action_type='instagram_carousel'`, payload `{caption, image_urls}`, notify with `image_urls[0]`; single → existing path unchanged. Asserts added for mypy narrowing of nullable columns.
- **`core/engine.py`**: System prompt updated from 3-way to 4-way routing: multi-photo + scheduling intent → `build_content_plan`; multi-photo + post now → `instagram_carousel`; multi-photo + text plan only → text; single photo → existing paths.
- **`clients/wiring.py`**: `BuildContentPlanPlugin`, `ListContentPlansPlugin`, `CancelContentPlanPlugin` registered inside the `if s.instagram_access_token` guard.
- **Tests**: `test_build_content_plan.py` (ClassVar contract, `build_preview` correctness, happy-path post creation, UTC localization, all validation error paths), `test_poll_scheduled_posts_carousel.py` (carousel action_type/payload, first-image notify URL, status→triggered, single regression), `test_cancel_content_plan.py` (cancel pattern, triggered-post untouched, not-found paths).

### Key design decisions

**Post-approval persistence.** `ContentPlan` row only created inside `execute()` after Confirm tap — not before. The proposal lives as JSONB in `pending_actions`. Clean: no orphaned plan rows if the user cancels.

**Index-based grouping.** LLM supplies `image_indices: list[int]` per item, never raw URLs. The engine injects a flat `image_urls` list (same as Slice 3). Plugin resolves `indices → actual_urls` in `execute()`. This keeps the trusted-context invariant from Slices 2/3 intact.

**`build_preview` hook.** Added to `PluginBase` as a concrete classmethod (not abstract) — existing plugins need zero changes. The planner calls it synchronously; no new I/O, no latency cost. For `build_content_plan`, it formats the plan as readable text with human-readable times (cross-platform strftime — `%-d`/`%-I` are Linux-only, replaced with explicit string construction).

**Carousel fire path is zero-change.** The existing `instagram_carousel` plugin, registry `_INJECTED_CONTEXT_KEYS`, and `handle_callback` path all work unchanged for scheduled carousels. The worker just builds `action_type='instagram_carousel'` + `{caption, image_urls}` instead of the single-post payload.

**Migration 0005 safe on populated data.** Tested pattern: `post_type NOT NULL DEFAULT 'single'` backfills via server default at `ALTER TABLE` time in Postgres. `image_url` going nullable leaves existing values intact. `pyproject.toml` `[[tool.mypy.overrides]]` updated to include `0005_content_plan`.

**Cancel scope in 5A.** Both `list_content_plans` and `cancel_content_plan` included — without them, there's no way to cancel all N posts as a unit, making the plan_id FK useless.

### Deferred
- 5B: Plan editing (change caption/time per item before approval), richer approval preview (show photos inline), per-item cancel from the list view.
- Per-user timezone (currently global `Settings.default_timezone` only).

---

## 2026-07-22 — Slice 3: Immediate Instagram carousel posting

### What was built

- `integrations/instagram.py`: `publish_carousel(image_urls, caption)` — creates N child containers
  (each with `is_carousel_item=true`), polls each child to FINISHED, creates the parent CAROUSEL
  container with the caption, polls the parent to FINISHED, then publishes. Same `@retry` +
  `_check_response` + code-190 token-expiry handling as `publish_photo`. No length validation
  in the integration layer (see below).
- `plugins/base.py`: `needs_hosted_images: ClassVar[bool] = False` added after `needs_hosted_image`.
  These two flags are mutually exclusive: `instagram_post` has `needs_hosted_image=True /
  needs_hosted_images=False`; `instagram_carousel` has `needs_hosted_images=True /
  needs_hosted_image=False`. The engine uses `if/elif` so only one branch fires.
- `core/tools/registry.py`: `"image_urls"` added to `_INJECTED_CONTEXT_KEYS`. The existing split
  loop and `inspect.signature` filter forward the list value to any plugin that declares
  `image_urls: list[str]` in `execute()`.
- `core/engine.py`: Extracted a shared `_upload_single_image(b64, mime)` inner helper used by
  both the singular (`needs_hosted_image`) and plural (`needs_hosted_images`) upload branches —
  no duplicated upload code. The `elif` carousel branch iterates `request.images`, uploads each
  to R2, and stores `image_urls: [url, ...]` in `action_payload` (JSONB holds lists natively —
  no migration needed). System prompt updated with unambiguous 3-way routing: carousel-post /
  content-plan / single-post.
- `plugins/instagram_carousel/`: New plugin — `requires_approval=True`, `needs_hosted_images=True`.
  Length validation (2 ≤ N ≤ 10) lives HERE, not in the integration client (correct layering:
  `IntegrationError` in the HTTP client, `PluginError` in the plugin).
- `clients/wiring.py`: `InstagramCarouselPlugin` registered alongside `InstagramPostPlugin` inside
  the `if s.instagram_access_token and s.instagram_user_id:` guard.

### Key design decisions

**Caption on parent container.** The caption goes in the parent container POST (`media_type=CAROUSEL,
children, caption`), not on child containers or the publish call. The live DoD test MUST confirm
the caption appears on the posted carousel (not tested by unit tests).

**Defensive per-container readiness poll.** Every child AND the parent container is polled to
FINISHED before the next step. The probe showed children FINISHED immediately, but single posts
also probed clean and then raced in prod ("Media ID is not available"). Lesson learned: poll
every container, unconditionally.

**`needs_hosted_images` plural flag (not a shared flag).** An explicit separate ClassVar, not a
unified flag. Clean: the engine's `if needs_hosted_image / elif needs_hosted_images` is
unambiguous. A plugin must never set both.

**`image_urls` as trusted injected context.** Never in the LLM-facing input schema. Mirrors the
`image_url` singular pattern: stored in `action_payload` at proposal time, stripped from
`llm_args` at execute time, forwarded as a kwarg only if declared in the plugin's signature.

**No migration.** `action_payload` is JSONB; `{"caption": "...", "image_urls": [...]}` rounds
through Postgres unchanged. The approval callback passes the dict as `raw_args` as-is.

### Partial-failure note

If children are created and then the parent-create or publish call errors (e.g., aspect-ratio
mismatch, token expiry mid-flow), the child containers dangle on IG's side. Instagram expires
them automatically — no cleanup is needed or built. The `IntegrationError` propagates up through
the plugin and callback handler, the `pending_action` row ends in `failed`, and the user sees
the clear error message. The failure path is explicit and visible.

### Unverified: aspect-ratio uniformity

Instagram carousels may require that all images share the same aspect ratio. This was not
verified in the probe (the probe images were uniform). If IG rejects a mixed-aspect carousel,
`_check_response` will surface the real error message — no guessing needed. A future slice
could add a pre-flight aspect check if the error message proves cryptic in practice.

### 3-way system prompt routing

The multi-photo paragraph was replaced with explicit routing:
- Multiple photos + "post as carousel" → `instagram_carousel`
- Multiple photos + "content plan" → text plan (Slice 1 behavior, unchanged)
- Single photo + "post to Instagram" → `instagram_post` (unchanged)

---

## 2026-07-20 — Part A: Model routing (gpt-5.5 → gpt-5.4) + Part B: Slice 1 multi-image content-plan

### Part A — Model cost reduction

**Change:** `openai_default_model` default changed from `"gpt-5.5"` to `"gpt-5.4"` in
`core/config.py`. One-line change; overridable via `.env` with no rebuild. All ReAct planner
turns and vision calls now use gpt-5.4 (half the input cost: $2.50/1M vs $5.00/1M).

**Grounding:** Every LLM call routes through `openai_default_model` except file-reader
summarization (already on `openai_fast_model` / gpt-5.4-nano). No per-turn classification or
history-summarization calls exist — the ReAct loop handles everything in one model. No nano
routing added (premature; no clear quality/cost boundary per call type yet). Vision stays on
gpt-5.4 (probe confirmed quality holds at detail=high).

**What was tricky:** Confirming completeness — grepping all `llm.complete` call sites across
plugins to verify nothing was silently on the expensive model. Only `file_reader` had a separate
model path.

### Part B — Slice 1: multi-image album → text content-plan

**What was built:**

- `core/schemas.py`: Added `ImageAttachment(data: str, mime: str)` and
  `images: list[ImageAttachment] | None` to `CoreRequest`. Single-image fields
  (`image_base64`, `image_mime`) untouched.
- `core/engine.py`: Three-branch content-part builder (precedence: `images` → `image_base64` →
  plain text). Batch path builds `N × input_image` parts with `detail="high"` + one `input_text`.
  System prompt updated to instruct the model on the batch case (groupings, captions, posting
  order, strength as suggestion not verdict). Memory/semantic-search use `request.content` string
  in all three paths — no image data ever reaches the DB.
- `clients/telegram/handlers.py`: Album debounce pattern added to `handle_photo`. Module-level
  `_media_group_buffer` and `_media_group_tasks` dicts, keyed by `media_group_id`. The
  append→cancel→restart block is synchronous (no await), so atomic. `_flush_media_group` fires
  1.5s after the last photo, pops both dicts before any failable await, sends the batch as one
  `CoreRequest(images=[...])`. Cancel safety: `CancelledError` caught only during sleep; cancelled
  tasks return before touching the buffer. No `finally` block — that would clobber shared state on
  both paths.

**Key insight — debounce cleanup:** The natural instinct is `try/finally` for cleanup, but a
`finally` block runs on BOTH the cancellation path AND the completing path. A cancelled task
must NOT touch the buffer (the replacement task owns it). Solution: catch `CancelledError`
separately and `return` immediately; pop the dicts only on the completing path, BEFORE any
await that could fail. This way: cancelled = no state touched; completing = dicts cleaned before
I/O work, errors only affect the response send, never dict state.

**Key insight — detail="high":** Probe confirmed ~10–20× more tokens vs "low" but cost still
~2¢/plan for 8 images. Quality difference mattered: "high" produced compositional reasoning
("off-center horizon, warm backlight") vs "low" generic labels. Use "high" for photography tools.

**What was tricky:** `asyncio.sleep` patching in tests — patch at the call site module
(`clients.telegram.handlers.asyncio.sleep`), not the stdlib. Also: `_make_photo_message` in
existing tests needed `media_group_id = None` explicitly, because MagicMock auto-attributes are
truthy and would route lone-photo tests into the album path.

**Tests:** 16 new tests (6 engine batch-image + 10 handler album-path). All 240 pass on VM.
Pre-existing mypy errors in `test_registry_approval.py` / `test_engine_approval.py` (fake plugin
stub missing `**kwargs`) are not from this slice.

**Deferred:** Multi-album batching (>10 photos across multiple sends), posting/scheduling
(Slice 2+), analytics-driven timing (later). Straggler edge (photo arriving after flush, same
mgid) accepted unguarded — realistically impossible at 1.5s debounce vs 500ms album delivery.

---

## 2026-07-17 — Slice: Human-in-the-Loop Approval Flow

**What was built:**

- `models/pending_action.py` + `alembic/versions/0003_pending_actions.py`: New `pending_actions`
  table with `pending_action_status` enum (`pending`, `executing`, `confirmed`, `cancelled`,
  `expired`, `failed`). Partial unique index on `(user_id) WHERE status='pending'::...` enforces
  single-pending-per-user. Enum per CLAUDE.md (postgresql.ENUM create_type=False, explicit CREATE
  TYPE in migration, cast in partial-index WHERE clause). Registered in `models/__init__.py`.
- `plugins/base.py`: Added `requires_approval: ClassVar[bool] = False` — safe default, existing
  plugins untouched.
- `core/planner/base.py`: Added `PendingActionProposal` dataclass and `pending_action` field to
  `PlannerResult` (optional, None by default).
- `core/tools/registry.py`: Added `_approved: bool = False` keyword param to `execute()`. When
  `plugin.requires_approval and not _approved`, returns sentinel `{"__approval_required__": True,
  "tool": name, "args": raw_args}` instead of calling `plugin.execute`. `_approved=True` bypasses
  the gate (used by the callback handler on confirm).
- `core/planner/react.py`: In the tool-dispatch inner loop, detect the sentinel after
  `registry.execute()` returns. If found: immediately return `PlannerResult(pending_action=...)` —
  sentinel is NOT appended to history (that was the key bug to avoid). Remaining batch tools are
  discarded on sentinel (safest for partial batches).
- `core/schemas.py`: Added `ProposalPayload` model and `proposal: ProposalPayload | None` field
  to `CoreResponse`.
- `core/engine.py`: In `_process`, after `planner.run()`, if `plan_result.pending_action` is set:
  cancel any existing pending row for the user (superseding), write a new `pending_actions` row
  (status=pending, expires_at = now + approval_ttl_minutes), return `CoreResponse(proposal=...)`.
  No episodic memory written on proposal turns.
- `core/config.py` + `.env.example`: Added `approval_ttl_minutes: int = 60`.
- `clients/telegram/handlers.py`: Refactored response sending into `_send_response()` helper.
  Proposal responses render as inline keyboard (`InlineKeyboardMarkup` with `InlineKeyboardButton`).
  New `@router.callback_query()` handler with full guard sequence: allowlist, data format (`ok:`/
  `no:` prefix), UUID validity, row existence, user ownership, status (rejects non-pending including
  `"executing"`), expiry. On `ok`: claim row as `"executing"` and COMMIT before calling
  `registry.execute(..., _approved=True)` — prevents double-execution on crash. On success:
  `"confirmed"`. On failure: `"failed"` + friendly message. On `no`: `"cancelled"`.
  Uses `spec=Message` on mock in tests so `isinstance(callback.message, Message)` works correctly.
- `clients/telegram/bot.py` + `__main__.py`: Added `registry` kwarg to `run_polling()` and
  `dp.start_polling(...)` workflow_data. Passed as `core._registry` from `__main__.py` — avoids
  changing `build_engine`'s return type.
- `plugins/approval_test/`: New `ApprovalTestPlugin` (`name="dummy_confirm_action"`,
  `requires_approval=True`) that echoes a message after confirmation. Registered in `wiring.py`.
  Proves the full pause→propose→confirm→execute cycle without any external side effects.
- Tests: `test_registry_approval.py`, `test_planner_approval.py`, `test_engine_approval.py`,
  `test_callback_handler.py`, `test_approval_test_plugin.py`. 234 passed, 6 skipped (integration).

**Key design decisions:**

- **Planner-loop interception** (not pre-planner routing): sentinel returns from registry into the
  loop body, which detects it and returns immediately. Intelligence stays in the planner; the gate
  is at the execution boundary. This was the resolved architectural fork.
- **Claim-before-execute** (`status="executing"` committed before `registry.execute()`): prevents
  double-execution across crashes or concurrent taps. A post-crash re-tap sees `"executing"` (non-
  pending) and is rejected. Instagram double-post prevention is built in from day one.
- **`executing` added to the enum**: required to support the claiming pattern without a separate
  lock table. The status guard rejects all non-`"pending"` rows including `"executing"`.
- **`_approved` leading underscore**: signals "internal/trusted" — never appears in LLM-facing
  schemas, only passed programmatically by the callback handler.
- **`spec=Message` on mock**: `isinstance(callback.message, Message)` in the handler requires the
  mock to pass the isinstance check. Patching `Message` in the handler namespace doesn't work
  because `patch(_PATCH_MSG)` replaces it with a MagicMock which is not a valid isinstance target.

**What failed during implementation:**

- `patch("clients.telegram.handlers.Message")` approach for isinstance: replaced the class with
  a MagicMock, making `isinstance()` crash with `TypeError: isinstance() arg 2 must be a type`.
  Fixed by using `MagicMock(spec=Message)` on `callback.message` in the test helpers — this makes
  the mock pass isinstance checks against the real Message class.
- mypy: `dict` without type args in model column (`Mapped[dict]`) and in test signatures. Fixed to
  `Mapped[dict[str, object]]` and `dict[str, object]` respectively.
- mypy: `0003_pending_actions` migration not in the pyproject.toml per-module ignore list (only
  `0001_initial` and `0002_hnsw_index` were). Added `"0003_pending_actions"` to the override.
- ruff SIM117: nested `with patch(...): with patch(...):` blocks. Collapsed to single `with`.
- ruff F841: unused variables in test assertions. Removed dead diagnostic lines.

**Deferred / PC gate:**

- **MIGRATION REQUIRED on PC**: `alembic upgrade head` against real Postgres must apply
  `0003_pending_actions` (creates table, enum, indexes). This is a schema change → real-DB gate
  is mandatory before declaring done. Neon will receive this migration on next deploy.
- PC: `pytest -v` full suite including real-DB integration tests (0 skipped).
- PC: `docker compose up --build` + `/health` 200.
- Live end-to-end proof on phone: trigger `dummy_confirm_action` → proposal + buttons → tap
  Confirm → "✅ Done." / tap Cancel → "❌ Cancelled." / let expire → "⏰ Action expired." /
  restart bot mid-pending → buttons still work.
- Next slice: plug in Instagram auto-post as the first real `requires_approval` consumer.

---

## 2026-07-17 — Slice: Instagram Posting Integration

**What was built:**

- `integrations/r2.py`: `R2Client` — async Cloudflare R2 upload via SigV4 presigned PUT URLs.
  Uses `aws-request-signer` package (zero transitive deps, pure HMAC). `presign_url("PUT", ...,
  content_hash=UNSIGNED_PAYLOAD)` generates the signed URL; httpx PUT uploads the bytes. Returns
  `{public_base_url}/{key}`. Tenacity retries on 5xx/timeout; 4xx raises `IntegrationError`.
- `integrations/instagram.py`: `InstagramClient` — two-step IG Graph API v21.0 (`/media` then
  `/media_publish`, back-to-back, no readiness wait). Token expiry (error code 190 or "token" in
  message) raises a clear `IntegrationError("Instagram access token has expired or is invalid —
  needs refresh in .env")` rather than a generic error. Tenacity retries on 5xx/timeout.
- `plugins/base.py`: Added `needs_hosted_image: ClassVar[bool] = False` (after
  `requires_approval`). Also widened abstract `execute()` to `**kwargs: Any` (Liskov — required
  for mypy strict mode; all existing plugins updated to add `**kwargs: Any` to their signatures).
- `core/tools/registry.py`: Added `_INJECTED_CONTEXT_KEYS = frozenset({"image_url"})`.
  `execute()` now splits `raw_args` into `llm_args` (passed to `input_schema`) and `injected`
  (filtered through `inspect.signature` and forwarded as `**kwargs` to `plugin.execute()`).
  Added `get_plugin(name)` public method (used by engine, avoids `_plugins` private access).
- `core/engine.py`: Added `r2: R2Client | None = None` param. In proposal branch: looks up plugin
  via `registry.get_plugin(action_type)`. If `needs_hosted_image` is True: (a) no image →
  returns friendly "Please send a photo" early refusal, (b) no R2 configured → returns defensive
  error (unreachable in practice since plugin only registered with R2), (c) image present →
  `base64.b64decode` → `r2.upload` → inject `image_url` into `pa.action_payload` before storing
  the pending row. Ordinary photo critiques never touch R2 (this code runs only in the proposal
  branch, gated by `needs_hosted_image`).
- `plugins/instagram_post/`: New plugin — `requires_approval=True`, `needs_hosted_image=True`.
  `input_schema = InstagramPostInput(caption: str)` — only LLM-facing field. `execute()` receives
  `image_url` as an injected kwarg (not in schema), calls `ig.publish_photo(image_url, caption)`,
  returns `InstagramPostOutput(media_id, confirmation)`.
- `clients/wiring.py`: Constructs `R2Client` (all 5 R2 env vars must be set) then optionally
  `InstagramClient` + `InstagramPostPlugin` (additionally requires `INSTAGRAM_ACCESS_TOKEN` and
  `INSTAGRAM_USER_ID`). Both optional with warning logs when absent. Passes `r2=r2_client` to
  `CoreEngine`.
- `docker-compose.yml`: Added 7 env vars to `bot` service (R2 × 5, IG × 2). `app` and `worker`
  services don't need them.
- `pyproject.toml`: Added `aws-request-signer>=1.0` dependency.
- Tests: 270 passed, 3 skipped (integration). New: `tests/integrations/test_r2.py`,
  `tests/integrations/test_instagram.py`, `tests/plugins/test_instagram_post.py`. Extended
  `test_registry_approval.py` (injected-context extraction) and `test_engine_approval.py`
  (R2 upload branch, no-image refusal, no-R2 defensive guard).

**Key design decisions:**

- **R2 signing: `aws-request-signer`** (not boto3, not hand-rolled). boto3 would be 25–50 MB for
  what is pure HMAC arithmetic. `aws-request-signer` is ~5 KB, zero deps, client-agnostic. The
  key probe finding: `presign_url()` requires `content_hash=UNSIGNED_PAYLOAD` explicitly for PUT
  requests (raises `ValueError` otherwise) — the research estimated "~5 call-site lines" and that
  was accurate once the API was probed.
- **`_INJECTED_CONTEXT_KEYS` + `inspect.signature`**: mirrors the existing `user_id`/`db`
  injection pattern cleanly. Keys in `action_payload` that are engine-trusted (not LLM-supplied)
  are stripped before `input_schema` validation and forwarded only to plugins whose `execute()`
  explicitly declares the parameter. No plugin-side ClassVar needed — registry owns the concept.
- **`needs_hosted_image` check in proposal branch only**: photo critique continues to work
  unchanged. The R2 upload is only triggered when the planner proposes an action requiring a
  hosted image. This was the key architectural separation to protect.
- **No R2 cleanup after post**: storage is free-tier, deletion adds failure surface (a failed
  delete shouldn't mask a successful post), images may be useful for debugging.
- **R2 presigning probe required**: the `UNSIGNED_PAYLOAD` requirement was discovered by actually
  calling the library (not just the research). The API doc says "content_hash must be specified
  for PUT request" — this would have been a runtime failure if not caught here. Always probe new
  signing libraries against the real endpoint on the PC gate.

**What failed during implementation:**

- `aws-request-signer.presign_url()` — initial call without `content_hash` raised `ValueError:
  content_hash must be specified for PUT request`. Fixed by passing `content_hash=UNSIGNED_PAYLOAD`
  (imported from the same package).
- **R2 401 on PC probe**: presigned PUT returned 401 Unauthorized. Root cause: `Content-Type`
  header was sent in the PUT but NOT included in the signed set, so R2 rejected the request as
  tampered. Fix: pass `headers={"Content-Type": content_type}` to BOTH `presign_url()` (adds it
  to `X-Amz-SignedHeaders`) AND the `httpx.put()` call. The library's `headers` param exists
  exactly for this: any header that will be sent must be signed. Lesson: presigned URL signing
  is request-exact — unsigned headers sent with the request cause 401, not 403.
- mypy strict mode (`[override]`): adding `**kwargs: Any` to the abstract `execute()` required
  ALL existing plugin subclasses to also add `**kwargs: Any` to their signatures. The plan stated
  "no changes needed to existing plugins" — this was wrong under strict mypy. Mechanical fix
  across 9 plugins.
- `engine._process` proposal branch: using `self._registry._plugins.get(pa.action_type)` on a
  mock registry returned a MagicMock (truthy for `needs_hosted_image`), breaking existing tests.
  Fixed by adding `registry.get_plugin(name)` public method and configuring mock to return None
  for existing non-image tests.

**⚠️ OPERATIONAL LANDMINE — TOKEN EXPIRY:**
The Instagram long-lived access token was issued ~2026-07-17. Long-lived tokens are valid for
~60 days. **This token expires around 2026-09-15.** After that, all `instagram_post` calls will
fail with "Instagram access token has expired or is invalid — needs refresh in .env". To refresh:
```
GET https://graph.instagram.com/refresh_access_token
  ?grant_type=ig_refresh_token&access_token=<current_token>
```
Update `INSTAGRAM_ACCESS_TOKEN` in prod `.env` on the Oracle VM. This is a manual operation —
no automated refresh is built (deferred).

**Deferred / PC gate:**
- `aws-request-signer` must be probed against REAL R2 before declaring the slice done: `pip
  install aws-request-signer`, throwaway script that presigns + PUTs a test file + confirms it's
  fetchable via public URL. Mocked tests cannot validate the SigV4 signature is accepted by R2.
- PC: `pip install -e ".[dev]"` — verifies `aws-request-signer` installs cleanly.
- PC: `pytest -v` full suite including integration tests (0 skipped). No new migration (reuses
  `pending_actions` table from previous slice).
- PC: `docker compose up --build` + `/health` 200.
- Live end-to-end (the real thing): send photo + "post this to instagram with caption X" →
  proposal + Confirm/Cancel → tap Confirm → image appears on Instagram. Verify Cancel → no post.
  Verify double-tap Confirm → posts ONCE. Delete test posts after.
  The `ApprovalTestPlugin` can be removed or kept for testing when Instagram lands.

---

## 2026-07-20 — Fix: LLM double-confirm bug on approval-required tools

**Problem:**

After the Instagram slice landed (first post succeeded end-to-end), a second failure mode was
observed: the LLM would sometimes reply in TEXT ("Should I post this?") instead of calling
`instagram_post` directly. No tool call → no approval buttons → user replies "yes" in a new
message → photo context gone (`image_base64` is per-turn, not persisted) → "Please send a photo."
The approval flow was working correctly; the LLM was adding a redundant text confirmation step
BEFORE calling the tool.

**Root cause — two compounding issues:**

1. **Plugin description said "The user must confirm before the post goes live."** — The LLM read
   this and concluded it was responsible for getting confirmation via text. The approval flow's
   buttons ARE the confirmation mechanism, but the description implied the LLM should ask first.
   Classic mismatch: the description documented the user-facing behavior, not the LLM instruction.

2. **System prompt had no guidance on approval-required tools.** Nothing told the LLM: "call these
   tools directly — the system handles confirmation." The prompt also said "when the user sends a
   photo, provide a thoughtful critique" with no carve-out for posting intent, so photo+post-request
   sometimes defaulted to critique instead of tool call.

**Fixes (prompt/description only — no logic, no schema, no migration):**

- `plugins/instagram_post/plugin.py` — description rewritten: removed "must confirm" wording.
  Now explicitly says: call this tool DIRECTLY with the caption, do NOT ask for confirmation
  yourself, the system shows the confirmation prompt automatically, only call when a photo is
  present in the current message.

- `core/engine.py` `_SYSTEM_PROMPT` — two additions:
  (a) Photo routing: "if they explicitly ask to post/share to Instagram, call instagram_post
      immediately with the caption; otherwise provide a critique." Critique remains the default.
  (b) Approval-tool guidance: "Some tools require user approval. Call them directly with the
      required arguments — do NOT ask for text confirmation first. The system presents a
      confirmation prompt automatically. Asking yourself is redundant and breaks the flow because
      photo context is not available in a later reply."

**Key lesson:**

For any `requires_approval` tool, the plugin description AND the system prompt must both
explicitly tell the LLM to call the tool immediately. The LLM's natural instinct when it reads
"requires confirmation" is to ask in text — the description must counteract this by making clear
that the confirmation UI is the system's job, not the LLM's. This is a general pattern: whenever
the platform handles a UX step (approval, file upload, etc.), the prompts must say "don't do this
yourself, call the tool and let the system handle it."

**Verification:**

- `/verify` (ruff + black + mypy): clean, 0 issues.
- Real validation is live: send photo + post intent → LLM calls `instagram_post` directly →
  buttons appear immediately, no text "should I post?" step.

---

## 2026-07-16 — Slice 1: Vision input + photo critique

**What was built:**

- `core/schemas.py`: Added `image_base64: str | None` and `image_mime: str | None` to `CoreRequest`.
  `content: str` stays unchanged — carries the caption or default critique prompt.
- `core/engine.py`: `_process` now builds a content-part list (`[{type:input_image,...}, {type:input_text,...}]`)
  for the user `LLMMessage` when `image_base64` is set; falls back to plain string for text-only requests.
  Semantic search and episodic memory write always receive `request.content` (a plain string — caption or
  default prompt), never image bytes. Added one system-prompt clause instructing the model to critique
  composition/lighting/subject/suggestions when a photo is received.
- `clients/telegram/handlers.py`: Added `@router.message(F.photo)` handler (`handle_photo`). Downloads
  the highest-res photo via `message.bot.get_file` / `message.bot.download_file`, base64-encodes it,
  uses `message.caption` or `"Please critique this photo."` as `content`. Enforces the same allowlist
  guard as the text handler. Error handling mirrors `handle_message` exactly.
- `tests/core/test_engine.py`: 4 new tests — content-part list shape, semantic search receives string,
  memory write receives string, text-only path unchanged.
- `tests/clients/test_telegram_handlers.py`: 4 new tests — correct `CoreRequest` built with caption,
  default prompt when no caption, allowlist block, allowlist pass.

**Key design decisions:**

- Engine-level (not plugin): the LLM cannot supply raw image bytes as a tool argument; injecting the
  image directly into the user `LLMMessage` before the planner is the only clean path.
- `image_base64: str` (not `bytes`): future-proofs the REST path (`ChatRequest` adapter in the API
  route constructs `CoreRequest` in-process, so bytes would work today, but str is JSON-safe and
  consistent).
- `message.photo` and `message.bot` are guaranteed non-None when `F.photo` fires, but mypy doesn't
  know that — added `assert` statements to narrow types rather than `# type: ignore` (asserts are
  defensive checks, not suppressions).
- Provider layer needed zero changes: `_to_items()` already passes `list[dict]` user content
  through unchanged on the plain-message branch.
- Telegram always delivers photos as JPEG regardless of original upload format; `image/jpeg` is always correct.

**What failed during implementation:**

- mypy caught 5 errors: `message.photo` typed as `list[PhotoSize] | None` (not indexable without
  guard), `message.bot` typed as `Bot | None` (get_file/download_file not callable), `file.file_path`
  typed as `str | None` (download_file expects `str | Path`), `buf` typed as `BinaryIO | None`.
  Fixed with `assert` narrowing guards.

**Deferred / PC gate:**

- No migration (CoreRequest is not a DB model).
- PC: `pytest -v` full suite + `docker compose up --build` + `/health` 200.
- Live: send real photo (no caption) → genuine critique; send with caption → answers that question.
  Watch logs to confirm image carried through and model called.

---

## 2026-07-15 — Slice: list_reminders + cancel_reminder plugins

**What was built:**

- `plugins/reminders/schemas.py`: Added `ReminderSummary`, `ListRemindersInput` (zero fields),
  `ListRemindersOutput`, `CancelReminderInput`, `CancelReminderOutput`, and matching config classes.
- `plugins/reminders/list.py` (`ListRemindersPlugin`): Queries `WHERE user_id=injected AND
  sent_at IS NULL ORDER BY remind_at ASC LIMIT 50`. Returns `reminder_id` (str UUID), `message`,
  `remind_at_local` (via `format_local`), and `remind_at_utc`. Read-only; no flush.
- `plugins/reminders/cancel.py` (`CancelReminderPlugin`): SELECT scoped by id AND user_id AND
  `sent_at IS NULL`; if found, `db.delete(reminder)` + `db.flush()`; if not found (wrong user,
  already sent, or bad UUID), returns friendly `status="not_found"`. Engine owns the commit.
- `clients/wiring.py`: Registered `ListRemindersPlugin` and `CancelReminderPlugin` unconditionally.
- `tests/plugins/test_list_cancel_reminders.py`: 13 unit tests with mocked DB.

**Key design decisions:**

- `ListRemindersInput` has zero LLM-supplied fields — the tool takes no arguments. Under strict
  schema normalization, an empty `properties` dict produces `required=[]`, which is valid for
  OpenAI strict mode.
- Cancel = hard DELETE (not a status update) since `Reminder` has no status enum. The
  `sent_at IS NULL` filter on the SELECT is the race guard: the worker uses a separate session
  with `FOR UPDATE SKIP LOCKED`, so our SELECT either catches a still-pending row (cancellable)
  or misses it (worker already fired it → not found). No window exists where both succeed.
- `db.delete` is `AsyncMock` in tests (SQLAlchemy's `session.delete` is technically synchronous,
  but mocking it as `AsyncMock` is harmless in unit tests — the real behavior is confirmed by the
  PC integration gate).

**Deferred / PC gate:**
- No migration. Schema-equivalence test must pass unchanged on PC.
- Live bot test: create two reminders, "what reminders do I have?" → listed; "cancel the X
  reminder" → planner does list→cancel; wait for fire time: cancelled one must NOT fire.

---

## 2026-07-15 — Slice: Task management plugins (create_task / list_tasks / complete_task)

**What was built:**

- `plugins/tasks/schemas.py`: Pydantic input/output/config models for all three plugins. Optional
  fields typed as `X | None` (not defaulted) so strict schema normalization keeps them in
  `required` while the LLM can pass `null`.
- `plugins/tasks/create.py` (`CreateTaskPlugin`): Creates a `Task` row; reuses `format_local`
  from `core/timeutil.py` for due-date display in the confirmation. Naive datetimes treated as UTC
  (same pattern as `RemindersPlugin`). `priority=None` falls back to 1 (DB default).
- `plugins/tasks/list.py` (`ListTasksPlugin`): Queries open tasks (`pending` + `in_progress`) by
  default; accepts explicit `status_filter` for other statuses. Ordered by priority desc / due_at
  asc / created_at asc; capped at 50 rows. Returns `task_id` as a string in every summary so the
  LLM can hand it to `complete_task`.
- `plugins/tasks/complete.py` (`CompleteTaskPlugin`): Marks a task `completed`. Scopes the lookup
  by BOTH `task.id` AND `user_id` — task_id alone never authorizes. Invalid UUID and not-found
  return a friendly `status="not_found"` result (not an exception), so the LLM can relay the
  message gracefully.
- `clients/wiring.py`: Registered all three plugins unconditionally (no external API dependency).
- `tests/plugins/test_tasks.py`: 25 unit tests covering schema, execute(), and health_check() for
  all three plugins with mocked DB.

**What failed / was fixed:**

- `_normalize_for_openai_strict` makes every `properties` key `required`. Optional fields typed
  with a default (e.g. `priority: int = 1`) would be ABSENT from `properties` after Pydantic's
  JSON schema generation (defaulted fields are omitted). Solution: type them as `X | None` with no
  default — they appear in `properties`, the LLM passes `null`, and the plugin applies its own
  default. Confirmed against `ReminderInput` (which uses the same pattern).
- `Task.status` is `Mapped[str]` (no Python Enum class) — bare string comparisons and assignments
  (`task.status = "completed"`, `.where(Task.status == "pending")`) are correct. This would only
  surface on real Postgres if it were wrong, not in mocked unit tests.
- `test_list_tasks_default_filter_queries_open_statuses`: initial approach compiled the SQLAlchemy
  statement with `literal_binds=False` and asserted "pending" appeared in the string. SQLAlchemy
  uses `__[POSTCOMPILE_...]` for `IN` lists at that setting, so the strings don't appear. Fixed:
  changed to a simple constant-value assertion on `_OPEN_STATUSES`. Authoritative verification
  of the filter remains the live bot test.
- mypy: `list` without type arg in `_make_db_with_query`. Fixed to `list[MagicMock]`.

**Key design decisions:**

- One plugin per operation (not one plugin with an `action` field) — matches existing convention.
- `list_tasks` default is open tasks only (`pending` + `in_progress`). "What's on my list"
  means actionable work; completed/cancelled tasks require an explicit `status_filter`.
- The multi-step `list → match → complete` flow relies entirely on the ReAct planner's existing
  multi-turn tool-call support. No new planner changes needed. The critical requirement is that
  `list_tasks` output always includes `task_id` — confirmed in output schema and tests.

**Deferred / PC gate:**
- No migration (schema existed from slice 0001). Schema-equivalence test must pass unchanged on PC.
- Live bot test is required to verify: (1) create, (2) list shows it, (3) "complete the X task"
  triggers list→match→complete multi-step, (4) list no longer shows it as open.

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

---

## 2026-07-21 — Slice 2: Worker-triggered approval for scheduled Instagram posts

**Goal:** arq worker proactively initiates the Telegram approval flow for scheduled posts — inverse of the reactive approval path (user message → engine → proposal).

**Key design decisions resolved during planning:**
- Image uploaded to R2 at schedule-creation via a lazy memoizing provider closure (engine-owned), never at trigger time — the R2 URL is stored in `scheduled_posts.image_url` at row creation.
- `image_url` injected into the `injected` dict (not `raw_args`) so it bypasses `input_schema` validation, same channel as the existing approval path (`_INJECTED_CONTEXT_KEYS`).
- 3-phase poll: EXPIRE (flip overdue `pending` → `expired`) → TRIGGER loop (commit-per-post + commit-before-send) → RECONCILE (map terminal pending_action statuses to scheduled_post statuses). Needed because nothing else was expiring `pending_actions` — stuck rows would permanently block the collision check.
- Collision on trigger: skip + retry next poll (don't cancel in-flight approvals).
- `_approval_keyboard_dict` helper in `telegram_notifier.py` builds the same `ok:{uuid}` / `no:{uuid}` callback_data that `handle_callback` (handlers.py:55,278) already parses — zero new callback code.

**What failed / needed correction during planning:**
- First plan injected `image_url` into `raw_args` before schema validation (wrong). Corrected: inject into `injected` dict after the approval gate, before `accepted_injected` filter.
- First plan had no expiry sweep anywhere — `pending_action.status='expired'` was never written by the worker. Added Phase 1 to poll_scheduled_posts to flip overdue rows.
- `call_count == 1` assertion was missing `assert` keyword (B015 ruff error). Fixed.
- `call` imported but unused from `unittest.mock` (F401). Removed.
- `result = await plugin.execute(...)` in test had unused binding (F841). Removed.
- Import sort issue (I001) in `test_lazy_image_upload.py`. Auto-fixed with `ruff --fix`.
- `_MockDB.push_execute_response` was setting `scalars().all()` but not `scalar_one_or_none()`. Fixed by setting both when `scalars_result` is provided.
- `_HostedImagePlugin` stub in lazy upload tests wasn't raising `PluginError` when `image_url is None`. Added explicit None check.
- Worktrees only contain tracked files — untracked files from main checkout (core/timeutil.py, models/pending_action.py, clients/errors.py, base planner types, etc.) had to be manually copied in.

**What worked:**
- Lazy memoizing provider pattern (closure with `list[str]` as mutable cell) cleanly threads R2 upload through engine → planner → registry without the registry importing or knowing about R2.
- Per-post commit + commit-before-send ordering guarantees the user can always tap Confirm after receiving the photo.
- 3-phase poll architecture correctly handles all terminal states: worker-expired (phase 1+3), callback-confirmed (handle_callback + phase 3), callback-cancelled, callback-failed.
- mypy clean (exit code 0). ruff + black clean. 257 unit tests pass, 1 skipped.

**Deferred to PC authoritative gate:**
- `alembic upgrade head` against real Postgres (new ENUM + table + indexes)
- Full pytest -v (0 skipped) including boto3/httpx-dependent tests excluded on VM
- `docker compose up --build` + `/health` 200 + worker log shows both cron jobs registered
- Live test on Oracle VM: 6 scenarios (schedule, confirm, cancel, double-tap, expiry)

---

## 2026-07-22 — Fix: structlog tracebacks + LLM timeout safety

### Problem

Prod logs showed zero tracebacks for any error — every `log.exception()` call
produced an event dict with no exception key. The structlog processor chain in
`core/logging.py` was missing `format_exc_info`. `StackInfoRenderer` only
handles `stack_info=` (a different kwarg); `exc_info=True` (set by
`log.exception`) was silently dropped before the renderer. This made all prod
error diagnosis require circular guessing — the "post these at 6:53am" timeout
investigation this session is the clearest example: 60+ second failure, no
traceback, no root cause.

The arq worker (`infra/worker/worker_settings.py`) was also missing
`configure_logging()` in `startup()`, so worker-side errors (scheduled-post
fires, Confirm-button execution) were using unconfigured structlog defaults —
completely opaque.

### What was tried / what failed

The 6:53am failure showed ~61s of total silence in prod logs with no
"POST /v1/responses" line. Initial hypothesis was routing (wrong plugin
called) or R2 (image upload hung). Routing was fixed (verb-agnostic
`build_content_plan` trigger), but the 6:53am error pattern wasn't routing
— there was no tool call, just a hang then a generic fallback with no info.
Without a traceback we couldn't confirm whether the hang was OpenAI connection,
OpenAI read timeout, httpx below the SDK, or something else entirely.

### What was built

**Bug 1 — logging:**
- `core/logging.py`: inserted `structlog.processors.format_exc_info` between
  `StackInfoRenderer()` and the renderer. One line. Now both JSON (prod) and
  ConsoleRenderer (dev) emit full tracebacks.
- `infra/worker/worker_settings.py`: `startup()` now calls
  `configure_logging(log_level=s.log_level, environment=s.environment)` before
  any other setup. Worker now emits structured JSON + real tracebacks.

**Bug 2 — LLM timeout safety net (unconfirmed cause):**
- `core/llm/openai_provider.py`: added `httpx.TimeoutException` alongside
  `openai.APITimeoutError` in the except clause. The OpenAI SDK wraps most
  timeouts as `APITimeoutError`, but connection-phase timeouts can surface as
  raw `httpx.TimeoutException` before the SDK catches them. This is a
  reasonable safety net, not a confirmed fix — the REAL cause will be revealed
  by the next occurrence now that tracebacks emit.
- `core/config.py`: lowered `openai_timeout_seconds` default from 60s to 30s.
  60s was silent failure territory; 30s gives a faster user-facing response
  ("AI provider timed out. Try again later.") via the `LLMTimeoutError` path
  that was already wired to `clients/errors.py`.

### Key insight

`LLMTimeoutError` was already handled — `user_message()` in `clients/errors.py`
returns a friendly message for it, and the Telegram handler catches it as
`PlatformError`. The problem was that `httpx.TimeoutException` was NOT caught,
so it fell through to the generic `except Exception` branch → generic fallback
+ no traceback. Once tracebacks emit, the next occurrence will confirm whether
this was the actual gap.

**Worker errors were completely dark.** Every scheduled-post fire, carousel
confirmation, and expiry reconciliation that failed silently was invisible in
worker logs. The `configure_logging()` fix closes this gap for all future
worker errors.

### Tests

- `tests/core/test_logging.py`: two tests capture stdout in both `production`
  (JSON) and `development` (console) modes and assert real traceback text
  appears in the output. These would have caught the missing `format_exc_info`
  immediately.
- `tests/core/llm/test_openai_provider.py`: added
  `test_complete_httpx_timeout_maps_to_llm_timeout_error` — raises
  `httpx.ConnectTimeout` from the mock, asserts `LLMTimeoutError` is raised.

### Deferred

- Confirm the actual 6:53am cause on next occurrence (tracebacks now emit).
- Consider adding tenacity retry on `LLMTimeoutError` (currently only
  `LLMRateLimitError` is retried); deferred until we know if timeouts are
  transient or persistent for this workload.

---

## 2026-07-23 — Slice 5A + 5B: multi-item content plans + conversational draft editing

### What was built

**Slice 5A — Multi-item scheduled content plan:**
- `models/content_plan.py`: new `ContentPlan` model. `scheduled_posts` extended with
  `post_type`, `image_urls` JSONB, `plan_id` FK. Migration 0005.
- `plugins/build_content_plan/`: plan creation, list, cancel sub-plugins.
- Worker (`core/scheduler/jobs.py`): carousel branch — `post_type='carousel'` rows dispatch
  `instagram_carousel` action type at scheduled time.
- System prompt + `instagram_carousel` description updated: verb-agnostic routing so any
  multi-photo + future time → `build_content_plan` regardless of verb.
- R2 upload errors wrapped as `IntegrationError` with `log.exception` before raise.

**Slice 5B — Conversational draft editing:**
- `build_content_plan` changed to `requires_approval=False`. Now creates `status='draft'`
  `ContentPlan` row with `items` JSONB (serialized plan items) and `image_urls` JSONB (flat
  R2 URL list). Auto-discards any prior draft for the user. Returns draft summary as plain
  text — no Confirm button at this stage.
- `edit_draft_plan`: structured edit ops (drop/edit_caption/edit_time/reorder/merge/split).
  CRITICAL invariant: `image_indices` always reference the ORIGINAL `plan.image_urls` list
  and are never renumbered across mutations — `approve_draft_plan` resolves them correctly
  regardless of how many edits occurred.
- `approve_draft_plan` (`requires_approval=True`): reads draft items + image_urls, creates
  `ScheduledPost` rows, sets `status='approved'`. Confirm button appears only here.
- `discard_draft_plan`: sets `status='discarded'`.
- Migration 0006: adds `items JSONB` + `image_urls JSONB` to `content_plans`.

**R2 upload wiring (key design):**
- `needs_hosted_images=True` was coupled to the approval path (only fired inside the
  `pending_action` branch). With `build_content_plan.requires_approval=False`, the old path
  never fires. Solution: lazy `_image_urls_provider` callable (mirror of existing
  `_image_url_provider`). Closure built in engine, only called when registry actually
  dispatches to a `needs_hosted_images=True, requires_approval=False` plugin. Zero wasted
  R2 uploads for critique-only multi-photo messages.
- Three files changed: `registry.execute()` (new `_image_urls_provider` branch),
  `react.py` (forward param), `engine.py` (closure + pass to planner).

**Draft routing:**
- Engine queries for `status='draft'` ContentPlan before building the system prompt.
  If found, injects `draft_block` with rendered item list + routing instructions for
  edit/approve/discard. Unrelated messages ("what's the weather") leave the draft intact.
- Routing stress cases to verify live: "actually make #2 a carousel" → `edit_draft_plan`,
  "yeah that works" → `approve_draft_plan`, "what's the weather" → normal, "cancel" →
  `discard_draft_plan`.

### What failed / was corrected

1. **Split producing empty caption**: initial plan had split leaving new item with empty
   caption. Corrected: split copies source caption to both resulting items.
2. **Stale draft accumulation**: first plan left `status='draft'` rows orphaned if user
   started a second plan. Fixed: `build_content_plan.execute()` auto-discards all existing
   drafts for the user before creating the new one.
3. **Eager R2 upload considered and rejected**: pre-planner upload would fire on every
   multi-photo message. Lazy provider is correct — upload only when plugin is actually invoked.

### Key insight

The structured-op approach (not full-regen) was essential. Full regen would have re-triggered
`requires_approval=True` on each edit, creating a new `PendingAction` and cancelling the
existing draft. Structured ops + `requires_approval=False` on `edit_draft_plan` mean edits
return results directly through the planner without any approval round-trip.

The image_indices invariant is the correctness spine of the whole feature. Items reference
original images by stable index — no renumbering after drops/merges/splits/reorders. The
regression test (merge + split + reorder → approve → verify correct image_urls per post) is
the guard.

### Deferred

- Live routing verification (requires deployed build): hammer "actually make #2 a carousel",
  "yeah that works", "what's the weather", "cancel" — all must route correctly.
- Editing already-APPROVED plans (change time on a post already scheduled) — different flow,
  later slice.
- Undo last edit — straightforward addition once structured ops are proven in prod.
