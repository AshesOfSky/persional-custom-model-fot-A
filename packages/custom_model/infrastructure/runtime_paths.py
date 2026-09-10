"""Deterministic paths for local runtime state.

Runtime databases are shared by processes that may start from different
working directories.  Resolving a relative path against ``Path.cwd()`` would
silently create separate state stores, so every relative value is anchored to
the Custom Model repository root instead.

This module deliberately performs no filesystem writes.  Directory creation
remains the responsibility of the concrete persistence adapter.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path, PureWindowsPath


CUSTOM_MODEL_ROOT = Path(__file__).resolve().parents[3]
MONITOR_WORKER_DB_ENV = "MONITOR_WORKER_DB"
DEFAULT_MONITOR_WORKER_DB = Path("cache/monitor_worker.sqlite3")


class RuntimeStatePathError(ValueError):
    """A runtime-state path is absent or cannot be represented safely."""


def resolve_runtime_state_path(
    value: str | os.PathLike[str],
    *,
    project_root: str | os.PathLike[str] = CUSTOM_MODEL_ROOT,
) -> Path:
    """Return an absolute runtime-state path independent of ``Path.cwd()``.

    Absolute values retain their meaning. Relative values are anchored to the
    supplied project root, which defaults to the checked-out Custom Model root.
    Whitespace-only and NUL-containing values are rejected before any
    persistence adapter can create files.
    """

    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            raise RuntimeStatePathError("runtime-state path cannot be blank")
        if "\x00" in normalized:
            raise RuntimeStatePathError(
                "runtime-state path contains an invalid character"
            )
        candidate = Path(normalized)
    elif isinstance(value, os.PathLike):
        try:
            raw_value = os.fspath(value)
        except TypeError as exc:
            raise RuntimeStatePathError("runtime-state path is invalid") from exc
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise RuntimeStatePathError("runtime-state path cannot be blank")
        if "\x00" in raw_value:
            raise RuntimeStatePathError("runtime-state path contains an invalid character")
        candidate = Path(raw_value)
    else:
        raise RuntimeStatePathError("runtime-state path must be text or path-like")

    root = Path(project_root).resolve()
    was_relative = not candidate.is_absolute()
    windows_candidate = PureWindowsPath(os.fspath(candidate))
    if was_relative and (
        candidate.drive
        or candidate.root
        or windows_candidate.drive
        or windows_candidate.root
    ):
        raise RuntimeStatePathError(
            "runtime-state path must be absolute or project-root relative"
        )
    if was_relative:
        candidate = root / candidate
    resolved = candidate.resolve()
    if was_relative and not resolved.is_relative_to(root):
        raise RuntimeStatePathError(
            "relative runtime-state path cannot escape the project root"
        )
    return resolved


def resolve_monitor_worker_db_path(
    *,
    env: Mapping[str, str] | None = None,
    project_root: str | os.PathLike[str] = CUSTOM_MODEL_ROOT,
) -> Path:
    """Resolve the configured Worker database using one cross-process rule."""

    environment = os.environ if env is None else env
    configured = environment.get(MONITOR_WORKER_DB_ENV)
    value: str | os.PathLike[str] = (
        DEFAULT_MONITOR_WORKER_DB if configured is None else configured
    )
    return resolve_runtime_state_path(value, project_root=project_root)


def resolve_monitor_snapshot_db_path(
    *,
    env: Mapping[str, str] | None = None,
    project_root: str | os.PathLike[str] = CUSTOM_MODEL_ROOT,
) -> Path:
    """Resolve snapshot coordination state to the same authoritative Worker DB."""

    return resolve_monitor_worker_db_path(env=env, project_root=project_root)


__all__ = [
    "CUSTOM_MODEL_ROOT",
    "DEFAULT_MONITOR_WORKER_DB",
    "MONITOR_WORKER_DB_ENV",
    "RuntimeStatePathError",
    "resolve_monitor_worker_db_path",
    "resolve_monitor_snapshot_db_path",
    "resolve_runtime_state_path",
]
