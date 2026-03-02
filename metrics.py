"""
metrics.py — Dependency Metrics & Cycle Detection

Provides:
    - DFS-based circular dependency detection
    - Coupling metrics (total edges, average deps, high-coupling modules)
"""


def detect_cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """
    Detect circular dependencies in the dependency graph using DFS.

    Each cycle is returned as a path where the first and last elements
    are the same module (the entry point of the cycle).

    Example:
        ["billing.service", "auth.utils", "billing.service"]

    Args:
        graph: Dependency graph {module: [dependencies]}.

    Returns:
        List of cycles, each represented as a list of module name strings.
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
                # Found a cycle — slice from where the cycle starts
                cycle_start = path.index(neighbor)
                cycle = path[cycle_start:] + [neighbor]
                cycles.append(cycle)

        path.pop()
        rec_stack.discard(node)

    for module in graph:
        if module not in visited:
            dfs(module, [])

    return _deduplicate_cycles(cycles)


def _deduplicate_cycles(cycles: list[list[str]]) -> list[list[str]]:
    """
    Remove duplicate cycles that represent the same loop entered from
    different starting points.

    Args:
        cycles: Raw list of cycle paths from DFS.

    Returns:
        Deduplicated list of cycles.
    """
    seen: set[frozenset] = set()
    unique: list[list[str]] = []

    for cycle in cycles:
        # Use frozenset of edges as the canonical key
        edges = frozenset(zip(cycle, cycle[1:]))
        if edges not in seen:
            seen.add(edges)
            unique.append(cycle)

    return unique


def compute_coupling_metrics(
    graph: dict[str, list[str]],
    high_coupling_threshold: int = 10,
) -> dict:
    """
    Compute coupling statistics for the dependency graph.

    Args:
        graph: Dependency graph {module: [dependencies]}.
        high_coupling_threshold: Number of dependencies above which a module
            is considered high-coupling. Defaults to 10.

    Returns:
        Dict with keys:
            total_modules       — number of nodes in the graph
            total_edges         — total dependency count across all modules
            avg_dependencies    — mean dependencies per module (float)
            high_coupling_modules — list of {module, dependency_count} dicts
    """
    total_modules = len(graph)
    edge_counts = {mod: len(deps) for mod, deps in graph.items()}
    total_edges = sum(edge_counts.values())
    avg_dependencies = round(total_edges / total_modules, 2) if total_modules else 0.0

    high_coupling_modules = [
        {"module": mod, "dependency_count": count}
        for mod, count in sorted(edge_counts.items(), key=lambda x: -x[1])
        if count > high_coupling_threshold
    ]

    return {
        "total_modules": total_modules,
        "total_edges": total_edges,
        "avg_dependencies": avg_dependencies,
        "high_coupling_modules": high_coupling_modules,
    }
