"""
scanner.py — Repository Scanner

Recursively scans a Python repository and returns all relevant .py file paths.
Ignores virtual environments, git internals, and cache directories.
"""

import os
from pathlib import Path

IGNORED_DIRS = {"venv", ".venv", ".git", "__pycache__", ".mypy_cache", ".tox", "dist", "build", "egg-info"}


def scan_repository(root_path: str) -> list[str]:
    """
    Recursively scan a directory for Python source files.

    Args:
        root_path: Absolute or relative path to the repository root.

    Returns:
        Sorted list of .py file paths relative to root_path.

    Raises:
        ValueError: If root_path does not exist or is not a directory.
    """
    root = Path(root_path).resolve()

    if not root.exists():
        raise ValueError(f"Path does not exist: {root_path}")
    if not root.is_dir():
        raise ValueError(f"Path is not a directory: {root_path}")

    py_files = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in-place to prevent descending into them
        dirnames[:] = [
            d for d in dirnames
            if d not in IGNORED_DIRS and not d.endswith(".egg-info")
        ]

        for filename in filenames:
            if filename.endswith(".py"):
                full_path = Path(dirpath) / filename
                relative_path = full_path.relative_to(root)
                py_files.append(str(relative_path))

    return sorted(py_files)


def file_path_to_module_name(file_path: str) -> str:
    """
    Convert a relative file path to a Python module name.

    Example:
        billing/service.py  →  billing.service
        main.py             →  main

    Args:
        file_path: Relative file path string (e.g. "billing/service.py").

    Returns:
        Dot-separated module name string.
    """
    path = Path(file_path)
    parts = list(path.parts)

    # Strip __init__ suffix — treat it as its package name
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")

    return ".".join(parts)
