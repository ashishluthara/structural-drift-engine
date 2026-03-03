"""
drift.py — Domain-Aware Boundary Violation Detection  (Phase 3)

Improvements over Phase 2:
    - If config.has_domains(): uses domain grouping instead of folder heuristics
    - Justification overrides: approved violations are suppressed and returned
      separately as "Approved Architectural Exceptions"
    - Falls back to folder-based detection when no domains configured
"""

from config import DriftConfig


def _get_folder_boundary(module_name: str) -> str | None:
    """Extract the top-level package from a dotted module name."""
    parts = module_name.split(".")
    return parts[0] if len(parts) > 1 else None


def detect_boundary_violations(
    graph: dict[str, list[str]],
    config: DriftConfig,
) -> tuple[list[dict], list[dict]]:
    """
    Detect cross-boundary imports, returning both violations and approved exceptions.

    Uses domain-based grouping when config.has_domains() is True,
    otherwise falls back to folder-based grouping (Phase 2 behaviour).

    Args:
        graph: Dependency graph {module: [dependencies]}.
        config: DriftConfig with boundary and override settings.

    Returns:
        Tuple of:
            violations          — list of unapproved cross-boundary imports
            approved_exceptions — list of suppressed imports with approval reason
    """
    if config.has_domains():
        return _detect_domain_violations(graph, config)
    else:
        return _detect_folder_violations(graph, config)


def _detect_folder_violations(
    graph: dict[str, list[str]],
    config: DriftConfig,
) -> tuple[list[dict], list[dict]]:
    """Folder-based boundary detection (Phase 2 behaviour, now with overrides)."""
    violations  = []
    approved    = []

    for source, deps in graph.items():
        source_boundary = _get_folder_boundary(source)
        if not source_boundary:
            continue
        if config.is_boundary_ignored(source_boundary):
            continue

        for target in deps:
            target_boundary = _get_folder_boundary(target)
            if not target_boundary or source_boundary == target_boundary:
                continue
            if config.is_boundary_ignored(target_boundary):
                continue

            entry = {
                "source":           source,
                "target":           target,
                "type":             "cross_boundary",
                "source_boundary":  source_boundary,
                "target_boundary":  target_boundary,
            }

            if config.is_violation_approved(source_boundary, target_boundary):
                approved.append({**entry, "reason": "approved_violation"})
            elif config.is_dependency_allowed(source_boundary, target_boundary):
                approved.append({**entry, "reason": "allowed_dependency"})
            else:
                violations.append(entry)

    return (
        sorted(violations, key=lambda v: (v["source"], v["target"])),
        sorted(approved,   key=lambda v: (v["source"], v["target"])),
    )


def _detect_domain_violations(
    graph: dict[str, list[str]],
    config: DriftConfig,
) -> tuple[list[dict], list[dict]]:
    """
    Domain-based boundary detection.

    Uses config.domains patterns (fnmatch) to resolve modules to domains.
    Ungrouped modules are ignored (no domain = no boundary).
    """
    violations = []
    approved   = []

    for source, deps in graph.items():
        source_domain = config.resolve_domain(source)
        if not source_domain:
            continue

        for target in deps:
            target_domain = config.resolve_domain(target)
            if not target_domain or source_domain == target_domain:
                continue

            entry = {
                "source":          source,
                "target":          target,
                "type":            "cross_domain",
                "source_boundary": source_domain,
                "target_boundary": target_domain,
            }

            if config.is_violation_approved(source_domain, target_domain):
                approved.append({**entry, "reason": "approved_violation"})
            elif config.is_domain_dependency_allowed(source_domain, target_domain):
                approved.append({**entry, "reason": "allowed_domain_dependency"})
            else:
                violations.append(entry)

    return (
        sorted(violations, key=lambda v: (v["source"], v["target"])),
        sorted(approved,   key=lambda v: (v["source"], v["target"])),
    )


def partition_cycles(
    cycles: list[list[str]],
    config: DriftConfig,
) -> tuple[list[list[str]], list[dict]]:
    """
    Split detected cycles into penalised cycles and approved exceptions.

    Args:
        cycles: All detected cycles.
        config: DriftConfig with approved_cycles list.

    Returns:
        Tuple of:
            active_cycles   — cycles that contribute to the penalty
            approved_cycles — cycles suppressed with their label
    """
    active   = []
    approved = []

    for cycle in cycles:
        if config.is_cycle_approved(cycle):
            approved.append({
                "cycle": cycle,
                "label": config.get_approved_cycle_label(cycle),
            })
        else:
            active.append(cycle)

    return active, approved
