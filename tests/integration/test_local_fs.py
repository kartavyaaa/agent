"""Unit tests for LocalFsClient security and happy-path behaviour.

Uses pytest's tmp_path fixture — real files in a temp directory, no filesystem mocking.
All tests are marked as unit tests (no Docker required).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.exceptions import (
    FileDecodeError,
    FileNotFoundInSandboxError,
    FileTooLargeError,
    PathIsDirectoryError,
    SandboxViolationError,
)
from integrations.local_fs import LocalFsClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(root: Path, max_bytes: int = 1_048_576) -> LocalFsClient:
    return LocalFsClient(root=root, max_bytes=max_bytes)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_read_valid_file(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "hello.txt").write_text("Hello, world!", encoding="utf-8")

    client = _client(sandbox)
    result = await client.read("hello.txt")

    assert result.content == "Hello, world!"
    assert result.size_bytes == len(b"Hello, world!")
    assert result.path == "hello.txt"


async def test_read_file_in_subdirectory(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    (sandbox / "sub").mkdir(parents=True)
    (sandbox / "sub" / "note.txt").write_text("nested", encoding="utf-8")

    client = _client(sandbox)
    result = await client.read("sub/note.txt")

    assert result.content == "nested"


# ---------------------------------------------------------------------------
# Security: path traversal and containment
# ---------------------------------------------------------------------------


async def test_dotdot_traversal_rejected(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("sensitive", encoding="utf-8")

    client = _client(sandbox)
    with pytest.raises(SandboxViolationError):
        await client.read("../secret.txt")


async def test_absolute_path_outside_root_rejected(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    client = _client(sandbox)
    # /etc/passwd on Linux, C:\Windows\System32 on Windows — use a known temp path
    with pytest.raises(SandboxViolationError):
        await client.read(str(tmp_path / "outside.txt"))


async def test_multihop_traversal_rejected(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    (sandbox / "sub").mkdir(parents=True)

    client = _client(sandbox)
    with pytest.raises(SandboxViolationError):
        await client.read("sub/../../outside.txt")


async def test_symlink_escaping_root_rejected(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    target = tmp_path / "secret.txt"
    target.write_text("sensitive", encoding="utf-8")
    link = sandbox / "link.txt"
    try:
        os.symlink(str(target), str(link))
    except OSError as exc:
        pytest.skip(f"Symlink creation requires elevated privileges on this platform: {exc}")

    client = _client(sandbox)
    with pytest.raises(SandboxViolationError):
        await client.read("link.txt")


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


async def test_nonexistent_file_raises(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    client = _client(sandbox)
    with pytest.raises(FileNotFoundInSandboxError):
        await client.read("missing.txt")


async def test_directory_path_raises(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    (sandbox / "subdir").mkdir(parents=True)

    client = _client(sandbox)
    with pytest.raises(PathIsDirectoryError):
        await client.read("subdir")


async def test_oversized_file_raises(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    big = sandbox / "big.txt"
    big.write_bytes(b"x" * 101)

    client = _client(sandbox, max_bytes=100)
    with pytest.raises(FileTooLargeError):
        await client.read("big.txt")


async def test_non_utf8_file_raises(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "binary.bin").write_bytes(b"\xff\xfe\x00\x01")

    client = _client(sandbox)
    with pytest.raises(FileDecodeError):
        await client.read("binary.bin")


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


async def test_health_check_existing_root(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert await client.health_check() is True


async def test_health_check_missing_root(tmp_path: Path) -> None:
    client = _client(tmp_path / "nonexistent")
    assert await client.health_check() is False
