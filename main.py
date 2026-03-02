"""
main.py — Structural Drift Engine CLI v2

Usage:
    python main.py --path /path/to/repo [options]

Options:
    --path              Repository root to analyse (required).
    --save-snapshot     Overwrite baseline snapshot.
    --no-snapshot       Skip snapshot loading and saving.
    --output-env        Write .drift_env for GitHub Actions.
    --pr-comment        Post/update PR comment via GitHub API.
    --max-drop          Max score drop before CI fails (default: 5).
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from config import load_config
from scanner import scan_repository, file_path_to_module_name
from graph_builder import build_dependency_graph
from metrics import detect_cycles, compute_coupling_metrics
from drift import detect_boundary_violations
from complexity import compute_complexity_map, compute_complexity_delta, summarise_complexity
from duplication import detect_duplicates
from drift_index import calculate_drift_index
from snapshot import load_snapshot, save_snapshot, compare_snapshots
from utils import (
    write_json_report,
    print_header,
    print_drift_index,
    print_cycles,
    print_high_coupling,
    print_violations,
    print_duplicates,
    print_complexity,
    print_comparison,
    print_index_breakdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="structural-drift",
        description="Analyse architectural drift in a Python repository.",
    )
    parser.add_argument("--path", required=True, help="Repository root path.")
    parser.add_argument("--save-snapshot", action="store_true")
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--output-env", action="store_true")
    parser.add_argument("--pr-comment", action="store_true")
    parser.add_argument("--max-drop", type=int, default=5,
                        help="Max index drop vs baseline before CI fails (default: 5).")
    return parser.parse_args()


def run_analysis(repo_path: str):
    """Run the full v2 analysis pipeline. Returns all result objects."""

    # Config
    config = load_config(repo_path)
    print(f"  Config: ignore_boundaries={sorted(config.ignore_boundaries)}"
          f"  strict={config.strict_mode}")

    # Scan
    print(f"\nScanning: {repo_path}")
    file_paths = scan_repository(repo_path, ignore_test_dirs=config.ignore_test_dirs)
    print(f"  Found {len(file_paths)} Python file(s)")

    # Module map (needed by multiple stages)
    module_map = {fp: file_path_to_module_name(fp) for fp in file_paths}

    # Graph
    print("Building dependency graph...")
    graph = build_dependency_graph(file_paths, repo_path)

    # Metrics
    print("Computing coupling metrics...")
    coupling_metrics = compute_coupling_metrics(
        graph,
        high_coupling_percentile=config.high_coupling_percentile,
    )

    # Cycles
    print("Detecting cycles...")
    cycles = detect_cycles(graph)

    # Boundary violations (config-aware)
    print("Detecting boundary violations...")
    violations = detect_boundary_violations(graph, config)

    # Complexity
    print("Estimating complexity...")
    complexity_map = compute_complexity_map(file_paths, repo_path, module_map)

    # Duplication
    print("Detecting duplication...")
    duplicate_pairs = detect_duplicates(
        file_paths,
        repo_path,
        module_map,
        threshold=config.duplication_threshold,
        min_lines=config.min_lines_for_duplication,
        max_modules=config.max_modules_for_duplication,
    )

    return (
        config,
        graph,
        coupling_metrics,
        cycles,
        violations,
        complexity_map,
        duplicate_pairs,
    )


def main() -> None:
    args = parse_args()
    repo_path = os.path.realpath(args.path)

    try:
        (
            config,
            graph,
            coupling_metrics,
            cycles,
            violations,
            complexity_map,
            duplicate_pairs,
        ) = run_analysis(repo_path)
    except ValueError as exc:
        print(f"\n[ERROR] {exc}")
        sys.exit(1)

    # -- Snapshot & complexity delta ------------------------------------------
    baseline = None
    comparison = None
    baseline_complexity = {}

    if not args.no_snapshot:
        baseline = load_snapshot(repo_path)
        if baseline is not None:
            baseline_complexity = baseline.get("complexity_map", {})
        else:
            print("  No baseline snapshot found — this run will become the baseline.")

    # Whether a baseline existed before this run (set before we potentially save one)
    first_run = (baseline is None) and (not args.no_snapshot)

    complexity_delta = compute_complexity_delta(baseline_complexity, complexity_map)
    complexity_summary = summarise_complexity(complexity_map, complexity_delta)

    # -- Drift Index ----------------------------------------------------------
    # On first run: complexity penalty is suppressed (no baseline to delta against).
    # Cycles, violations, coupling, and duplication are still scored — those are
    # absolute structural facts, not delta-dependent.
    drift_index_result = calculate_drift_index(
        cycles=cycles,
        high_coupling_modules=coupling_metrics["high_coupling_modules"],
        duplicate_pairs=duplicate_pairs,
        total_positive_complexity_delta=0 if first_run else complexity_summary["total_positive_delta"],
        total_complexity=complexity_summary["total_complexity"],
        first_run=first_run,
    )

    # -- Snapshot comparison --------------------------------------------------
    if baseline is not None:
        comparison = compare_snapshots(baseline, {
            "drift_index":      drift_index_result["drift_index"],
            "cycles":           cycles,
            "violations":       violations,
            "coupling_metrics": coupling_metrics,
            "duplicate_pairs":  duplicate_pairs,
            "complexity_map":   complexity_map,
        })

    # -- Save snapshot --------------------------------------------------------
    if not args.no_snapshot and (args.save_snapshot or baseline is None):
        snap_path = save_snapshot(
            root_path=repo_path,
            graph=graph,
            coupling_metrics=coupling_metrics,
            cycles=cycles,
            violations=violations,
            duplicate_pairs=duplicate_pairs,
            complexity_summary=complexity_summary,
            complexity_map=complexity_map,
            drift_index_result=drift_index_result,
            config_dict=config.to_dict(),
        )
        print(f"  Snapshot saved -> {snap_path}")

    # -- Output env (GitHub Actions) ------------------------------------------
    if args.output_env:
        index_delta = comparison["index_delta"] if comparison else ""
        lines = [
            f"drift_score={drift_index_result['drift_index']}",
            f"cycles_count={len(cycles)}",
            f"violations_count={len(violations)}",
            f"score_delta={index_delta}",
        ]
        (Path(repo_path) / ".drift_env").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    # -- PR comment -----------------------------------------------------------
    should_fail = False
    if args.pr_comment:
        from pr_comment import post_pr_comment
        index_delta = comparison["index_delta"] if comparison else None
        should_fail = post_pr_comment(
            drift_index=drift_index_result["drift_index"],
            severity=drift_index_result["severity"],
            index_delta=index_delta,
            cycles=cycles,
            violations=violations,
            coupling_metrics=coupling_metrics,
            duplicate_pairs=duplicate_pairs,
            complexity_summary=complexity_summary,
            drift_components=drift_index_result["components"],
            comparison=comparison,
            first_run=first_run,
            max_drop=args.max_drop,
        )

    # -- JSON report ----------------------------------------------------------
    report_path = write_json_report(
        output_path=repo_path,
        graph=graph,
        coupling_metrics=coupling_metrics,
        cycles=cycles,
        violations=violations,
        duplicate_pairs=duplicate_pairs,
        complexity_summary=complexity_summary,
        drift_index_result=drift_index_result,
        comparison=comparison,
    )

    # -- Terminal output ------------------------------------------------------
    print_header("Structural Drift Report")

    if first_run:
        print("\n  ℹ️  Baseline initialized. No delta comparison available.")
        print("     Complexity penalty suppressed on first run.")

    print_drift_index(drift_index_result["drift_index"], drift_index_result["severity"], comparison)
    print(f"\n  Modules: {coupling_metrics['total_modules']}  "
          f"Edges: {coupling_metrics['total_edges']}  "
          f"Avg deps: {coupling_metrics['avg_dependencies']}")
    print_cycles(cycles)
    print_high_coupling(
        coupling_metrics["high_coupling_modules"],
        coupling_metrics["percentile_threshold"],
    )
    print_violations(violations)
    print_duplicates(duplicate_pairs)
    print_complexity(complexity_summary)

    if comparison is not None:
        print_comparison(comparison)

    print_index_breakdown(drift_index_result["components"])
    print(f"\nJSON report -> {report_path}\n")

    if should_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
