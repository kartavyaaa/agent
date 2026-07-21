"""Unit tests for task management plugins (create_task, list_tasks, complete_task).

DB is mocked — no Postgres needed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.tasks.complete import CompleteTaskPlugin
from plugins.tasks.create import CreateTaskPlugin
from plugins.tasks.list import ListTasksPlugin
from plugins.tasks.schemas import (
    CompleteTaskInput,
    CompleteTaskOutput,
    CreateTaskInput,
    CreateTaskOutput,
    ListTasksInput,
    ListTasksOutput,
)


def _make_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _make_db_with_query(rows: list[MagicMock]) -> MagicMock:
    """Mock db for list/complete queries: db.execute returns rows via scalars().all()."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=execute_result)
    return db


def _make_db_with_one(row: object | None) -> MagicMock:
    """Mock db for complete_task: db.execute returns a single row via scalar_one_or_none()."""
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=execute_result)
    return db


# ---------------------------------------------------------------------------
# create_task — schema
# ---------------------------------------------------------------------------


def test_create_task_input_has_no_user_id() -> None:
    assert "user_id" not in CreateTaskInput.model_fields


def test_create_task_output_has_expected_fields() -> None:
    fields = set(CreateTaskOutput.model_fields.keys())
    assert {"task_id", "title", "status", "confirmation"} == fields


# ---------------------------------------------------------------------------
# create_task — execute()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_builds_task_row() -> None:
    plugin = CreateTaskPlugin(tz_name="UTC")
    db = _make_db()
    uid = uuid.uuid4()

    await plugin.execute(
        CreateTaskInput(title="call dentist", description=None, due_at=None, priority=None),
        user_id=uid,
        db=db,
    )

    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert added.user_id == uid
    assert added.title == "call dentist"


@pytest.mark.asyncio
async def test_create_task_flushes() -> None:
    plugin = CreateTaskPlugin(tz_name="UTC")
    db = _make_db()

    await plugin.execute(
        CreateTaskInput(title="test", description=None, due_at=None, priority=None),
        user_id=uuid.uuid4(),
        db=db,
    )

    db.flush.assert_called_once()


@pytest.mark.asyncio
async def test_create_task_returns_output() -> None:
    plugin = CreateTaskPlugin(tz_name="UTC")
    db = _make_db()

    result = await plugin.execute(
        CreateTaskInput(title="buy milk", description=None, due_at=None, priority=None),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert isinstance(result, CreateTaskOutput)
    assert result.status == "pending"
    assert result.title == "buy milk"
    assert "buy milk" in result.confirmation


@pytest.mark.asyncio
async def test_create_task_naive_due_at_gets_utc() -> None:
    plugin = CreateTaskPlugin(tz_name="UTC")
    db = _make_db()
    naive_dt = datetime(2026, 7, 10, 9, 0)  # no tzinfo

    await plugin.execute(
        CreateTaskInput(title="test", description=None, due_at=naive_dt, priority=None),
        user_id=uuid.uuid4(),
        db=db,
    )

    added = db.add.call_args[0][0]
    assert added.due_at is not None
    assert added.due_at.tzinfo is not None


@pytest.mark.asyncio
async def test_create_task_none_priority_defaults_to_1() -> None:
    plugin = CreateTaskPlugin(tz_name="UTC")
    db = _make_db()

    await plugin.execute(
        CreateTaskInput(title="test", description=None, due_at=None, priority=None),
        user_id=uuid.uuid4(),
        db=db,
    )

    added = db.add.call_args[0][0]
    assert added.priority == 1


@pytest.mark.asyncio
async def test_create_task_explicit_priority_used() -> None:
    plugin = CreateTaskPlugin(tz_name="UTC")
    db = _make_db()

    await plugin.execute(
        CreateTaskInput(title="urgent", description=None, due_at=None, priority=5),
        user_id=uuid.uuid4(),
        db=db,
    )

    added = db.add.call_args[0][0]
    assert added.priority == 5


# ---------------------------------------------------------------------------
# list_tasks — schema
# ---------------------------------------------------------------------------


def test_list_tasks_input_has_no_user_id() -> None:
    assert "user_id" not in ListTasksInput.model_fields


# ---------------------------------------------------------------------------
# list_tasks — execute()
# ---------------------------------------------------------------------------


def _make_mock_task(
    title: str = "test task",
    status: str = "pending",
    priority: int = 1,
    due_at: datetime | None = None,
) -> MagicMock:
    t = MagicMock()
    t.id = uuid.uuid4()
    t.title = title
    t.status = status
    t.priority = priority
    t.due_at = due_at
    return t


@pytest.mark.asyncio
async def test_list_tasks_returns_task_summaries_with_ids() -> None:
    plugin = ListTasksPlugin()
    mock_task = _make_mock_task(title="dentist", status="pending")
    db = _make_db_with_query([mock_task])

    result = await plugin.execute(
        ListTasksInput(status_filter=None),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert isinstance(result, ListTasksOutput)
    assert result.count == 1
    assert result.tasks[0].task_id == str(mock_task.id)
    assert result.tasks[0].title == "dentist"


@pytest.mark.asyncio
async def test_list_tasks_count_matches() -> None:
    plugin = ListTasksPlugin()
    tasks = [_make_mock_task(title=f"task {i}") for i in range(3)]
    db = _make_db_with_query(tasks)

    result = await plugin.execute(
        ListTasksInput(status_filter=None),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.count == len(result.tasks) == 3


def test_list_tasks_default_filter_open_statuses_constant() -> None:
    """_OPEN_STATUSES must contain exactly pending and in_progress.

    The authoritative proof that the filter is applied is the live bot test;
    this guards against accidentally changing the constant itself.
    """
    from plugins.tasks.list import _OPEN_STATUSES

    assert set(_OPEN_STATUSES) == {"pending", "in_progress"}


@pytest.mark.asyncio
async def test_list_tasks_explicit_status_filter_passed_through() -> None:
    """Explicit status_filter is forwarded; return value reflects the mock's rows."""
    plugin = ListTasksPlugin()
    mock_task = _make_mock_task(title="done thing", status="completed")
    db = _make_db_with_query([mock_task])

    result = await plugin.execute(
        ListTasksInput(status_filter="completed"),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.tasks[0].status == "completed"


@pytest.mark.asyncio
async def test_list_tasks_empty_result() -> None:
    plugin = ListTasksPlugin()
    db = _make_db_with_query([])

    result = await plugin.execute(
        ListTasksInput(status_filter=None),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.count == 0
    assert result.tasks == []


# ---------------------------------------------------------------------------
# complete_task — schema
# ---------------------------------------------------------------------------


def test_complete_task_input_has_no_user_id() -> None:
    assert "user_id" not in CompleteTaskInput.model_fields


# ---------------------------------------------------------------------------
# complete_task — execute()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_task_found_sets_completed() -> None:
    plugin = CompleteTaskPlugin()
    tid = uuid.uuid4()
    mock_task = MagicMock()
    mock_task.id = tid
    mock_task.title = "call dentist"
    mock_task.status = "pending"
    db = _make_db_with_one(mock_task)

    result = await plugin.execute(
        CompleteTaskInput(task_id=str(tid)),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert mock_task.status == "completed"
    db.flush.assert_called_once()
    assert isinstance(result, CompleteTaskOutput)
    assert result.status == "completed"
    assert result.title == "call dentist"


@pytest.mark.asyncio
async def test_complete_task_not_found_returns_not_found() -> None:
    plugin = CompleteTaskPlugin()
    db = _make_db_with_one(None)

    result = await plugin.execute(
        CompleteTaskInput(task_id=str(uuid.uuid4())),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.status == "not_found"
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_complete_task_invalid_uuid_returns_not_found() -> None:
    plugin = CompleteTaskPlugin()
    db = _make_db_with_one(None)

    result = await plugin.execute(
        CompleteTaskInput(task_id="not-a-valid-uuid"),
        user_id=uuid.uuid4(),
        db=db,
    )

    assert result.status == "not_found"
    # DB should not even be queried for an unparseable id
    db.execute.assert_not_called()
    db.flush.assert_not_called()


@pytest.mark.asyncio
async def test_complete_task_cross_user_returns_not_found() -> None:
    """A task belonging to another user must not be completable.

    The DB query scopes by BOTH task id AND the injected user_id.
    Simulate this by returning None from the DB (as would happen when the WHERE
    id=X AND user_id=injected finds nothing), and verify not_found is returned.
    """
    plugin = CompleteTaskPlugin()
    db = _make_db_with_one(None)  # DB returns nothing because user_id doesn't match

    result = await plugin.execute(
        CompleteTaskInput(task_id=str(uuid.uuid4())),
        user_id=uuid.uuid4(),  # different user than the task owner
        db=db,
    )

    assert result.status == "not_found"
    db.flush.assert_not_called()


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_task_health_check() -> None:
    assert (await CreateTaskPlugin(tz_name="UTC").health_check()).status == "healthy"


@pytest.mark.asyncio
async def test_list_tasks_health_check() -> None:
    assert (await ListTasksPlugin().health_check()).status == "healthy"


@pytest.mark.asyncio
async def test_complete_task_health_check() -> None:
    assert (await CompleteTaskPlugin().health_check()).status == "healthy"
