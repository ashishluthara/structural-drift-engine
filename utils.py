"""
utils.py — Shared Utilities

Terminal formatting helpers and JSON report writer.
"""

import json
from pathlib import Path

REPORT_FILENAME = "drift_report.json"


def write_json_report(
    output_path: str,
    graph: dict,
    coupling_metrics: dict,
    cycles: list,
    violations: list,
    drift_result: dict,
    comparison: dict | None,
) -> str:
    """
    Write the full analysis results to a JSON report file.

    Args:
        output_path: Directory where drift_report.json will be written.
        graph: Dependency graph.
        coupling_metrics: Coupling metric results.
        cycles: Detected cycles.
        violations: Boundary violations.
        drift_result: Drift score and breakdown.
        comparison: Snapshot comparison results (or None).

    Returns:
        Absolute path of the written report file.
    """
    report = {
        "drift_score": drift_result["drift_score"],
        "penalty_breakdown": drift_result["penalty_breakdown"],
        "coupling_metrics": coupling_metrics,
        "circular_dependencies": cycles,
        "boundary_violations": violations,
        "dependency_graph": graph,
        "snapshot_comparison": comparison,
    }

    report_path = Path(output_path) / REPORT_FILENAME
    try:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Failed to write report: {exc}") from exc

    return str(report_path)


# ── Terminal rendering ────────────────────────────────────────────────────────

def print_header(title: str) -> None:
    """Print a section header."""
    print(f"\n{title}")
    print("=" * len(title))


def print_drift_score(score: int, comparison: dict | None) -> None:
    """Print the drift score with optional delta indicator."""
    delta_str = ""
    if comparison is not None:
        delta = comparison["score_delta"]
        if delta > 0:
            delta_str = f"  (↑ {delta} from baseline)"
        elif delta < 0:
            delta_str = f"  (↓ {abs(delta)} from baseline)"
        else:
            delta_str = "  (no change from baseline)"

    print(f"\nDrift Score: {score}{delta_str}")


def print_cycles(cycles: list[list[str]]) -> None:
    """Render circular dependency list to terminal."""
    print("\nCircular Dependencies:")
    if not cycles:
        print("  None detected ✓")
        return
    for cycle in cycles:
        print("  - " + " → ".join(cycle))


def print_high_coupling(modules: list[dict]) -> None:
    """Render high-coupling modules to terminal."""
    print("\nHigh Coupling Modules:")
    if not modules:
        print("  None detected ✓")
        return
    for item in modules:
        print(f"  - {item['module']} ({item['dependency_count']} deps)")


def print_violations(violations: list[dict]) -> None:
    """Render boundary violations to terminal."""
    print("\nBoundary Violations:")
    if not violations:
        print("  None detected ✓")
        return
    for v in violations:
        print(f"  - {v['source']} imports {v['target']}")


def print_comparison(comparison: dict) -> None:
    """Render snapshot comparison delta section."""
    print("\nChanges Since Baseline:")

    if comparison["new_cycles"]:
        print("  New Cycles:")
        for c in comparison["new_cycles"]:
            print("    + " + " → ".join(c))

    if comparison["resolved_cycles"]:
        print("  Resolved Cycles:")
        for c in comparison["resolved_cycles"]:
            print("    ✓ " + " → ".join(c))

    if comparison["new_violations"]:
        print("  New Violations:")
        for v in comparison["new_violations"]:
            print(f"    + {v['source']} → {v['target']}")

    if comparison["resolved_violations"]:
        print("  Resolved Violations:")
        for v in comparison["resolved_violations"]:
            print(f"    ✓ {v['source']} → {v['target']}")

    if comparison["newly_high_coupling"]:
        print("  Newly High-Coupling Modules:")
        for m in comparison["newly_high_coupling"]:
            print(f"    + {m}")

    all_clear = not any([
        comparison["new_cycles"],
        comparison["resolved_cycles"],
        comparison["new_violations"],
        comparison["resolved_violations"],
        comparison["newly_high_coupling"],
    ])
    if all_clear:
        print("  No structural changes detected ✓")


def print_coupling_summary(metrics: dict) -> None:
    """Print coupling summary stats."""
    print(
        f"\nCoupling Summary:  "
        f"{metrics['total_modules']} modules · "
        f"{metrics['total_edges']} edges · "
        f"avg {metrics['avg_dependencies']} deps/module"
    )
