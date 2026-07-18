# Design: Human-in-the-Loop Approval Flow

**Status:** Design draft (pre-implementation). Not built yet. This is the architectural design to
review and refine before grounding + plan-mode.

## Purpose
A reusable engine capability: for high-stakes actions (first consumer: Instagram auto-post; later:
calendar, any external write), the engine must **pause, propose the action to the user, and execute
only after explicit confirmation** — instead of executing autonomously as it does today.

This is **action-agnostic**: it holds "a proposed action of type X with parameters Y," presents it,
and on confirm dispatches to whatever executes type X. Instagram posting is just the first user.

## The core problem it solves
Today the engine is single-shot: request → planner → tools execute immediately → response. One
`handle_request` completes the whole cycle. Approval breaks this: the action is proposed in one
turn and confirmed in a *later, separate* message. So we need **state that survives between two
messages** (the proposal and the confirmation). `CoreRequest.session_id` is per-message (fresh UUID
each request), so there is no persistent thread today — the pending action must live in the DB.

## Decisions (locked)
1. **Storage: dedicated `pending_actions` table** (migration required).
2. **Confirmation UX: Telegram inline buttons** [Confirm] / [Cancel] (deterministic; a button press
   carries an unambiguous callback payload — no free-text "yes" parsing).
3. **Fully generic**: holds any action type; Instagram is the first consumer, not hardcoded.

---

## Proposed architecture

### 1. The `pending_actions` table (new migration)
Columns (draft — refine at grounding against existing model conventions):
- `id` UUID PK
- `user_id` UUID FK -> users.id (CASCADE) — user-scoped, like tasks/reminders
- `action_type` str — e.g. "instagram_post" (the generic discriminator; dispatch key)
- `action_payload` JSONB — the action's parameters (e.g. {caption, image_ref, ...}); action-agnostic
- `status` enum — pending / confirmed / cancelled / expired / failed
- `preview_text` Text — the human-readable proposal shown to the user ("Post this caption + photo…")
- `created_at`, `updated_at` (tz)
- `expires_at` (tz) — when this pending action goes stale (see Edge Cases)

Notes:
- Enum handling per CLAUDE.md (postgresql.ENUM create_type=False, explicit CREATE TYPE in migration).
- Consider: at most ONE active (status=pending) action per user at a time? (See "superseding".)
  A partial unique index on (user_id) WHERE status='pending' could enforce single-pending-per-user
  — decide during design.

### 2. How an action gets proposed (the pause)
Two sub-questions the design must answer (OPEN — see Open Questions):
- **How does the engine/planner decide an action needs approval?** Candidate: the *plugin* that
  performs the high-stakes action is marked `requires_approval = True` (a new plugin-contract
  field). When the planner would call such a plugin, the engine intercepts: instead of executing,
  it writes a `pending_actions` row (status=pending, payload = the tool args, preview_text = a
  generated summary) and returns a proposal response + inline buttons — WITHOUT executing the tool.
- This means the engine's tool-dispatch path gains a branch: "if the resolved plugin
  requires_approval and there is no confirmation context, DON'T execute — propose instead."

### 3. Holding state / ending the turn
- After writing the pending row and sending the proposal (+ buttons), the turn ENDS. The action is
  NOT executed. The `pending_actions` row (status=pending) is the held state — survives restarts
  (it's in Postgres, not memory).

### 4. The confirmation (resume)
- Telegram inline buttons: the proposal message carries an inline keyboard with two buttons whose
  callback_data encodes the pending_action id + the choice, e.g.
  `approve:{pending_id}` and `reject:{pending_id}`.
- A NEW aiogram **callback query handler** (`@router.callback_query(...)`) handles the button press:
  - Parse callback_data -> (pending_id, choice).
  - Load the pending_actions row; verify it belongs to THIS user (user-scope security — the
    callback's from_user.id must map to the row's user_id) AND status is still `pending` AND not
    expired.
  - On **approve**: dispatch the action (action_type -> the executor), set status=confirmed (or
    failed if execution errors), edit the message to reflect the outcome ("✅ Posted" / "❌ Failed:
    …"), and remove the buttons.
  - On **reject**: set status=cancelled, edit message ("Cancelled."), remove buttons.
  - Guard: if the row is already non-pending (double-tap, or expired) -> friendly "This action was
    already handled / has expired," no double-execution.

### 5. Dispatch on confirm (the generic executor)
- action_type -> executor mapping. The confirmed action needs to actually run. Options (OPEN):
  - The executor is the SAME plugin that would have run, now invoked in an "approved" mode (the
    engine calls plugin.execute with the stored payload + injected user_id/db).
  - Or a small dispatch registry keyed by action_type.
- Must reuse trusted-context injection (user_id/db injected, never from the stored payload's
  untrusted fields — though here the payload was generated by our own engine, still scope by the
  row's user_id, not anything the button could forge).

---

## Edge cases (this is where it gets subtle — all must be handled)
1. **Expiry:** a pending action not confirmed within `expires_at` must NOT execute. The callback
   handler checks expiry on button press (reject stale). Also: should expired rows be swept?
   (A periodic job could mark expired, or we check lazily at press time. Lazy check is simpler and
   sufficient — decide.)
2. **Superseding:** user proposes action A, then proposes action B before confirming A. Options:
   (a) enforce single-pending-per-user — proposing B cancels/replaces A's pending row; (b) allow
   multiple pending, each with its own buttons. Single-pending is simpler and less confusing for a
   personal bot — LEAN single-pending (proposing a new high-stakes action supersedes/cancels any
   prior un-confirmed one, with a note).
3. **Double-tap / stale button:** user taps Confirm twice, or taps a button on an old message. The
   status check (only execute if status=pending) prevents double-execution; second tap gets
   "already handled."
4. **Restart safety:** pending rows live in Postgres, so a bot restart doesn't lose them; the
   buttons still work after restart (callback_data carries the id; the handler reloads the row).
5. **Unrelated message while pending:** user has a pending post, then sends "what's the weather?"
   With inline BUTTONS (not free-text), this is clean: the weather message is just a normal request;
   the pending action stays pending (its buttons still live on the proposal message) until tapped or
   expired. No confusion — because we're NOT interpreting free-text as confirmation. (This is a big
   advantage of buttons over free-text: no "is this message a confirmation?" ambiguity.)
6. **Execution failure on confirm:** the action runs but the external call fails (e.g. IG API error).
   Set status=failed, tell the user it failed (reuse the graceful-error user_message pattern), don't
   leave it dangling as pending.

---

## What this slice INCLUDES vs. EXCLUDES
**Includes (this design → build):**
- The `pending_actions` table + migration.
- The engine branch: requires_approval plugins get proposed, not executed.
- The `requires_approval` plugin-contract field.
- The proposal response + Telegram inline keyboard.
- The callback_query handler: approve/reject, with all the guards (user-scope, status, expiry).
- The generic dispatch-on-confirm.
- A **test/dummy approval-requiring action** to prove the flow end-to-end WITHOUT needing Instagram
  (e.g. a trivial "requires_approval" echo/no-op plugin, or wire it to an existing safe action) so
  the whole pause→propose→confirm→execute cycle is provable in isolation.

**Excludes (later slices):**
- Instagram Graph API, OAuth, image hosting, the actual posting (Slice: auto-post, needs Meta app
  review). The approval flow is built + proven with a DUMMY action first; Instagram plugs in later
  as a consumer.
- Free-text confirmation (we chose buttons).

---

## RESOLVED ARCHITECTURE (grounding complete — decision made)

**The fork (planner-loop interception vs. pre-planner routing): RESOLVED → Option (a),
planner-loop interception.** Reasoning: the user will trigger posts CONVERSATIONALLY ("post that
with the sunset pic") from multi-turn context, not via rigid commands. Pre-planner routing (b)
would require a brittle pre-LLM classifier to detect intent + extract params from the raw message
alone, which fights the LLM-first architecture and can't resolve conversational references like
"that". Option (a) lets the planner (LLM) understand intent from full context and assemble the
action, then the approval gate intercepts AT THE EXECUTION BOUNDARY. Intelligence stays in the
planner; the safety gate sits at execution. This also keeps posting consistent with every other
capability (all conversational, no rigid commands).

**Confirmed integration points (from grounding — actual code):**
- **PluginBase** (plugins/base.py): add `requires_approval: ClassVar[bool] = False`. Safe default,
  no `__init_subclass__` enforcement, existing plugins untouched. Clean.
- **ToolRegistry.execute** (registry.py:80–85): interception seam between the `plugin =
  self._plugins.get(name)` lookup and the `await plugin.execute(...)` call. If
  `plugin.requires_approval` AND not an approved-dispatch context → return an approval SENTINEL
  instead of executing (e.g. `{"__approval_required__": True, "tool": name, "args": raw_args,
  "preview": <summary>}`).
- **ReActPlanner loop** (react.py:106–122): the for-loop over `llm_resp.tool_calls` currently has
  NO early-exit. ADD a hook: after `registry.execute` returns, if the result is the approval
  sentinel → STOP (break the batch loop AND the outer iteration loop) and return a PlannerResult
  carrying the pending action. Multi-tool-batch handling: if a batch contains an approval-required
  tool, pause the WHOLE turn before executing any remaining batch tools (the high-stakes action
  won't realistically be batched; naive "pause on first, discard rest of batch" is acceptable — the
  user re-triggers after approving).
- **PlannerResult / PlannerBase** (contract change — small, clean): add an optional
  `pending_action: PendingAction | None = None` field (safe default None). The planner sets it when
  it hits the approval sentinel; the engine checks it after `run()`.
- **engine._process** (engine.py after ~line 154, where planner.run() returns): after `run()`, if
  `plan_result.pending_action` is set → write the `pending_actions` row (status=pending,
  action_type, action_payload, preview_text, expires_at), and return a CoreResponse that signals
  the client to render the proposal + inline buttons (NOT execute). If not set → normal flow
  (write memory, return content) as today.
- **Confirmed dispatch (on button press):** the callback handler loads the pending_actions row,
  validates (user-scope + status=pending + not expired), and calls `registry.execute(action_type,
  action_payload, user_id=..., db=...)` DIRECTLY — bypassing the planner (decision already made).
  This is the "already_approved context" so the registry interception does NOT re-trigger (pass a
  flag so execute actually runs the plugin this time). Then set status=confirmed/failed, edit the
  Telegram message with the outcome, remove buttons.

**How the CoreResponse carries a proposal to the client (design point to finalize in plan-mode):**
CoreResponse currently has content/memories_written/tool_calls_made/error. A proposal needs to
convey: the preview text + the pending_action id (for the button callback_data) + a signal "render
buttons." Options: add an optional `pending_action_id`/`proposal` field to CoreResponse, OR the
Telegram handler detects the proposal another way. Decide in plan-mode; a small optional
CoreResponse field is likely cleanest (and unlike the vestigial `error` field, this one is read).

## Remaining open questions (resolve in plan-mode / at build)
1. **aiogram callback_query specifics** — InlineKeyboardMarkup construction, callback_data 64-byte
   limit (UUID=36 chars fits with a short prefix like `ok:`/`no:`), callback handler registration
   (mind the routing-order lesson — register appropriately, and remember the F.text/F.photo
   handlers already exist). PROBE the real aiogram API for inline keyboards + callback_query; do
   NOT assume the construction (this session's repeated lesson).
2. **The "already_approved" flag through registry.execute** — how the confirmed-dispatch path tells
   the registry "actually execute this time, don't re-propose." A keyword arg on execute
   (`_approved: bool = False`) is simplest. Confirm it threads cleanly.
3. **How the planner summarizes the action for the preview** — the sentinel needs a human-readable
   preview ("Post this caption + photo to Instagram: …"). Where does that text come from — the
   plugin generates it, or the engine builds it from action_type + payload? Decide.
4. **Migration = schema change** → real-DB gate + applies to Neon on deploy. First migration in a
   while; treat with migration caution (test against real Postgres on PC, note Neon impact).
5. (Superseded original Q2 below — kept for history.)

### (Historical) Original open questions before grounding
2. **Where exactly in the engine/planner the "propose instead of execute" branch lives.** The ReAct
   planner calls registry.execute(tool, args, ...). The interception could be in the registry or in
   the engine around the planner. Grounding must show the exact dispatch path so we branch cleanly.
   SUBTLE: the planner is mid-loop when it calls a tool — pausing means the planner must ALSO stop
   its loop and return, not continue planning. How does the planner currently handle a tool that
   returns a "this needs approval, I paused" signal vs. a normal tool result? This is the trickiest
   integration point — the planner's loop wasn't built to be interrupted mid-plan.
3. **How the confirmed action re-enters execution.** On confirm, we call the executor directly
   (bypassing the planner, since the decision's already made) — confirm this is clean and that the
   plugin can be invoked outside a planner loop with the stored payload.
4. **aiogram callback_query specifics** — the inline keyboard construction, callback_data size
   limits (Telegram caps callback_data at 64 bytes — a UUID is 36 chars, fits with a short prefix),
   and the callback handler registration (alongside the message/photo handlers, mind the routing
   order lesson). PROBE the real aiogram API for InlineKeyboardMarkup + callback_query, don't assume.
5. **Migration = schema change** → real-DB gate + applies to Neon on deploy (per CLAUDE.md). This is
   the first migration in a while; treat it with the migration caution.
6. **Does the planner even need to be involved for the proposal?** Alternative simpler design: maybe
   high-stakes actions DON'T go through the planner as tools at all — maybe "post to Instagram" is
   detected earlier and routed straight to the propose-flow, bypassing the ReAct loop. This might be
   architecturally cleaner than interrupting the planner mid-loop (see open Q2). Worth weighing:
   planner-integrated (approval as a tool-dispatch branch) vs. pre-planner routing (approval as a
   separate path). This is a genuine fork to resolve in design.

## The trickiest part (flagged for careful design)
Open Q2/Q6 are the crux: **the ReAct planner's loop was not designed to pause mid-plan.** Making an
action "propose then stop" either means (a) teaching the planner to handle an "I paused for approval"
tool result and cleanly exit its loop, or (b) routing high-stakes actions OUTSIDE the planner
entirely (detect → propose → confirm → execute, never entering the ReAct loop). Option (b) may be
much cleaner. This must be resolved before building — it's the load-bearing architectural decision.
