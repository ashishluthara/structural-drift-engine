"""
graph_builder.py — Dependency Graph Builder

Parses Python source files using the `ast` module to extract import statements,
then constructs a module-level dependency graph.
"""

import ast
from pathlib import Path

from scanner import file_path_to_module_name


def extract_imports(file_path: str, root_path: str) -> list[str]:
    """
    Parse a Python file and extract all imported module names.

    Handles both:
        import x
        from x import y

    Args:
        file_path: Relative file path to parse.
        root_path: Repository root (used to resolve absolute path).

    Returns:
        List of imported module name strings (dot-separated).
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
            if node.module:
                # Relative imports (level > 0) are skipped — no meaningful cross-boundary info
                if node.level == 0:
                    imports.append(node.module)

    return imports


def normalize_import(raw_import: str, known_modules: set[str]) -> str | None:
    """
    Normalize a raw import string against the set of known project modules.

    Tries progressively shorter prefixes to find the deepest known match.
    Returns None if the import is an external/stdlib dependency.

    Args:
        raw_import: Full dotted import string (e.g. "billing.service.helpers").
        known_modules: Set of module names that exist in the project.

    Returns:
        Matched module name or None.
    """
    parts = raw_import.split(".")

    # Try longest match first
    for length in range(len(parts), 0, -1):
        candidate = ".".join(parts[:length])
        if candidate in known_modules:
            return candidate

    return None


def build_dependency_graph(file_paths: list[str], root_path: str) -> dict[str, list[str]]:
    """
    Build a module-level dependency graph from a list of Python files.

    Graph format:
        {
            "billing.service": ["auth.utils", "users.models"],
            ...
        }

    Self-references and duplicate edges are removed.

    Args:
        file_paths: Relative .py file paths (from scanner).
        root_path: Repository root path.

    Returns:
        Dependency graph as a dict mapping module names to lists of dependencies.
    """
    # Map each file to its module name
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
