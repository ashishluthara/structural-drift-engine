"""
metrics.py — Dependency Metrics & Cycle Detection

Improvements over v1:
- Percentile-based high coupling detection (85th percentile default)
- Percentile score included in each flagged module's output
- Cycle detection unchanged (DFS) but benefits from cleaner internal-only graph
"""

import math


def detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """
    Detect circular dependencies using DFS with a recursion stack.

    Each cycle is returned as a path where the first and last element
    are the same module (the cycle entry point).

    Args:
        graph: Dependency graph {module: [dependencies]}.

    Returns:
        Deduplicated list of cycles.
    """
    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbor in graph.get(node, []):
            if neighbor not in graph:
                continue
            if neighbor not in visited:
                dfs(neighbor, path)
            elif neighbor in rec_stack:
                cycle_start = path.index(neighbor)
                cycles.append(path[cycle_start:] + [neighbor])

        path.pop()
        rec_stack.discard(node)

    for module in graph:
        if module not in visited:
            dfs(module, [])

    return _deduplicate_cycles(cycles)


def _deduplicate_cycles(cycles: list[list[str]]) -> list[list[str]]:
    """Remove duplicate cycles entered from different starting nodes."""
    seen: set[frozenset] = set()
    unique: list[list[str]] = []

    for cycle in cycles:
        key = frozenset(zip(cycle, cycle[1:]))
        if key not in seen:
            seen.add(key)
            unique.append(cycle)

    return unique


def compute_coupling_metrics(
    graph: dict[str, list[str]],
    high_coupling_percentile: int = 85,
) -> dict:
    """
    Compute coupling statistics using percentile-based thresholding.

    Instead of a fixed threshold (e.g. >10 deps), flags modules above
    the configured percentile of the dependency count distribution.
    This scales correctly with repo size.

    Args:
        graph: Dependency graph {module: [dependencies]}.
        high_coupling_percentile: Percentile above which a module is
            considered high-coupling (default: 85).

    Returns:
        Dict with keys:
            total_modules           — number of nodes
            total_edges             — total dependency count
            avg_dependencies        — mean deps per module
            high_coupling_modules   — list of {module, dependency_count, percentile}
            percentile_threshold    — the actual dep count at the threshold
    """
    total_modules = len(graph)

    if total_modules == 0:
        return {
            "total_modules": 0,
            "total_edges": 0,
            "avg_dependencies": 0.0,
            "high_coupling_modules": [],
            "percentile_threshold": 0,
        }

    edge_counts = {mod: len(deps) for mod, deps in graph.items()}
    total_edges = sum(edge_counts.values())
    avg_dependencies = round(total_edges / total_modules, 2)

    # Compute percentile threshold
    sorted_counts = sorted(edge_counts.values())
    threshold = _percentile(sorted_counts, high_coupling_percentile)

    # Assign percentile rank to each module
    def _rank_percentile(count: int) -> int:
        """Return the percentile rank of a given count in the distribution."""
        below = sum(1 for c in sorted_counts if c < count)
        return round((below / total_modules) * 100)

    # A module must exceed BOTH the percentile threshold AND have a minimum
    # absolute dependency count. This prevents flagging modules with 1–2 deps
    # just because most modules have 0 deps (which would set threshold=0).
    MIN_ABS_DEPS = max(3, int(avg_dependencies * 1.5))

    high_coupling_modules = [
        {
            "module": mod,
            "dependency_count": count,
            "percentile": _rank_percentile(count),
        }
        for mod, count in sorted(edge_counts.items(), key=lambda x: -x[1])
        if count > threshold and count >= MIN_ABS_DEPS
    ]

    return {
        "total_modules": total_modules,
        "total_edges": total_edges,
        "avg_dependencies": avg_dependencies,
        "high_coupling_modules": high_coupling_modules,
        "percentile_threshold": threshold,
    }


def _percentile(sorted_data: list[int], p: int) -> float:
    """
    Compute the p-th percentile of a sorted list.

    Uses linear interpolation (same as numpy's default).

    Args:
        sorted_data: Sorted list of numeric values.
        p: Percentile to compute (0–100).

    Returns:
        Interpolated percentile value.
    """
    if not sorted_data:
        return 0.0

    n = len(sorted_data)
    index = (p / 100) * (n - 1)
    lower = int(index)
    upper = min(lower + 1, n - 1)
    frac = index - lower

    return sorted_data[lower] + frac * (sorted_data[upper] - sorted_data[lower])
