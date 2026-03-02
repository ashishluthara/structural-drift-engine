"""
main.py -- Structural Drift Engine CLI

Usage:
    python main.py --path /path/to/repo [options]

Options:
    --path            Repository root to analyse (required).
    --threshold       High-coupling dependency threshold (default: 10).
    --save-snapshot   Overwrite baseline snapshot with current results.
    --no-snapshot     Skip snapshot loading and saving entirely.
    --output-env      Write key outputs to .drift_env (used by GitHub Actions).
    --pr-comment      Post/update a structured comment on the current PR.
    --drift-threshold Minimum acceptable score delta before CI fails (default: -5).
"""

import argparse
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from scanner import scan_repository
from graph_builder import build_dependency_graph
from metrics import detect_cycles, compute_coupling_metrics
from drift import detect_boundary_violations, calculate_drift_score
from snapshot import load_snapshot, save_snapshot, compare_snapshots
from utils import (
    write_json_report,
    print_header,
    print_drift_score,
    print_cycles,
    print_high_coupling,
    print_violations,
    print_comparison,
    print_coupling_summary,
)


def parse_args() -> argparse.Namespace:
    """Parse and return CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="structural-drift",
        description="Analyse architectural drift in a Python repository.",
    )
    parser.add_argument("--path", required=True, help="Path to the repository root.")
    parser.add_argument("--threshold", type=int, default=10,
                        help="Dep count above which a module is high-coupling (default: 10).")
    parser.add_argument("--save-snapshot", action="store_true",
                        help="Overwrite baseline snapshot with current results.")
    parser.add_argument("--no-snapshot", action="store_true",
                        help="Skip snapshot loading and saving entirely.")
    parser.add_argument("--output-env", action="store_true",
                        help="Write key outputs to .drift_env for GitHub Actions.")
    parser.add_argument("--pr-comment", action="store_true",
                        help="Post/update a structured comment on the current GitHub PR.")
    parser.add_argument("--drift-threshold", type=int, default=-5,
                        help="Minimum acceptable score delta before CI fails (default: -5).")
    return parser.parse_args()


def run_analysis(repo_path: str, threshold: int) -> tuple[dict, dict, list, list, dict]:
    """
    Execute the full analysis pipeline.

    Returns:
        (graph, coupling_metrics, cycles, violations, drift_result)
    """
    print(f"\nScanning: {repo_path}")
    file_paths = scan_repository(repo_path)
    print(f"  Found {len(file_paths)} Python file(s)")

    print("Building dependency graph...")
    graph = build_dependency_graph(file_paths, repo_path)

    print("Computing metrics...")
    coupling_metrics = compute_coupling_metrics(graph, high_coupling_threshold=threshold)
    cycles = detect_cycles(graph)
    violations = detect_boundary_violations(graph)

    # Normalized scoring requires total_modules and total_edges
    drift_result = calculate_drift_score(
        cycles=cycles,
        violations=violations,
        high_coupling_modules=coupling_metrics["high_coupling_modules"],
        total_modules=coupling_metrics["total_modules"],
        total_edges=coupling_metrics["total_edges"],
    )

    return graph, coupling_metrics, cycles, violations, drift_result


def write_env_outputs(
    repo_path: str,
    drift_result: dict,
    cycles: list,
    violations: list,
    comparison: dict | None,
) -> None:
    """Write .drift_env key=value file for GitHub Actions $GITHUB_OUTPUT parsing."""
    score_delta = ""
    if comparison is not None:
        score_delta = str(comparison["score_delta"])

    lines = [
        f"drift_score={drift_result['drift_score']}",
        f"cycles_count={len(cycles)}",
        f"violations_count={len(violations)}",
        f"score_delta={score_delta}",
    ]

    (Path(repo_path) / ".drift_env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    """CLI entry point."""
    args = parse_args()
    repo_path = os.path.realpath(args.path)

    try:
        graph, coupling_metrics, cycles, violations, drift_result = run_analysis(
            repo_path, args.threshold
        )
    except ValueError as exc:
        print(f"\n[ERROR] {exc}")
        sys.exit(1)

    # -- Snapshot comparison --------------------------------------------------
    comparison: dict | None = None

    if not args.no_snapshot:
        baseline = load_snapshot(repo_path)

        if baseline is not None:
            comparison = compare_snapshots(baseline, {
                "drift_score":    drift_result["drift_score"],
                "cycles":         cycles,
                "violations":     violations,
                "coupling_metrics": coupling_metrics,
            })
        else:
            print("  No baseline snapshot found -- this run will become the baseline.")

        if args.save_snapshot or baseline is None:
            snap_path = save_snapshot(
                root_path=repo_path,
                graph=graph,
                coupling_metrics=coupling_metrics,
                cycles=cycles,
                violations=violations,
                drift_result=drift_result,
            )
            print(f"  Snapshot saved -> {snap_path}")

    # -- GitHub Actions env outputs -------------------------------------------
    if args.output_env:
        write_env_outputs(repo_path, drift_result, cycles, violations, comparison)

    # -- PR comment -----------------------------------------------------------
    should_fail = False
    if args.pr_comment:
        from pr_comment import post_pr_comment
        score_delta = comparison["score_delta"] if comparison else None
        should_fail = post_pr_comment(
            drift_score=drift_result["drift_score"],
            score_delta=score_delta,
            cycles=cycles,
            violations=violations,
            coupling_metrics=coupling_metrics,
            penalty_breakdown=drift_result["penalty_breakdown"],
            drift_threshold=args.drift_threshold,
        )

    # -- JSON report ----------------------------------------------------------
    report_path = write_json_report(
        output_path=repo_path,
        graph=graph,
        coupling_metrics=coupling_metrics,
        cycles=cycles,
        violations=violations,
        drift_result=drift_result,
        comparison=comparison,
    )

    # -- Terminal output -------------------------------------------------------
    print_header("Structural Drift Report")
    print_drift_score(drift_result["drift_score"], comparison)
    print_coupling_summary(coupling_metrics)
    print_cycles(cycles)
    print_high_coupling(coupling_metrics["high_coupling_modules"])
    print_violations(violations)

    if comparison is not None:
        print_comparison(comparison)

    pb = drift_result["penalty_breakdown"]
    print("\nPenalty Breakdown (normalized):")
    print(f"  Circular deps  : -{pb['circular_dependencies']['penalty']}  "
          f"({pb['circular_dependencies']['count']} cycles, "
          f"ratio={pb['circular_dependencies']['ratio']:.2%})")
    print(f"  Violations     : -{pb['boundary_violations']['penalty']}  "
          f"({pb['boundary_violations']['count']} violations, "
          f"ratio={pb['boundary_violations']['ratio']:.2%})")
    print(f"  High coupling  : -{pb['high_coupling_modules']['penalty']}  "
          f"({pb['high_coupling_modules']['count']} modules, "
          f"ratio={pb['high_coupling_modules']['ratio']:.2%})")

    print(f"\nJSON report written -> {report_path}")
    print()

    if should_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
