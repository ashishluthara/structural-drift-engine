"""
scanner.py — Repository Scanner

Recursively scans a Python repository and returns all relevant .py file paths.
Respects DriftConfig for test directory exclusion.
"""

import os
from pathlib import Path

IGNORED_DIRS = {
    "venv", ".venv", ".git", "__pycache__", ".mypy_cache",
    ".tox", "dist", "build", ".eggs", "node_modules",
}

TEST_DIR_PATTERNS = {
    "tests", "test", "testing", "spec", "specs", "_test", "test_",
}


def _is_test_dir(dirname: str) -> bool:
    """Return True if a directory name looks like a test directory."""
    lower = dirname.lower()
    return lower in TEST_DIR_PATTERNS or lower.startswith("test_")


def scan_repository(
    root_path: str,
    ignore_test_dirs: bool = True,
) -> list[str]:
    """
    Recursively scan a directory for Python source files.

    Args:
        root_path: Absolute or relative path to the repository root.
        ignore_test_dirs: If True, skip directories that look like test dirs.

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
        pruned = []
        for d in dirnames:
            if d in IGNORED_DIRS or d.endswith(".egg-info"):
                continue
            if ignore_test_dirs and _is_test_dir(d):
                continue
            pruned.append(d)
        dirnames[:] = pruned

        for filename in filenames:
            if filename.endswith(".py"):
                # Skip test files at the top level too
                if ignore_test_dirs and (
                    filename.startswith("test_") or filename.endswith("_test.py")
                ):
                    continue
                full_path = Path(dirpath) / filename
                relative_path = full_path.relative_to(root)
                py_files.append(str(relative_path))

    return sorted(py_files)


def file_path_to_module_name(file_path: str) -> str:
    """
    Convert a relative file path to a Python module name.

    Example:
        billing/service.py  ->  billing.service
        main.py             ->  main
    """
    path = Path(file_path)
    parts = list(path.parts)

    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")

    return ".".join(parts)
