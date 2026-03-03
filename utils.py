"""
utils.py — Terminal Output & JSON Report Writer  (Phase 3)
"""

import json
from pathlib import Path

REPORT_FILENAME = "drift_report.json"


def write_json_report(
    output_path: str,
    graph: dict,
    coupling_metrics: dict,
    cycles: list,
    approved_cycles: list,
    violations: list,
    approved_exceptions: list,
    duplicate_pairs: list,
    complexity_summary: dict,
    drift_index_result: dict,
    comparison: dict | None,
    trend_analytics: dict,
) -> str:
    report = {
        "drift_index":             drift_index_result["drift_index"],
        "raw_score":               drift_index_result.get("raw_score"),
        "severity":                drift_index_result.get("severity"),
        "drift_index_components":  drift_index_result["components"],
        "smoothing_applied":       drift_index_result.get("smoothing_applied", False),
        "drop_capped":             drift_index_result.get("drop_capped", False),
        "size_scale":              drift_index_result.get("size_scale"),
        "coupling_metrics":        coupling_metrics,
        "circular_dependencies":   cycles,
        "approved_cycles":         approved_cycles,
        "boundary_violations":     violations,
        "approved_exceptions":     approved_exceptions,
        "duplicate_modules":       duplicate_pairs,
        "complexity":              complexity_summary,
        "dependency_graph":        graph,
        "snapshot_comparison":     comparison,
        "trend":                   trend_analytics,
    }
    report_path = Path(output_path) / REPORT_FILENAME
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return str(report_path)


def print_header(title: str) -> None:
    print(f"\n{title}")
    print("=" * len(title))


def print_drift_index(
    index: int,
    severity: str,
    comparison: dict | None,
    drift_result: dict,
    trend_analytics: dict,
) -> None:
    if comparison is None:
        delta_str = ""
    elif comparison["index_delta"] > 0:
        delta_str = f"  (↑ +{comparison['index_delta']} from baseline)"
    elif comparison["index_delta"] < 0:
        delta_str = f"  (↓ {comparison['index_delta']} from baseline)"
    else:
        delta_str = "  (no change from baseline)"

    # 5-run average delta
    ma = trend_analytics.get("moving_avg")
    ma_str = f"  {_ma_delta_str(index, ma)}" if ma is not None else ""

    if index >= 80:   icon = "🟢"
    elif index >= 60: icon = "🟡"
    elif index >= 40: icon = "🟠"
    else:             icon = "🔴"

    print(f"\n{icon}  Drift Index: {index}  —  {severity}{delta_str}{ma_str}")

    if drift_result.get("drop_capped"):
        print(f"  ⚠️  {drift_result['drop_cap_message']}")
    if drift_result.get("smoothing_applied"):
        raw = drift_result.get("raw_score")
        print(f"  (Raw structural score: {raw}  ·  Smoothed with 7-run MA)")


def _ma_delta_str(index: int, ma: float) -> str:
    diff = round(index - ma, 1)
    if diff > 0:   return f"(+{diff} vs 7-run avg)"
    if diff < 0:   return f"({diff} vs 7-run avg)"
    return ""


def print_trend(trend_analytics: dict) -> None:
    n = trend_analytics.get("run_count", 0)
    if n < 2:
        return

    print("\nDrift Trend:")
    ts = trend_analytics.get("trend_string", "")
    if ts:
        print(f"  {ts}")
    label = trend_analytics.get("trend_label", "")
    if label:
        print(f"  Trend: {label}")

    volatility = trend_analytics.get("volatility", 0.0)
    if trend_analytics.get("high_volatility"):
        print(f"  ⚠️  Drift volatility high (σ={volatility}). Scoring instability detected.")
    else:
        print(f"  Volatility: {'Low' if volatility < 2.0 else 'Moderate'}  (σ={volatility})")

    # % change summary
    pct = trend_analytics.get("pct_changes", {})
    if pct:
        parts = []
        if "coupling_pct" in pct and abs(pct["coupling_pct"]) >= 5:
            parts.append(f"coupling {_pct_str(pct['coupling_pct'])}")
        if "complexity_pct" in pct and abs(pct["complexity_pct"]) >= 5:
            parts.append(f"complexity {_pct_str(pct['complexity_pct'])}")
        if "duplication_pct" in pct and abs(pct["duplication_pct"]) >= 5:
            parts.append(f"duplication {_pct_str(pct['duplication_pct'])}")
        if parts:
            print(f"  % Change vs avg:  {',  '.join(parts)}")


def _pct_str(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct}%"


def print_cycles(cycles: list) -> None:
    print("\nCircular Dependencies:")
    if not cycles:
        print("  ✓ None detected")
        return
    for cycle in cycles:
        print("  - " + " → ".join(cycle))


def print_approved_exceptions(approved_cycles: list, approved_violations: list) -> None:
    if not approved_cycles and not approved_violations:
        return
    print("\nApproved Architectural Exceptions:")
    for ac in approved_cycles:
        print(f"  ✓ {ac['label']}  (cycle — approved in config)")
    for av in approved_violations:
        print(f"  ✓ {av['source']} → {av['target']}  (violation — approved in config)")


def print_high_coupling(modules: list, high_coupling_percentile: int) -> None:
    print(f"\nHigh Coupling Modules  (above {high_coupling_percentile}th percentile):")
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

    for section, items, fmt in [
        ("New Cycles",            comparison["new_cycles"],          lambda c: " → ".join(c)),
        ("Resolved Cycles",       comparison["resolved_cycles"],     lambda c: " → ".join(c)),
        ("New Violations",        comparison["new_violations"],      lambda v: f"{v['source']} → {v['target']}"),
        ("Resolved Violations",   comparison["resolved_violations"], lambda v: f"{v['source']} → {v['target']}"),
        ("New Duplicate Pairs",   comparison["new_duplicates"],      lambda d: f"{d['module_a']} ↔ {d['module_b']}"),
    ]:
        if items:
            printed = True
            prefix = "✓" if "Resolved" in section else "+"
            print(f"  {section}:")
            for item in items:
                print(f"    {prefix} {fmt(item)}")

    if comparison.get("complexity_increases"):
        printed = True
        print("  Complexity Increases:")
        for mod, delta in sorted(comparison["complexity_increases"].items(), key=lambda x: -x[1])[:5]:
            print(f"    + {mod}  (+{delta})")

    if not printed:
        print("  ✓ No structural changes detected")


def print_index_breakdown(components: dict, size_scale: float) -> None:
    print(f"\nDrift Index Breakdown:  (size scale: {size_scale}×)")
    cp = components.get("circular_penalty", {})
    kp = components.get("coupling_penalty", {})
    dp = components.get("duplication_penalty", {})
    xp = components.get("complexity_penalty", {})
    print(f"  Circular deps   : −{cp.get('value',0)}  ({cp.get('count',0)} cycles)")
    print(f"  High coupling   : −{kp.get('value',0)}  (avg {kp.get('avg_percentile',0)}th pct)")
    print(f"  Duplication     : −{dp.get('value',0)}  ({dp.get('count',0)} pairs)")
    suppressed = " (suppressed — first run)" if xp.get("suppressed") else ""
    print(f"  Complexity Δ    : −{xp.get('value',0)}  (+{xp.get('total_positive_delta',0)} total){suppressed}")
