"""
snapshot.py — Baseline Snapshot System

Saves and loads drift analysis results as JSON snapshots for comparison
between analysis runs. Enables tracking of architectural degradation over time.
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
    drift_result: dict,
) -> str:
    """
    Persist current analysis results as the baseline snapshot.

    The snapshot is saved as `.drift_baseline.json` at the repository root.

    Args:
        root_path: Repository root path (snapshot saved here).
        graph: Dependency graph.
        coupling_metrics: Coupling metric results.
        cycles: Detected dependency cycles.
        violations: Detected boundary violations.
        drift_result: Drift score and breakdown.

    Returns:
        Absolute path to the saved snapshot file.
    """
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "drift_score": drift_result["drift_score"],
        "graph": graph,
        "coupling_metrics": coupling_metrics,
        "cycles": cycles,
        "violations": violations,
        "penalty_breakdown": drift_result["penalty_breakdown"],
    }

    snapshot_path = Path(root_path) / SNAPSHOT_FILENAME

    try:
        snapshot_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Failed to write snapshot: {exc}") from exc

    return str(snapshot_path)


def load_snapshot(root_path: str) -> dict | None:
    """
    Load the existing baseline snapshot from the repository root.

    Args:
        root_path: Repository root path.

    Returns:
        Snapshot dict if found and valid, else None.
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
    Compare the current analysis results against the saved baseline.

    Args:
        baseline: Previously saved snapshot dict.
        current: Current run results dict (same structure).

    Returns:
        Dict with keys:
            score_delta            — current score minus baseline score
            new_cycles             — cycles present now but not in baseline
            resolved_cycles        — cycles in baseline but not now
            new_violations         — violations added since baseline
            resolved_violations    — violations resolved since baseline
            newly_high_coupling    — modules that became high-coupling
    """
    baseline_score = baseline.get("drift_score", 100)
    current_score = current.get("drift_score", 100)
    score_delta = current_score - baseline_score

    baseline_cycles = _cycles_to_set(baseline.get("cycles", []))
    current_cycles = _cycles_to_set(current.get("cycles", []))
    new_cycles = [c for c in current.get("cycles", []) if _cycle_key(c) not in baseline_cycles]
    resolved_cycles = [c for c in baseline.get("cycles", []) if _cycle_key(c) not in current_cycles]

    baseline_violations = _violations_to_set(baseline.get("violations", []))
    current_violations_list = current.get("violations", [])
    new_violations = [v for v in current_violations_list if _violation_key(v) not in baseline_violations]
    resolved_violations = [
        v for v in baseline.get("violations", [])
        if _violation_key(v) not in _violations_to_set(current_violations_list)
    ]

    baseline_hc = {m["module"] for m in baseline.get("coupling_metrics", {}).get("high_coupling_modules", [])}
    current_hc = {m["module"] for m in current.get("coupling_metrics", {}).get("high_coupling_modules", [])}
    newly_high_coupling = sorted(current_hc - baseline_hc)

    return {
        "score_delta": score_delta,
        "new_cycles": new_cycles,
        "resolved_cycles": resolved_cycles,
        "new_violations": new_violations,
        "resolved_violations": resolved_violations,
        "newly_high_coupling": newly_high_coupling,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _cycle_key(cycle: list[str]) -> frozenset:
    return frozenset(zip(cycle, cycle[1:]))


def _cycles_to_set(cycles: list[list[str]]) -> set:
    return {_cycle_key(c) for c in cycles}


def _violation_key(v: dict) -> tuple:
    return (v.get("source"), v.get("target"))


def _violations_to_set(violations: list[dict]) -> set:
    return {_violation_key(v) for v in violations}
