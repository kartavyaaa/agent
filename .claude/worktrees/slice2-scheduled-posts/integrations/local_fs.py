from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import (
    FileDecodeError,
    FileNotFoundInSandboxError,
    FileTooLargeError,
    PathIsDirectoryError,
    SandboxViolationError,
)


@dataclass
class FileReadResult:
    path: str  # path relative to sandbox root, for display
    content: str
    size_bytes: int


class LocalFsClient:
    """Sandboxed local file reader.

    All reads are confined to `root`. The containment check (is_relative_to)
    runs BEFORE any existence or stat queries, so out-of-sandbox paths reveal
    nothing about the host filesystem.
    """

    def __init__(self, root: Path, max_bytes: int = 1_048_576) -> None:
        self._root = root
        self._max_bytes = max_bytes

    def _read_sync(self, requested_path: str) -> FileReadResult:
        root = self._root.resolve()

        # Join as-is then resolve — no string sanitization.
        # resolve() follows all symlinks; is_relative_to() is the sole guard.
        candidate = (root / requested_path).resolve()

        if not candidate.is_relative_to(root):
            raise SandboxViolationError(f"Path '{requested_path}' resolves outside sandbox root")

        if not candidate.exists():
            raise FileNotFoundInSandboxError(f"File not found in sandbox: '{requested_path}'")

        if candidate.is_dir():
            raise PathIsDirectoryError(f"Path is a directory, not a file: '{requested_path}'")

        size = candidate.stat().st_size
        if size > self._max_bytes:
            raise FileTooLargeError(
                f"File '{requested_path}' is {size} bytes, limit is {self._max_bytes}"
            )

        raw = candidate.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileDecodeError(f"File '{requested_path}' is not valid UTF-8") from exc

        rel = candidate.relative_to(root)
        return FileReadResult(path=str(rel), content=content, size_bytes=size)

    async def read(self, requested_path: str) -> FileReadResult:
        return await asyncio.to_thread(self._read_sync, requested_path)

    async def health_check(self) -> bool:
        root = self._root.resolve()
        return root.exists() and root.is_dir()
