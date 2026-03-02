"""
drift.py — Boundary Violation Detection & Normalized Drift Score

Scoring Philosophy
------------------
Flat per-item penalties punish large codebases unfairly.
A monolith with 200 modules and 3 cycles is healthier than a
microservice with 10 modules and 3 cycles.

All penalties are therefore normalized against repo size:

    cycle_penalty     = (cycles      / total_modules) * 50   → cap 40
    violation_penalty = (violations  / max(total_edges, 1))  * 50   → cap 35
    coupling_penalty  = (high_coupling / total_modules)       * 20   → cap 20

    drift_score = max(0, 100 - cycle_penalty - violation_penalty - coupling_penalty)

Assumptions
-----------
- Top-level folders represent architectural boundaries (billing/, auth/, ...).
- Any import crossing those folders is a boundary violation.
"""

import math


def _get_boundary(module_name: str) -> str | None:
    """
    Extract the top-level boundary (package) from a module name.

    Examples:
        billing.service.utils  ->  billing
        main                   ->  None  (root-level, no boundary)
    """
    parts = module_name.split(".")
    return parts[0] if len(parts) > 1 else None


def detect_boundary_violations(graph: dict[str, list[str]]) -> list[dict]:
    """
    Detect cross-boundary imports between top-level packages.

    A violation is raised when a module in package A imports a module
    from package B (A != B), and both are sub-packages (not root-level).

    Args:
        graph: Dependency graph {module: [dependencies]}.

    Returns:
        List of violation dicts with keys:
            source, target, type, source_boundary, target_boundary
    """
    violations = []

    for source, deps in graph.items():
        source_boundary = _get_boundary(source)
        if not source_boundary:
            continue

        for target in deps:
            target_boundary = _get_boundary(target)
            if not target_boundary:
                continue

            if source_boundary != target_boundary:
                violations.append({
                    "source": source,
                    "target": target,
                    "type": "cross_boundary",
                    "source_boundary": source_boundary,
                    "target_boundary": target_boundary,
                })

    return sorted(violations, key=lambda v: (v["source"], v["target"]))


def calculate_drift_score(
    cycles: list[list[str]],
    violations: list[dict],
    high_coupling_modules: list[dict],
    total_modules: int,
    total_edges: int,
) -> dict:
    """
    Calculate the Drift Score using a size-normalized heuristic.

    All penalties are expressed as ratios against repo size so the score
    stays comparable across codebases of different scales.

    Formula:
        cycle_penalty     = min(40, ceil((n_cycles     / n_modules) * 50))
        violation_penalty = min(35, ceil((n_violations / n_edges  ) * 50))
        coupling_penalty  = min(20, ceil((n_hc_modules / n_modules) * 20))
        drift_score       = max(0, 100 - sum_of_penalties)

    Args:
        cycles: List of detected dependency cycles.
        violations: List of cross-boundary violation dicts.
        high_coupling_modules: List of high-coupling module dicts.
        total_modules: Total module count (denominator for normalization).
        total_edges: Total edge count (denominator for normalization).

    Returns:
        Dict with keys: drift_score, penalty_breakdown
    """
    n_modules = max(1, total_modules)
    n_edges   = max(1, total_edges)

    cycle_ratio     = len(cycles)                / n_modules
    violation_ratio = len(violations)            / n_edges
    coupling_ratio  = len(high_coupling_modules) / n_modules

    cycle_penalty     = min(40, math.ceil(cycle_ratio     * 50))
    violation_penalty = min(35, math.ceil(violation_ratio * 50))
    coupling_penalty  = min(20, math.ceil(coupling_ratio  * 20))

    score = max(0, 100 - cycle_penalty - violation_penalty - coupling_penalty)

    return {
        "drift_score": score,
        "penalty_breakdown": {
            "circular_dependencies": {
                "count":   len(cycles),
                "ratio":   round(cycle_ratio, 4),
                "penalty": cycle_penalty,
                "formula": "min(40, ceil((cycles / modules) x 50))",
            },
            "boundary_violations": {
                "count":   len(violations),
                "ratio":   round(violation_ratio, 4),
                "penalty": violation_penalty,
                "formula": "min(35, ceil((violations / edges) x 50))",
            },
            "high_coupling_modules": {
                "count":   len(high_coupling_modules),
                "ratio":   round(coupling_ratio, 4),
                "penalty": coupling_penalty,
                "formula": "min(20, ceil((high_coupling / modules) x 20))",
            },
        },
    }
