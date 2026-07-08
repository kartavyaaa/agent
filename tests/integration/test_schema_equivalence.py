"""Schema equivalence test: models vs alembic migrations.
 
Proves that Base.metadata (models) and `alembic upgrade head` (migrations)
produce an identical Postgres schema.  Requires Docker (pgvector/pgvector:pg16).
Skipped automatically when Docker is unavailable (conftest.py).
 
Two fully isolated containers are used so enum type-name collisions and
search_path confusion cannot occur:
  - Container 1 (alembic): migrations applied via subprocess, same as test_full_flow.py
  - Container 2 (models):  Base.metadata.create_all via async conn.run_sync
  - Container 3 (drift):   throwaway MetaData with one extra column, proves guard fires
 
All fixtures and tests in this module share ONE event loop (loop_scope="module").
asyncpg connections are bound to the loop that created them, so module-scoped async
engines MUST run on a module-scoped loop — otherwise pytest-asyncio's default
function-scoped loop causes "attached to a different loop" / "Event loop is closed".
"""
 
from __future__ import annotations
 
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any
 
import pytest
import pytest_asyncio
from sqlalchemy import Column, MetaData, String, Table, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
 
import models  # noqa: F401  registers all ORM models on Base.metadata
from models.base import Base
 
# Every async fixture and test in this file runs on the SAME module-scoped loop.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="module"),
]
 
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
 
# Enum DDL exactly as in migration 0001 — create_type=False means create_all
# won't emit these, so we must emit them manually before create_all.
_ENUM_DDL = [
    "CREATE TYPE memory_type AS ENUM ('working','episodic','semantic','knowledge')",
    "CREATE TYPE task_status AS ENUM ('pending','in_progress','completed','cancelled','failed')",
    "CREATE TYPE project_status AS ENUM ('active','archived','deleted')",
    "CREATE TYPE plugin_health_status AS ENUM ('healthy','degraded','unhealthy','unknown')",
]
 
# Legitimately absent from model side: HNSW index needs pgvector DDL,
# intentionally left out of __table_args__ (see models/memory.py).
_HNSW_INDEX = "ix_memories_embedding_hnsw"
 
# alembic_version is a migration-tracking table, not an ORM model.
_EXCLUDE_TABLES = {"alembic_version"}
 
 
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
 
 
def _base_env(url: str) -> dict[str, str]:
    env = dict(os.environ)
    env["DATABASE_URL"] = url
    env.setdefault("OPENAI_API_KEY", "sk-test")
    env.setdefault("TELEGRAM_BOT_TOKEN", "0:test")
    env.setdefault("TELEGRAM_WEBHOOK_SECRET", "test")
    env.setdefault("REDIS_URL", "redis://localhost:6379/0")
    return env
 
 
def _run_alembic(url: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic.config", "-c", str(_ALEMBIC_INI), "upgrade", "head"],
        cwd=str(_REPO_ROOT),
        env=_base_env(url),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade failed (exit {result.returncode}):\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
 
 
def _asyncpg_url(pg: Any) -> str:
    url: str = pg.get_connection_url().replace("psycopg2", "asyncpg", 1)
    return url
 
 
def _normalise_default(raw: str | None) -> str | None:
    """Normalise a Postgres column_default string for comparison.
 
    Strips type casts ('pending'::task_status -> pending), outer quotes,
    and normalises now() / CURRENT_TIMESTAMP to the canonical form 'now()'.
    """
    if raw is None:
        return None
    s = raw.strip()
    # Strip ::typename casts (e.g. 'pending'::task_status, 'true'::boolean)
    s = re.sub(r"::[\w_ ]+", "", s)
    # Normalise timestamp defaults BEFORE stripping quotes
    s = re.sub(r"CURRENT_TIMESTAMP", "now()", s, flags=re.IGNORECASE)
    # Strip outer single quotes from string literals
    if s.startswith("'") and s.endswith("'"):
        s = s[1:-1]
    return s.strip()
 
 
def _normalise_indexdef(raw: str) -> str:
    """Collapse runs of whitespace in an index definition."""
    return re.sub(r"\s+", " ", raw).strip()
 
 
async def _get_columns(
    engine: AsyncEngine,
) -> dict[str, dict[str, dict[str, str | None]]]:
    """Return {table: {col: {udt_name, is_nullable, column_default}}} for user tables."""
    query = text("""
        SELECT table_name, column_name, udt_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name NOT IN ('alembic_version')
        ORDER BY table_name, ordinal_position
    """)
    result: dict[str, dict[str, dict[str, str | None]]] = {}
    async with engine.connect() as conn:
        rows = await conn.execute(query)
        for table, col, udt, nullable, default in rows:
            result.setdefault(table, {})[col] = {
                "udt_name": udt,
                "is_nullable": nullable,
                "column_default": _normalise_default(default),
            }
    return result
 
 
async def _get_indexes(engine: AsyncEngine) -> dict[str, str]:
    """Return {indexname: normalised_indexdef} for user tables, excluding alembic_version."""
    query = text("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename NOT IN ('alembic_version')
        ORDER BY indexname
    """)
    result: dict[str, str] = {}
    async with engine.connect() as conn:
        rows = await conn.execute(query)
        for name, defn in rows:
            result[name] = _normalise_indexdef(defn)
    return result
 
 
async def _get_enums(engine: AsyncEngine) -> dict[str, list[str]]:
    """Return {type_name: [label, ...]} in sort order."""
    query = text("""
        SELECT t.typname, e.enumlabel
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        ORDER BY t.typname, e.enumsortorder
    """)
    result: dict[str, list[str]] = {}
    async with engine.connect() as conn:
        rows = await conn.execute(query)
        for typname, label in rows:
            result.setdefault(typname, []).append(label)
    return result
 
 
async def _setup_models_db(engine: AsyncEngine, metadata: MetaData | None = None) -> None:
    """Bootstrap a fresh DB for create_all: extension, enum types, then create_all."""
    target = metadata or Base.metadata
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        for ddl in _ENUM_DDL:
            await conn.execute(text(ddl))
    async with engine.begin() as conn:
        await conn.run_sync(target.create_all)
 
 
def _diff(
    label_a: str,
    label_b: str,
    a: dict[str, Any],
    b: dict[str, Any],
) -> list[str]:
    """Return human-readable diff lines between two flat-ish dicts."""
    lines: list[str] = []
    for key in sorted(set(a) | set(b)):
        if key not in b:
            lines.append(f"  {label_b} missing key: {key!r}")
        elif key not in a:
            lines.append(f"  {label_a} missing key: {key!r}")
        elif a[key] != b[key]:
            lines.append(f"  {key!r}: {label_a}={a[key]!r}  {label_b}={b[key]!r}")
    return lines
 
 
# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
 
 
@pytest.fixture(scope="module")
def alembic_url() -> Generator[str, None, None]:
    from testcontainers.postgres import PostgresContainer
 
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        url = _asyncpg_url(pg)
        _run_alembic(url)
        yield url
 
 
@pytest.fixture(scope="module")
def models_url() -> Generator[str, None, None]:
    from testcontainers.postgres import PostgresContainer
 
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield _asyncpg_url(pg)
 
 
@pytest.fixture(scope="module")
def drift_url() -> Generator[str, None, None]:
    from testcontainers.postgres import PostgresContainer
 
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield _asyncpg_url(pg)
 
 
@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def alembic_engine(alembic_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(alembic_url, echo=False)
    yield engine
    await engine.dispose()
 
 
@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def models_engine(models_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(models_url, echo=False)
    await _setup_models_db(engine)
    yield engine
    await engine.dispose()
 
 
# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
 
 
@pytest.mark.asyncio(loop_scope="module")
async def test_schema_equivalence(
    alembic_engine: AsyncEngine,
    models_engine: AsyncEngine,
) -> None:
    """Alembic migrations and Base.metadata.create_all produce identical schemas."""
    alembic_cols = await _get_columns(alembic_engine)
    models_cols = await _get_columns(models_engine)
 
    alembic_idxs = await _get_indexes(alembic_engine)
    models_idxs = await _get_indexes(models_engine)
 
    alembic_enums = await _get_enums(alembic_engine)
    models_enums = await _get_enums(models_engine)
 
    failures: list[str] = []
 
    # --- Tables present in both ---
    alembic_tables = set(alembic_cols) - _EXCLUDE_TABLES
    models_tables = set(models_cols)
    if alembic_tables != models_tables:
        missing_from_models = alembic_tables - models_tables
        missing_from_alembic = models_tables - alembic_tables
        if missing_from_models:
            failures.append(f"Tables in alembic but not models: {sorted(missing_from_models)}")
        if missing_from_alembic:
            failures.append(f"Tables in models but not alembic: {sorted(missing_from_alembic)}")
 
    # --- Columns (for tables present in both) ---
    for table in sorted(alembic_tables & models_tables):
        a_cols = alembic_cols[table]
        m_cols = models_cols[table]
        col_diff = _diff("alembic", "models", a_cols, m_cols)
        if col_diff:
            failures.append(f"Column mismatch in table {table!r}:")
            for line in col_diff:
                # Each column value is a sub-dict; recurse one level for clarity.
                failures.append(line)
 
    # --- Indexes: remove HNSW from alembic side before diffing ---
    alembic_idxs_check = {k: v for k, v in alembic_idxs.items() if k != _HNSW_INDEX}
    idx_diff = _diff("alembic", "models", alembic_idxs_check, models_idxs)
    if idx_diff:
        failures.append("Index mismatch:")
        failures.extend(idx_diff)
 
    # HNSW must exist on the alembic side (migration 0002 ran).
    if _HNSW_INDEX not in alembic_idxs:
        failures.append(f"HNSW index {_HNSW_INDEX!r} missing from alembic schema (0002 not run?)")
 
    # --- ENUMs ---
    enum_diff = _diff("alembic", "models", alembic_enums, models_enums)
    if enum_diff:
        failures.append("ENUM mismatch:")
        failures.extend(enum_diff)
 
    if failures:
        pytest.fail("Schema drift detected:\n" + "\n".join(failures))
 
 
@pytest.mark.asyncio(loop_scope="module")
async def test_drift_is_detected(
    alembic_engine: AsyncEngine,
    drift_url: str,
) -> None:
    """Comparator fires when models and migrations diverge (guard is live)."""
    # Build a throwaway MetaData that mirrors the real schema but adds an extra column.
    # We do NOT touch Base.metadata or any live mapper state — to_metadata() clones each
    # table onto the throwaway MetaData (the supported replacement for Column.copy()).
    throwaway = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(throwaway)
 
    # Add a spurious column to the 'users' table in the throwaway metadata.
    users_throwaway = throwaway.tables["users"]
    users_throwaway.append_column(Column("fake_drift_col", String(10)))
 
    drift_engine = create_async_engine(drift_url, echo=False)
    try:
        await _setup_models_db(drift_engine, metadata=throwaway)
 
        alembic_cols = await _get_columns(alembic_engine)
        drift_cols = await _get_columns(drift_engine)
 
        # The diff must be non-empty (fake_drift_col is present in drift, absent in alembic).
        diff_lines = _diff(
            "alembic", "drift", alembic_cols.get("users", {}), drift_cols.get("users", {})
        )
        assert diff_lines, (
            "Expected comparator to detect a drift (extra column 'fake_drift_col') "
            "but diff was empty — the guard is broken."
        )
    finally:
        await drift_engine.dispose()