# Database Schema

**Engine:** PostgreSQL 16 with pgvector extension.
**ORM:** SQLAlchemy 2.x async (declarative).
**Migrations:** Alembic.

---

## Tables

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | `gen_random_uuid()` default |
| `telegram_id` | BIGINT (unique, nullable) | NULL for API-only users |
| `api_key_hash` | VARCHAR(64) (nullable) | SHA-256 hex of raw API key |
| `preferences` | JSONB | default `{}` — theme, timezone, default model, etc. |
| `created_at` | TIMESTAMPTZ | server default `now()` |
| `updated_at` | TIMESTAMPTZ | server default + `onupdate` |

---

### `projects`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK → users, CASCADE) | |
| `name` | VARCHAR(255) | |
| `description` | TEXT (nullable) | |
| `status` | ENUM(`active`,`archived`,`deleted`) | default `active` |
| `metadata` | JSONB | default `{}` |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

---

### `memories`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK → users, CASCADE) | |
| `content` | TEXT | raw text of the memory |
| `embedding` | VECTOR(1536) (nullable) | text-embedding-3-small; NULL until embed job runs |
| `importance_score` | FLOAT | 0.0–1.0, default 0.5; scored by LLM or heuristic |
| `memory_type` | ENUM(`working`,`episodic`,`semantic`,`knowledge`) | |
| `metadata` | JSONB | default `{}` — source, tags, related_ids, etc. |
| `created_at` | TIMESTAMPTZ | |
| `last_accessed_at` | TIMESTAMPTZ (nullable) | updated on retrieval |
| `expires_at` | TIMESTAMPTZ (nullable) | NULL = permanent; working memories get TTL |

**Indexes:**
- `USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)` — approximate nearest-neighbour search. HNSW chosen over ivfflat because it builds correctly on empty/growing tables without a training set.
- `btree (user_id, memory_type, created_at DESC)` — listing by layer.
- Partial `btree (expires_at) WHERE expires_at IS NOT NULL` — expiry sweep.

**Memory-type layer semantics:**

| Layer | Storage | Lifetime | Purpose |
|---|---|---|---|
| `working` | Redis (TTL) + mirrored row | Session TTL (30 min default) | Current conversation context |
| `episodic` | Postgres | Permanent | Significant events, past interactions |
| `semantic` | Postgres + pgvector | Permanent | Facts, beliefs, compressed knowledge |
| `knowledge` | Postgres | Permanent | Structured static knowledge, references |

---

### `tasks`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK → users, CASCADE) | |
| `project_id` | UUID (FK → projects, SET NULL, nullable) | |
| `title` | VARCHAR(500) | |
| `description` | TEXT (nullable) | |
| `status` | ENUM(`pending`,`in_progress`,`completed`,`cancelled`,`failed`) | |
| `priority` | SMALLINT | 1=low, 2=medium, 3=high, 4=critical |
| `due_at` | TIMESTAMPTZ (nullable) | |
| `plugin_name` | VARCHAR(100) (nullable) | plugin that owns this task |
| `plugin_payload` | JSONB (nullable) | plugin-specific execution data |
| `created_at` | TIMESTAMPTZ | |
| `updated_at` | TIMESTAMPTZ | |

**Indexes:**
- `btree (user_id, status, priority)` — list by status/priority.
- Partial `btree (due_at) WHERE due_at IS NOT NULL AND status = 'pending'` — upcoming tasks.

---

### `reminders`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `user_id` | UUID (FK → users, CASCADE) | |
| `task_id` | UUID (FK → tasks, SET NULL, nullable) | |
| `message` | TEXT | |
| `remind_at` | TIMESTAMPTZ | when to fire |
| `recurrence` | JSONB (nullable) | e.g. `{"freq": "daily", "interval": 1}` |
| `sent_at` | TIMESTAMPTZ (nullable) | NULL = not yet sent |
| `created_at` | TIMESTAMPTZ | |

**Indexes:**
- Partial `btree (remind_at, sent_at) WHERE sent_at IS NULL` — hot path for the arq cron poller.

---

### `plugin_registry`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID (PK) | |
| `plugin_name` | VARCHAR(100) (unique) | machine-readable slug |
| `version` | VARCHAR(20) | semver |
| `enabled` | BOOLEAN | default `true` |
| `config` | JSONB | per-plugin runtime config overrides; default `{}` |
| `health_status` | ENUM(`healthy`,`degraded`,`unhealthy`,`unknown`) | default `unknown` |
| `last_health_check_at` | TIMESTAMPTZ (nullable) | |

---

## Alembic Migration Strategy

1. **Migration 1:** `CREATE EXTENSION IF NOT EXISTS vector` then all tables and btree indexes.
2. **Migration 2:** HNSW index on `memories.embedding` — safe on empty table; created separately to isolate failure mode.
3. **ENUM types** are Postgres-native (not VARCHAR+CHECK); managed by `sqlalchemy.Enum`.
