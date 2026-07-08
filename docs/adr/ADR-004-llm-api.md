# ADR-004: LLM API Surface and Model Selection

**Status:** Accepted  
**Date:** 2026-07-07

## Context

The platform requires:
- Multi-step tool-calling loop (ReAct planner)
- Streaming responses
- Text embeddings (1536-dim, for pgvector)
- A single provider seam so swapping providers touches exactly one file

OpenAI is the sole provider for v1.

## Decision

**Use the OpenAI Responses API (`client.responses.create()`), not Chat Completions. Use the GPT-5 family. No separate reasoning model.**

## Verified Model IDs and Specs

Fetched from `developers.openai.com/api/docs/models` on **2026-07-07**:

| Role | Model ID | Snapshot ID | Context | Max Output | Deprecation |
|---|---|---|---|---|---|
| Default (complex planning, synthesis) | `gpt-5.5` | `gpt-5.5-2026-04-23` | 1,050,000 tokens | 128,000 tokens | None listed |
| Fast/cheap (routine steps, classification) | `gpt-5.4-nano` | `gpt-5.4-nano-2026-03-17` | 400,000 tokens | 128,000 tokens | None listed |
| Alternative fast | `gpt-5.4-mini` | `gpt-5.4-mini-2026-03-17` | 400,000 tokens | 128,000 tokens | None listed |

**Embedding model:** `text-embedding-3-small` — 1536 dimensions (default), $0.02/1M tokens. No deprecation announced as of this date.

> **Provisional:** The exact snapshot IDs, context sizes, and pricing above are correct as of 2026-07-07 but **must be re-verified against the live models page** (`developers.openai.com/api/docs/models`) when the OpenAI adapter is built in Phase 1. The GPT-5 family choice stands; the snapshot-level specifics are provisional.

## Rationale

### Responses API over Chat Completions

- The Responses API is OpenAI's current primary API surface as of mid-2026. Assistants API sunset expected in 2026; Chat Completions is being soft-deprecated for new agentic projects.
- Responses API uses `input[]` arrays of typed Items (not `messages`), with native tool-call loop support — the correct interface for a ReAct planner.
- `client.responses.create()` in the Python SDK (`openai >= 1.50`) is stable and async-compatible via `AsyncOpenAI`.

### Two model tiers (not one)

`gpt-5.5` handles complex steps: full planning, final answer synthesis, memory importance scoring.
`gpt-5.4-nano` handles cheap/fast steps: tool-result summarisation, input classification, sub-agent routing.

This is a cost optimisation, not a capability split. Both tiers go through the single `LLMProvider` seam. The planner selects the model via `LLMConfig.model`; the provider is model-agnostic.

### No separate reasoning model

The GPT-5 family exposes `reasoning_effort` (`none` / `low` / `medium` / `high` / `xhigh`) as a parameter on any model. There is no separate `o3`-style reasoning model — the planner controls reasoning depth per step via `LLMConfig.reasoning_effort`, not by switching to a different model string.

### Embedding model: 1536 dims

`text-embedding-3-small` at its default 1536 dimensions matches the pgvector `VECTOR(1536)` column. Keeping at 1536 (not reducing) avoids re-embedding all rows if we later need full-quality recall. If the model is deprecated, the successor is `text-embedding-3-large` (3072 dims, $0.13/1M tokens) — a column migration and full re-embed would be required.

## LLM Provider Seam

`core/llm/openai_provider.py` is the **only file** in the codebase that imports `openai`. All callers use `LLMProvider` from `core/llm/base.py` and pass `LLMMessage` objects.

### Translation: LLMMessage → Responses API typed Items

| `LLMMessage.role` | Responses API Item |
|---|---|
| `system` | `{"role": "system", "content": "..."}` |
| `user` | `{"role": "user", "content": "..."}` (content array for multi-part) |
| `assistant` (text) | `{"role": "assistant", "content": "..."}` |
| `assistant` (tool calls) | `{"role": "assistant", "content": [{tool_call items}]}` |
| `tool_result` | `{"type": "function_call_output", "call_id": ..., "output": str(result)}` |

**Critical:** `output` in `function_call_output` must be a string. JSON-serialise dicts before passing. Malformed items are silently ignored by the model, causing the loop to stall.

## Configuration

In `core/config.py` (pydantic-settings, env-driven):

```python
openai_default_model: str = "gpt-5.5"      # provisional — re-verify at Phase 1
openai_fast_model: str = "gpt-5.4-nano"    # provisional — re-verify at Phase 1
openai_embedding_model: str = "text-embedding-3-small"
```

Model strings are configurable via environment variables — no code change needed to pin to a specific snapshot ID.

## Consequences

- Responses API `input[]` items, not `messages` — the adapter must translate on every call.
- Snapshot IDs must be re-verified before writing the adapter (see provisional note above).
- Switching to a second provider (Anthropic, Ollama) in v2 requires implementing a new `LLMProvider` subclass — no other files change.
