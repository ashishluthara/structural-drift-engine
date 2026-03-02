"""
utils.py — Terminal Output & JSON Report Writer
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
    duplicate_pairs: list,
    complexity_summary: dict,
    drift_index_result: dict,
    comparison: dict | None,
) -> str:
    report = {
        "drift_index": drift_index_result["drift_index"],
        "drift_index_components": drift_index_result["components"],
        "coupling_metrics": coupling_metrics,
        "circular_dependencies": cycles,
        "boundary_violations": violations,
        "duplicate_modules": duplicate_pairs,
        "complexity": complexity_summary,
        "dependency_graph": graph,
        "snapshot_comparison": comparison,
    }
    report_path = Path(output_path) / REPORT_FILENAME
    try:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Failed to write report: {exc}") from exc
    return str(report_path)


def print_header(title: str) -> None:
    print(f"\n{title}")
    print("=" * len(title))


def print_drift_index(index: int, severity: str, comparison: dict | None) -> None:
    if comparison is None:
        delta_str = ""
    elif comparison["index_delta"] > 0:
        delta_str = f"  (↑ +{comparison['index_delta']} from baseline)"
    elif comparison["index_delta"] < 0:
        delta_str = f"  (↓ {comparison['index_delta']} from baseline)"
    else:
        delta_str = "  (no change from baseline)"

    if index >= 80:
        icon = "🟢"
    elif index >= 60:
        icon = "🟡"
    elif index >= 40:
        icon = "🟠"
    else:
        icon = "🔴"

    print(f"\n{icon}  Drift Index: {index}  —  {severity}{delta_str}")


def print_cycles(cycles: list) -> None:
    print("\nCircular Dependencies:")
    if not cycles:
        print("  ✓ None detected")
        return
    for cycle in cycles:
        print("  - " + " → ".join(cycle))


def print_high_coupling(modules: list, percentile_threshold: int) -> None:
    print(f"\nHigh Coupling Modules  (above {percentile_threshold:.0f}th percentile threshold):")
    if not modules:
        print("  ✓ None detected")
        return
    for m in modules:
        print(f"  - {m['module']}  ({m['dependency_count']} deps, {m['percentile']}th percentile)")


def print_violations(violations: list) -> None:
    print("\nBoundary Violations:")
    if not violations:
        print("  ✓ None detected")
        return
    for v in violations:
        print(f"  - {v['source']} → {v['target']}")


def print_duplicates(pairs: list) -> None:
    print("\nDuplicate Modules:")
    if not pairs:
        print("  ✓ None detected")
        return
    for p in pairs:
        pct = int(p["similarity"] * 100)
        print(f"  - {p['module_a']} ↔ {p['module_b']}  ({pct}% similar)")


def print_complexity(summary: dict) -> None:
    print(f"\nComplexity:  total={summary['total_complexity']}  "
          f"avg={summary['avg_complexity']}  "
          f"Δ_positive={summary['total_positive_delta']}")
    delta = summary.get("complexity_delta", {})
    increases = {m: d for m, d in delta.items() if d > 0}
    if increases:
        print("  Increased:")
        for mod, d in sorted(increases.items(), key=lambda x: -x[1])[:5]:
            print(f"    + {mod}  (+{d})")


def print_comparison(comparison: dict) -> None:
    print("\nChanges Since Baseline:")
    printed = False

    if comparison["new_cycles"]:
        printed = True
        print("  New Cycles:")
        for c in comparison["new_cycles"]:
            print("    + " + " → ".join(c))

    if comparison["resolved_cycles"]:
        printed = True
        print("  Resolved Cycles:")
        for c in comparison["resolved_cycles"]:
            print("    ✓ " + " → ".join(c))

    if comparison["new_violations"]:
        printed = True
        print("  New Violations:")
        for v in comparison["new_violations"]:
            print(f"    + {v['source']} → {v['target']}")

    if comparison["resolved_violations"]:
        printed = True
        print("  Resolved Violations:")
        for v in comparison["resolved_violations"]:
            print(f"    ✓ {v['source']} → {v['target']}")

    if comparison["new_duplicates"]:
        printed = True
        print("  New Duplicate Pairs:")
        for d in comparison["new_duplicates"]:
            print(f"    + {d['module_a']} ↔ {d['module_b']}")

    if comparison.get("complexity_increases"):
        printed = True
        print("  Complexity Increases:")
        for mod, delta in sorted(comparison["complexity_increases"].items(), key=lambda x: -x[1])[:5]:
            print(f"    + {mod}  (+{delta})")

    if not printed:
        print("  ✓ No structural changes detected")


def print_index_breakdown(components: dict) -> None:
    print("\nDrift Index Breakdown:")
    cp = components.get("circular_penalty", {})
    kp = components.get("coupling_penalty", {})
    dp = components.get("duplication_penalty", {})
    xp = components.get("complexity_penalty", {})
    print(f"  Circular deps   : −{cp.get('value',0)}  ({cp.get('count',0)} cycles)")
    print(f"  High coupling   : −{kp.get('value',0)}  (avg {kp.get('avg_percentile',0)}th pct)")
    print(f"  Duplication     : −{dp.get('value',0)}  ({dp.get('count',0)} pairs)")
    print(f"  Complexity Δ    : −{xp.get('value',0)}  (+{xp.get('total_positive_delta',0)} total)")
