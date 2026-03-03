"""
main.py — Structural Drift Engine CLI  (Phase 3)

Usage:
    python main.py --path /path/to/repo [options]

Options:
    --path              Repository root to analyse (required).
    --save-snapshot     Overwrite baseline / append to history.
    --no-snapshot       Skip all history loading and saving.
    --output-env        Write .drift_env for GitHub Actions.
    --pr-comment        Post/update PR comment via GitHub API.
    --max-drop          Max index drop vs baseline before CI fails (default: 5).
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
from drift import detect_boundary_violations, partition_cycles
from complexity import compute_complexity_map, compute_complexity_delta, summarise_complexity
from duplication import detect_duplicates
from drift_index import calculate_drift_index
from history import (
    load_history, load_baseline, append_run, save_history,
    build_full_snapshot, compute_trend_analytics, compare_with_baseline,
    _run_record,
)
from utils import (
    write_json_report, print_header, print_drift_index, print_trend,
    print_cycles, print_approved_exceptions, print_high_coupling,
    print_violations, print_duplicates, print_complexity,
    print_comparison, print_index_breakdown,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="structural-drift",
        description="Analyse architectural drift in a Python repository.",
    )
    parser.add_argument("--path",          required=True)
    parser.add_argument("--save-snapshot", action="store_true")
    parser.add_argument("--no-snapshot",   action="store_true")
    parser.add_argument("--output-env",    action="store_true")
    parser.add_argument("--pr-comment",    action="store_true")
    parser.add_argument("--max-drop",      type=int, default=5)
    return parser.parse_args()


def run_analysis(repo_path: str):
    """Run the full Phase 3 analysis pipeline."""
    config = load_config(repo_path)
    print(f"  Config: ignore_boundaries={sorted(config.ignore_boundaries)}"
          f"  strict={config.strict_mode}"
          + (f"  domains={list(config.domains)}" if config.has_domains() else ""))

    print(f"\nScanning: {repo_path}")
    file_paths = scan_repository(repo_path, ignore_test_dirs=config.ignore_test_dirs)
    print(f"  Found {len(file_paths)} Python file(s)")

    module_map = {fp: file_path_to_module_name(fp) for fp in file_paths}

    print("Building dependency graph...")
    graph = build_dependency_graph(file_paths, repo_path)

    print("Computing coupling metrics...")
    coupling_metrics = compute_coupling_metrics(
        graph, high_coupling_percentile=config.high_coupling_percentile
    )

    print("Detecting cycles...")
    all_cycles = detect_cycles(graph)
    active_cycles, approved_cycles = partition_cycles(all_cycles, config)

    print("Detecting boundary violations...")
    violations, approved_exceptions = detect_boundary_violations(graph, config)

    print("Estimating complexity...")
    complexity_map = compute_complexity_map(file_paths, repo_path, module_map)

    print("Detecting duplication...")
    duplicate_pairs = detect_duplicates(
        file_paths, repo_path, module_map,
        threshold=config.duplication_threshold,
        min_lines=config.min_lines_for_duplication,
        max_modules=config.max_modules_for_duplication,
    )

    return (
        config, graph, coupling_metrics,
        active_cycles, approved_cycles,
        violations, approved_exceptions,
        complexity_map, duplicate_pairs,
    )


def main() -> None:
    args      = parse_args()
    repo_path = os.path.realpath(args.path)

    try:
        (
            config, graph, coupling_metrics,
            active_cycles, approved_cycles,
            violations, approved_exceptions,
            complexity_map, duplicate_pairs,
        ) = run_analysis(repo_path)
    except ValueError as exc:
        print(f"\n[ERROR] {exc}")
        sys.exit(1)

    # ── History & baseline ────────────────────────────────────────────────────
    history          = {"runs": []}
    baseline         = None
    baseline_complexity = {}
    first_run        = True

    if not args.no_snapshot:
        history  = load_history(repo_path)
        baseline = load_baseline(repo_path)
        if baseline is not None:
            baseline_complexity = baseline.get("complexity_map", {})
            first_run = False
        else:
            print("  No baseline found — this run initialises history.")

    # ── Trend analytics (from existing history, before appending this run) ────
    trend_analytics = compute_trend_analytics(history)
    moving_average  = trend_analytics.get("moving_avg")          # 7-run MA
    previous_index  = history["runs"][-1]["drift_index"] if history["runs"] else None

    # ── Complexity ────────────────────────────────────────────────────────────
    complexity_delta   = compute_complexity_delta(baseline_complexity, complexity_map)
    complexity_summary = summarise_complexity(complexity_map, complexity_delta)

    # ── Drift Index ───────────────────────────────────────────────────────────
    # Only active (non-approved) cycles count against the score
    drift_index_result = calculate_drift_index(
        cycles=active_cycles,
        high_coupling_modules=coupling_metrics["high_coupling_modules"],
        duplicate_pairs=duplicate_pairs,
        total_positive_complexity_delta=0 if first_run else complexity_summary["total_positive_delta"],
        total_complexity=complexity_summary["total_complexity"],
        total_modules=coupling_metrics["total_modules"],
        first_run=first_run,
        moving_average=moving_average,
        previous_index=previous_index,
    )

    # ── Snapshot comparison ───────────────────────────────────────────────────
    current_snapshot = build_full_snapshot(
        drift_index_result=drift_index_result,
        graph=graph,
        coupling_metrics=coupling_metrics,
        cycles=active_cycles,
        violations=violations,
        duplicate_pairs=duplicate_pairs,
        complexity_summary=complexity_summary,
        complexity_map=complexity_map,
        config_dict=config.to_dict(),
    )
    comparison = compare_with_baseline(baseline, current_snapshot)

    # ── Append run to history + save ─────────────────────────────────────────
    if not args.no_snapshot and (args.save_snapshot or first_run):
        run_rec = _run_record(
            drift_index=drift_index_result["drift_index"],
            cycles=active_cycles,
            coupling_metrics=coupling_metrics,
            duplicate_pairs=duplicate_pairs,
            complexity_summary=complexity_summary,
            drift_components=drift_index_result["components"],
            violations=violations,
        )
        updated_history = append_run(repo_path, run_rec)
        hist_path = save_history(repo_path, updated_history, current_snapshot)
        print(f"  History saved -> {hist_path}  ({len(updated_history['runs'])} runs)")

    # Recompute trend analytics after appending so the current run is included
    # when reporting (only matters for the first run of a session)
    if not first_run or args.save_snapshot:
        trend_analytics = compute_trend_analytics(load_history(repo_path))

    # ── Output env (GitHub Actions) ───────────────────────────────────────────
    if args.output_env:
        index_delta = comparison["index_delta"] if comparison else ""
        lines = [
            f"drift_score={drift_index_result['drift_index']}",
            f"cycles_count={len(active_cycles)}",
            f"violations_count={len(violations)}",
            f"score_delta={index_delta}",
        ]
        (Path(repo_path) / ".drift_env").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    # ── PR comment ────────────────────────────────────────────────────────────
    should_fail = False
    if args.pr_comment:
        from pr_comment import post_pr_comment
        index_delta = comparison["index_delta"] if comparison else None
        should_fail = post_pr_comment(
            drift_index=drift_index_result["drift_index"],
            severity=drift_index_result["severity"],
            index_delta=index_delta,
            cycles=active_cycles,
            approved_cycles=approved_cycles,
            violations=violations,
            approved_exceptions=approved_exceptions,
            coupling_metrics=coupling_metrics,
            duplicate_pairs=duplicate_pairs,
            complexity_summary=complexity_summary,
            drift_result=drift_index_result,
            comparison=comparison,
            trend_analytics=trend_analytics,
            first_run=first_run,
            max_drop=args.max_drop,
        )

    # ── JSON report ───────────────────────────────────────────────────────────
    report_path = write_json_report(
        output_path=repo_path,
        graph=graph,
        coupling_metrics=coupling_metrics,
        cycles=active_cycles,
        approved_cycles=approved_cycles,
        violations=violations,
        approved_exceptions=approved_exceptions,
        duplicate_pairs=duplicate_pairs,
        complexity_summary=complexity_summary,
        drift_index_result=drift_index_result,
        comparison=comparison,
        trend_analytics=trend_analytics,
    )

    # ── Terminal output ───────────────────────────────────────────────────────
    print_header("Structural Drift Report")

    if first_run:
        print("\n  ℹ️  Baseline initialized. No delta comparison available.")
        print("     Complexity penalty suppressed on first run.")

    print_drift_index(
        drift_index_result["drift_index"],
        drift_index_result["severity"],
        comparison,
        drift_index_result,
        trend_analytics,
    )
    print(f"\n  Modules: {coupling_metrics['total_modules']}  "
          f"Edges: {coupling_metrics['total_edges']}  "
          f"Avg deps: {coupling_metrics['avg_dependencies']}")

    print_trend(trend_analytics)
    print_approved_exceptions(approved_cycles, approved_exceptions)
    print_cycles(active_cycles)
    print_high_coupling(
        coupling_metrics["high_coupling_modules"],
        coupling_metrics["high_coupling_percentile"],
    )
    print_violations(violations)
    print_duplicates(duplicate_pairs)
    print_complexity(complexity_summary)

    if comparison is not None:
        print_comparison(comparison)

    print_index_breakdown(
        drift_index_result["components"],
        drift_index_result.get("size_scale", 1.0),
    )
    print(f"\nJSON report -> {report_path}\n")

    if should_fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
