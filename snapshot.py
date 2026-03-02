"""
snapshot.py — Extended Baseline Snapshot System

Extended over v1 to include:
- Duplication metadata
- Complexity scores per module
- Drift Index components
- Config hash for detecting config changes between runs
"""

import json
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_FILENAME = ".drift_baseline.json"


def save_snapshot(
    root_path: str,
    graph: dict,
    coupling_metrics: dict,
    cycles: list,
    violations: list,
    duplicate_pairs: list,
    complexity_summary: dict,
    complexity_map: dict,
    drift_index_result: dict,
    config_dict: dict,
) -> str:
    """
    Persist current analysis results as the baseline snapshot.

    Args:
        root_path: Repository root (snapshot saved here).
        graph: Dependency graph.
        coupling_metrics: Coupling metric results.
        cycles: Detected cycles.
        violations: Boundary violations.
        duplicate_pairs: Detected duplicate module pairs.
        complexity_summary: Complexity summary dict.
        complexity_map: Per-module raw complexity scores.
        drift_index_result: Drift index and component breakdown.
        config_dict: Serialised DriftConfig for reference.

    Returns:
        Absolute path to the saved snapshot file.
    """
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "drift_index": drift_index_result["drift_index"],
        "graph": graph,
        "coupling_metrics": coupling_metrics,
        "cycles": cycles,
        "violations": violations,
        "duplicate_pairs": duplicate_pairs,
        "complexity_map": complexity_map,
        "complexity_summary": complexity_summary,
        "drift_index_components": drift_index_result["components"],
        "config": config_dict,
    }

    snapshot_path = Path(root_path) / SNAPSHOT_FILENAME

    try:
        snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Failed to write snapshot: {exc}") from exc

    return str(snapshot_path)


def load_snapshot(root_path: str) -> dict | None:
    """
    Load the existing baseline snapshot.

    Returns None gracefully if not found or malformed.
    """
    snapshot_path = Path(root_path) / SNAPSHOT_FILENAME

    if not snapshot_path.exists():
        return None

    try:
        raw = snapshot_path.read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  [WARN] Could not load snapshot: {exc}")
        return None


def compare_snapshots(baseline: dict, current: dict) -> dict:
    """
    Compare current analysis against saved baseline.

    Produces a delta report covering:
        - Drift Index change
        - New / resolved cycles
        - New / resolved violations
        - Newly high-coupling modules
        - New duplicate pairs
        - Modules with increased complexity

    Args:
        baseline: Previously saved snapshot dict.
        current: Current run results (same structure).

    Returns:
        Comparison delta dict.
    """
    baseline_index = baseline.get("drift_index", 100)
    current_index  = current.get("drift_index", 100)
    index_delta    = current_index - baseline_index

    # Cycles
    baseline_cycles = _cycles_to_set(baseline.get("cycles", []))
    current_cycles  = _cycles_to_set(current.get("cycles", []))
    new_cycles      = [c for c in current.get("cycles", []) if _cycle_key(c) not in baseline_cycles]
    resolved_cycles = [c for c in baseline.get("cycles", []) if _cycle_key(c) not in current_cycles]

    # Violations
    baseline_viols = _violations_to_set(baseline.get("violations", []))
    current_viols  = current.get("violations", [])
    new_violations = [v for v in current_viols if _violation_key(v) not in baseline_viols]
    resolved_violations = [
        v for v in baseline.get("violations", [])
        if _violation_key(v) not in _violations_to_set(current_viols)
    ]

    # High coupling
    baseline_hc = {m["module"] for m in baseline.get("coupling_metrics", {}).get("high_coupling_modules", [])}
    current_hc  = {m["module"] for m in current.get("coupling_metrics", {}).get("high_coupling_modules", [])}
    newly_high_coupling = sorted(current_hc - baseline_hc)

    # Duplicates
    baseline_dups = _dup_set(baseline.get("duplicate_pairs", []))
    current_dup_list = current.get("duplicate_pairs", [])
    new_duplicates = [d for d in current_dup_list if _dup_key(d) not in baseline_dups]

    # Complexity increases
    baseline_complexity = baseline.get("complexity_map", {})
    current_complexity  = current.get("complexity_map", {})
    complexity_increases = {
        mod: current_complexity[mod] - baseline_complexity.get(mod, 0)
        for mod in current_complexity
        if current_complexity[mod] > baseline_complexity.get(mod, 0)
    }

    return {
        "index_delta": index_delta,
        "new_cycles": new_cycles,
        "resolved_cycles": resolved_cycles,
        "new_violations": new_violations,
        "resolved_violations": resolved_violations,
        "newly_high_coupling": newly_high_coupling,
        "new_duplicates": new_duplicates,
        "complexity_increases": complexity_increases,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cycle_key(cycle):
    return frozenset(zip(cycle, cycle[1:]))

def _cycles_to_set(cycles):
    return {_cycle_key(c) for c in cycles}

def _violation_key(v):
    return (v.get("source"), v.get("target"))

def _violations_to_set(violations):
    return {_violation_key(v) for v in violations}

def _dup_key(d):
    return frozenset([d.get("module_a"), d.get("module_b")])

def _dup_set(dups):
    return {_dup_key(d) for d in dups}
