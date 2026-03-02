"""
drift.py — Config-Aware Boundary Violation Detection

Improvements over v1:
- Loads allowed_dependencies from DriftConfig
- Suppresses violations for ignored boundaries (shared/, utils/, core/ etc.)
- Suppresses violations explicitly whitelisted in allowed_dependencies
- strict_mode disables all suppression
"""

from config import DriftConfig


def _get_boundary(module_name: str) -> str | None:
    """Extract the top-level package from a module name."""
    parts = module_name.split(".")
    return parts[0] if len(parts) > 1 else None


def detect_boundary_violations(
    graph: dict[str, list[str]],
    config: DriftConfig,
) -> list[dict]:
    """
    Detect cross-boundary imports, respecting the DriftConfig.

    A violation is suppressed when:
        - The source or target boundary is in config.ignore_boundaries
          (unless strict_mode is enabled)
        - The dependency is listed in config.allowed_dependencies

    Args:
        graph: Dependency graph {module: [dependencies]}.
        config: DriftConfig instance with boundary rules.

    Returns:
        List of violation dicts with keys:
            source, target, type, source_boundary, target_boundary
    """
    violations = []

    for source, deps in graph.items():
        source_boundary = _get_boundary(source)
        if not source_boundary:
            continue

        # Suppress violations from ignored source boundaries
        if config.is_boundary_ignored(source_boundary):
            continue

        for target in deps:
            target_boundary = _get_boundary(target)
            if not target_boundary:
                continue

            if source_boundary == target_boundary:
                continue

            # Suppress violations into ignored target boundaries
            if config.is_boundary_ignored(target_boundary):
                continue

            # Suppress explicitly allowed dependencies
            if config.is_dependency_allowed(source_boundary, target_boundary):
                continue

            violations.append({
                "source": source,
                "target": target,
                "type": "cross_boundary",
                "source_boundary": source_boundary,
                "target_boundary": target_boundary,
            })

    return sorted(violations, key=lambda v: (v["source"], v["target"]))
