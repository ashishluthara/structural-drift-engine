"""
complexity.py — Cyclomatic Complexity Estimator

Uses Python's `ast` module to estimate cyclomatic complexity per module.
No external dependencies.

Complexity is counted by summing decision points:
    - if / elif
    - for / while
    - try / except / finally
    - with
    - boolean operators (and / or)
    - function and async function definitions
    - lambda expressions
    - comprehensions (list, dict, set, generator)
    - assert statements
    - match / case (Python 3.10+)

Each function definition contributes a base complexity of 1.
A module with no branches has complexity 1 (the module itself).
"""

import ast
from pathlib import Path


# AST node types that each contribute +1 to complexity
BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.While,
    ast.Try,
    ast.ExceptHandler,
    ast.With,
    ast.Assert,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _count_boolean_ops(tree: ast.AST) -> int:
    """Count boolean operator sub-expressions (and/or) in the AST."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp):
            # Each additional operand beyond the first adds a branch
            count += len(node.values) - 1
    return count


def compute_file_complexity(file_path: str, root_path: str) -> int | None:
    """
    Compute the cyclomatic complexity score for a single Python file.

    Args:
        file_path: Relative path to the file (from repo root).
        root_path: Repository root path.

    Returns:
        Integer complexity score, or None if the file cannot be parsed.
    """
    abs_path = Path(root_path) / file_path

    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    try:
        tree = ast.parse(source, filename=str(abs_path))
    except SyntaxError:
        return None

    score = 1  # base complexity for the module itself

    for node in ast.walk(tree):
        if isinstance(node, BRANCH_NODES):
            score += 1

    score += _count_boolean_ops(tree)

    # Python 3.10+ match statement support
    for node in ast.walk(tree):
        node_type = type(node).__name__
        if node_type == "Match":
            score += 1
        elif node_type == "match_case":
            score += 1

    return score


def compute_complexity_map(
    file_paths: list[str],
    root_path: str,
    module_map: dict[str, str],
) -> dict[str, int]:
    """
    Compute complexity scores for all modules in the repository.

    Args:
        file_paths: List of relative .py file paths.
        root_path: Repository root path.
        module_map: Mapping of file_path -> module_name.

    Returns:
        Dict mapping module_name -> complexity_score.
    """
    result: dict[str, int] = {}

    for file_path in file_paths:
        module_name = module_map.get(file_path)
        if not module_name:
            continue
        score = compute_file_complexity(file_path, root_path)
        if score is not None:
            result[module_name] = score

    return result


def compute_complexity_delta(
    baseline: dict[str, int],
    current: dict[str, int],
) -> dict[str, int]:
    """
    Compute per-module complexity change since the baseline snapshot.

    Only returns modules where complexity changed.
    Positive delta = complexity increased (more branches added).
    Negative delta = complexity decreased (code simplified).

    Args:
        baseline: Module -> complexity from previous snapshot.
        current: Module -> complexity from current run.

    Returns:
        Dict of module_name -> delta (only changed modules).
    """
    delta: dict[str, int] = {}

    all_modules = set(baseline) | set(current)

    for module in all_modules:
        prev = baseline.get(module, 0)
        curr = current.get(module, 0)
        if curr != prev:
            delta[module] = curr - prev

    return delta


def summarise_complexity(
    complexity_map: dict[str, int],
    delta: dict[str, int] | None = None,
) -> dict:
    """
    Produce a summary of complexity metrics for the report.

    Args:
        complexity_map: Module -> complexity scores.
        delta: Module -> delta values (optional).

    Returns:
        Summary dict with totals, top offenders, and delta breakdown.
    """
    if not complexity_map:
        return {
            "total_complexity": 0,
            "avg_complexity": 0.0,
            "top_complex_modules": [],
            "complexity_delta": {},
            "total_positive_delta": 0,
        }

    scores = list(complexity_map.values())
    total = sum(scores)
    avg = round(total / len(scores), 2)

    top = sorted(complexity_map.items(), key=lambda x: -x[1])[:10]
    top_modules = [{"module": m, "complexity_score": s} for m, s in top]

    total_positive_delta = 0
    if delta:
        total_positive_delta = sum(v for v in delta.values() if v > 0)

    return {
        "total_complexity": total,
        "avg_complexity": avg,
        "top_complex_modules": top_modules,
        "complexity_delta": delta or {},
        "total_positive_delta": total_positive_delta,
    }
