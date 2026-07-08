from __future__ import annotations

import subprocess

import pytest


def _docker_available() -> bool:
    try:
        result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "integration: requires Docker + testcontainers")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if not _docker_available():
        skip = pytest.mark.skip(reason="Docker not available on this host")
        for item in items:
            if item.get_closest_marker("integration"):
                item.add_marker(skip)
