"""Model registry.

Importing this package registers every ORM model on Base.metadata, so foreign
keys that span tables (e.g. reminders.task_id -> tasks.id) always resolve
regardless of which model a caller imported first.
"""

from __future__ import annotations

from models.base import Base
from models.memory import Memory
from models.plugin_registry import PluginRegistry
from models.project import Project
from models.reminder import Reminder
from models.task import Task
from models.user import User

__all__ = [
    "Base",
    "Memory",
    "PluginRegistry",
    "Project",
    "Reminder",
    "Task",
    "User",
]
