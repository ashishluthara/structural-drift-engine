"""
graph_builder.py — Dependency Graph Builder

Parses Python source files using `ast` to extract import statements,
then constructs a module-level dependency graph.

Key improvement over v1: is_internal_module() filters out stdlib and
third-party packages before graph construction, eliminating false positives
in cycle detection and coupling metrics.
"""

import ast
import sys
from pathlib import Path

from scanner import file_path_to_module_name

# Built-in module names that should never be treated as internal
_STDLIB_TOP_LEVEL: set[str] = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else {
    "os", "sys", "re", "io", "abc", "ast", "csv", "json", "math", "time",
    "enum", "copy", "uuid", "typing", "logging", "pathlib", "hashlib",
    "functools", "itertools", "threading", "collections", "contextlib",
    "dataclasses", "datetime", "subprocess", "traceback", "warnings",
    "urllib", "http", "email", "html", "xml", "socket", "ssl", "struct",
    "string", "textwrap", "unittest", "importlib", "inspect", "types",
    "weakref", "gc", "platform", "signal", "queue", "heapq", "bisect",
    "decimal", "fractions", "random", "statistics", "array", "pickle",
    "shelve", "sqlite3", "gzip", "zipfile", "tarfile", "tempfile",
    "shutil", "glob", "fnmatch", "pprint", "reprlib", "operator",
}


def is_internal_module(module_name: str, known_modules: set[str]) -> bool:
    """
    Return True if a module name belongs to the current project.

    Checks:
        1. Exact match against known project modules
        2. Top-level package match (e.g. "billing.service" -> "billing" in known)
        3. Excludes stdlib top-level names
        4. Excludes common third-party prefixes

    Args:
        module_name: Dotted module name string to evaluate.
        known_modules: Set of all project module names.

    Returns:
        True if the module is part of the project, False otherwise.
    """
    if not module_name:
        return False

    top_level = module_name.split(".")[0]

    # Explicit stdlib exclusion
    if top_level in _STDLIB_TOP_LEVEL:
        return False

    # If exact match or prefix match found in known modules, it's internal
    if module_name in known_modules:
        return True

    # Check if top-level package is a known project package
    for known in known_modules:
        if known.split(".")[0] == top_level:
            return True

    return False


def extract_imports(file_path: str, root_path: str) -> list[str]:
    """
    Parse a Python file and extract all absolute imported module names.

    Skips relative imports (level > 0) — they are resolved at runtime
    and cannot cause cross-boundary violations.

    Args:
        file_path: Relative file path to parse.
        root_path: Repository root path.

    Returns:
        List of absolute imported module name strings.
    """
    abs_path = Path(root_path) / file_path

    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"  [WARN] Could not read {file_path}: {exc}")
        return []

    try:
        tree = ast.parse(source, filename=str(abs_path))
    except SyntaxError as exc:
        print(f"  [WARN] Syntax error in {file_path}: {exc}")
        return []

    imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)

        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                imports.append(node.module)

    return imports


def normalize_import(raw_import: str, known_modules: set[str]) -> str | None:
    """
    Normalize a raw import string against known project modules.

    Tries progressively shorter prefixes to find the deepest known match.
    Only returns a match if the module is confirmed internal.

    Args:
        raw_import: Full dotted import string.
        known_modules: Set of known project module names.

    Returns:
        Matched project module name, or None.
    """
    if not is_internal_module(raw_import, known_modules):
        return None

    parts = raw_import.split(".")

    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        if candidate in known_modules:
            return candidate

    return None


def build_dependency_graph(file_paths: list[str], root_path: str) -> dict[str, list[str]]:
    """
    Build a module-level dependency graph from a list of Python files.

    Only internal project modules appear as edges — stdlib and third-party
    imports are filtered out by is_internal_module().

    Args:
        file_paths: Relative .py file paths from scanner.
        root_path: Repository root path.

    Returns:
        Graph as {module_name: [dependency_module_name, ...]}
    """
    module_map: dict[str, str] = {
        fp: file_path_to_module_name(fp) for fp in file_paths
    }
    known_modules: set[str] = set(module_map.values())

    graph: dict[str, list[str]] = {mod: [] for mod in known_modules}

    for file_path, module_name in module_map.items():
        raw_imports = extract_imports(file_path, root_path)
        deps: set[str] = set()

        for raw in raw_imports:
            matched = normalize_import(raw, known_modules)
            if matched and matched != module_name:
                deps.add(matched)

        graph[module_name] = sorted(deps)

    return graph
