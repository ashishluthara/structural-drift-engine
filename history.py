"""
history.py — Multi-Run Time Series Persistence

Replaces snapshot.py as the primary persistence layer.

Stores the last 50 runs in .drift_history.json.
Computes:
    - 7-run moving average of drift index
    - Drift slope (linear regression over last 5 runs)
    - Volatility (standard deviation of last 5 runs)
    - % change metrics vs moving average

Also writes .drift_baseline.json for backward compatibility
with tools that read only the baseline.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path

HISTORY_FILE    = ".drift_history.json"
BASELINE_FILE   = ".drift_baseline.json"   # kept for backward compat
MAX_HISTORY     = 50
VOLATILITY_WARN = 5.0   # std dev above which we warn


# ── Data models ───────────────────────────────────────────────────────────────

def _run_record(
    drift_index: int,
    cycles: list,
    coupling_metrics: dict,
    duplicate_pairs: list,
    complexity_summary: dict,
    drift_components: dict,
    violations: list,
) -> dict:
    """Build a compact run record for time-series storage."""
    hc = coupling_metrics.get("high_coupling_modules", [])
    avg_coupling = (
        sum(m["dependency_count"] for m in hc) / len(hc)
        if hc else coupling_metrics.get("avg_dependencies", 0.0)
    )
    return {
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "drift_index":      drift_index,
        "circular_count":   len(cycles),
        "violation_count":  len(violations),
        "coupling_avg":     round(avg_coupling, 3),
        "duplication_count": len(duplicate_pairs),
        "total_complexity": complexity_summary.get("total_complexity", 0),
        "components": {
            "circular_penalty":     drift_components.get("circular_penalty", {}).get("value", 0),
            "coupling_penalty":     drift_components.get("coupling_penalty", {}).get("value", 0),
            "duplication_penalty":  drift_components.get("duplication_penalty", {}).get("value", 0),
            "complexity_penalty":   drift_components.get("complexity_penalty", {}).get("value", 0),
        },
    }


# ── Storage ───────────────────────────────────────────────────────────────────

def load_history(root_path: str) -> dict:
    """
    Load existing run history from .drift_history.json.

    Falls back gracefully if file is absent or malformed.

    Returns:
        Dict with key 'runs': list of run records (oldest first).
    """
    path = Path(root_path) / HISTORY_FILE
    if not path.exists():
        return {"runs": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "runs" in raw:
            return raw
    except (json.JSONDecodeError, OSError):
        pass
    return {"runs": []}


def append_run(root_path: str, run_record: dict) -> dict:
    """
    Append a new run to history, capping at MAX_HISTORY entries.

    Args:
        root_path: Repository root.
        run_record: Record from _run_record().

    Returns:
        Updated history dict (not yet written to disk).
    """
    history = load_history(root_path)
    history["runs"].append(run_record)
    if len(history["runs"]) > MAX_HISTORY:
        history["runs"] = history["runs"][-MAX_HISTORY:]
    return history


def save_history(root_path: str, history: dict, full_snapshot: dict) -> str:
    """
    Persist history to .drift_history.json.
    Also writes .drift_baseline.json (last run) for backward compat.

    Returns:
        Path to the history file.
    """
    hist_path = Path(root_path) / HISTORY_FILE
    hist_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

    # Backward compat — snapshot consumers just need the latest run
    base_path = Path(root_path) / BASELINE_FILE
    base_path.write_text(json.dumps(full_snapshot, indent=2), encoding="utf-8")

    return str(hist_path)


def load_baseline(root_path: str) -> dict | None:
    """Load the last saved full snapshot from .drift_baseline.json."""
    path = Path(root_path) / BASELINE_FILE
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def build_full_snapshot(
    drift_index_result: dict,
    graph: dict,
    coupling_metrics: dict,
    cycles: list,
    violations: list,
    duplicate_pairs: list,
    complexity_summary: dict,
    complexity_map: dict,
    config_dict: dict,
) -> dict:
    """Build the full snapshot dict written to .drift_baseline.json."""
    return {
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "drift_index":   drift_index_result["drift_index"],
        "severity":      drift_index_result.get("severity", ""),
        "graph":         graph,
        "coupling_metrics": coupling_metrics,
        "cycles":        cycles,
        "violations":    violations,
        "duplicate_pairs": duplicate_pairs,
        "complexity_map": complexity_map,
        "complexity_summary": complexity_summary,
        "drift_index_components": drift_index_result["components"],
        "config":        config_dict,
    }


# ── Analytics ─────────────────────────────────────────────────────────────────

def compute_trend_analytics(history: dict, window_ma: int = 7, window_slope: int = 5) -> dict:
    """
    Compute trend metrics from run history.

    Args:
        history: History dict with 'runs' list.
        window_ma: Moving average window (default 7).
        window_slope: Slope + volatility window (default 5).

    Returns:
        Dict with keys:
            run_count         — total runs in history
            moving_avg        — 7-run MA of drift index (None if < 2 runs)
            slope             — per-run drift change over last 5 runs
            volatility        — std dev of last 5 drift index values
            high_volatility   — bool: volatility > VOLATILITY_WARN
            trend_label       — human-readable trend description
            recent_indices    — list of recent drift_index values (oldest first)
            trend_string      — formatted "85 → 84 → 82" string
    """
    runs = history.get("runs", [])
    n    = len(runs)

    if n == 0:
        return {
            "run_count": 0, "moving_avg": None, "slope": 0.0,
            "volatility": 0.0, "high_volatility": False,
            "trend_label": "No history", "recent_indices": [],
            "trend_string": "", "pct_changes": {},
        }

    indices = [r["drift_index"] for r in runs]

    # 7-run moving average
    ma_window = indices[-window_ma:]
    moving_avg = round(sum(ma_window) / len(ma_window), 1) if len(ma_window) >= 2 else None

    # Slope: simple linear regression over last 5 runs
    slope_data = indices[-window_slope:]
    slope = _linear_slope(slope_data) if len(slope_data) >= 2 else 0.0

    # Volatility: std dev of last 5
    vol_data = indices[-window_slope:]
    volatility = _std_dev(vol_data) if len(vol_data) >= 2 else 0.0

    # Trend label
    total_change = indices[-1] - indices[max(0, n - window_slope)]
    trend_label = _trend_label(slope, total_change, n, window_slope)

    # Recent indices for display (last 7)
    recent = indices[-7:]
    trend_string = " → ".join(str(i) for i in recent)

    # % change analytics (vs moving average for coupling, complexity, duplication)
    pct_changes = _compute_pct_changes(runs, window_ma)

    return {
        "run_count":      n,
        "moving_avg":     moving_avg,
        "slope":          round(slope, 3),
        "volatility":     round(volatility, 2),
        "high_volatility": volatility > VOLATILITY_WARN,
        "trend_label":    trend_label,
        "recent_indices": recent,
        "trend_string":   trend_string,
        "pct_changes":    pct_changes,
        "total_change_5": total_change,
    }


def compare_with_baseline(baseline: dict | None, current: dict) -> dict | None:
    """
    Compare current run against the saved baseline snapshot.

    Identical to the old snapshot.compare_snapshots() interface
    so callers don't need to change.
    """
    if baseline is None:
        return None

    baseline_index = baseline.get("drift_index", 100)
    current_index  = current.get("drift_index", 100)
    index_delta    = current_index - baseline_index

    def _cycle_key(c):   return frozenset(zip(c, c[1:]))
    def _viol_key(v):    return (v.get("source"), v.get("target"))
    def _dup_key(d):     return frozenset([d.get("module_a"), d.get("module_b")])

    baseline_cycles = {_cycle_key(c) for c in baseline.get("cycles", [])}
    current_cycles  = {_cycle_key(c) for c in current.get("cycles", [])}
    new_cycles      = [c for c in current.get("cycles", []) if _cycle_key(c) not in baseline_cycles]
    resolved_cycles = [c for c in baseline.get("cycles", []) if _cycle_key(c) not in current_cycles]

    baseline_viols = {_viol_key(v) for v in baseline.get("violations", [])}
    current_viol_list = current.get("violations", [])
    new_violations     = [v for v in current_viol_list if _viol_key(v) not in baseline_viols]
    resolved_violations = [
        v for v in baseline.get("violations", [])
        if _viol_key(v) not in {_viol_key(v2) for v2 in current_viol_list}
    ]

    baseline_hc = {m["module"] for m in baseline.get("coupling_metrics", {}).get("high_coupling_modules", [])}
    current_hc  = {m["module"] for m in current.get("coupling_metrics", {}).get("high_coupling_modules", [])}
    newly_high_coupling = sorted(current_hc - baseline_hc)

    baseline_dups = {_dup_key(d) for d in baseline.get("duplicate_pairs", [])}
    current_dup_list = current.get("duplicate_pairs", [])
    new_duplicates    = [d for d in current_dup_list if _dup_key(d) not in baseline_dups]

    baseline_cx = baseline.get("complexity_map", {})
    current_cx  = current.get("complexity_map", {})
    complexity_increases = {
        mod: current_cx[mod] - baseline_cx.get(mod, 0)
        for mod in current_cx
        if current_cx[mod] > baseline_cx.get(mod, 0)
    }

    return {
        "index_delta":           index_delta,
        "new_cycles":            new_cycles,
        "resolved_cycles":       resolved_cycles,
        "new_violations":        new_violations,
        "resolved_violations":   resolved_violations,
        "newly_high_coupling":   newly_high_coupling,
        "new_duplicates":        new_duplicates,
        "complexity_increases":  complexity_increases,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _linear_slope(values: list[float]) -> float:
    """
    Compute slope of a simple linear regression y ~ a + b*x.
    Returns b (change per step).
    """
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(values) / n
    num = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    den = sum((i - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0


def _std_dev(values: list[float]) -> float:
    """Population standard deviation."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / n)


def _trend_label(slope: float, total_change: int, n_runs: int, window: int) -> str:
    """Convert slope + total change into a human-readable label."""
    if n_runs < 2:
        return "Insufficient history"
    if abs(slope) < 0.3:
        return "Stable"
    if slope > 2.0:
        return "Rapid improvement"
    if slope > 0.3:
        return f"Gradual improvement (+{abs(total_change)} over {min(n_runs, window)} runs)"
    if slope < -2.0:
        return f"Rapid degradation ({total_change} over {min(n_runs, window)} runs)"
    return f"Gradual degradation ({total_change} over {min(n_runs, window)} runs)"


def _compute_pct_changes(runs: list, window: int) -> dict:
    """
    Compute % change for key metrics vs their moving average.
    Caps at ±200% to prevent distortion from near-zero baselines.
    """
    if len(runs) < 2:
        return {}

    window_runs = runs[-window:] if len(runs) >= window else runs[:-1]
    current = runs[-1]

    def avg_field(field):
        vals = [r.get(field, 0) for r in window_runs]
        return sum(vals) / len(vals) if vals else 0

    def pct_change(current_val, avg_val):
        if avg_val == 0:
            return None   # undefined
        raw = ((current_val - avg_val) / avg_val) * 100
        return max(-200, min(200, round(raw, 1)))

    avg_coupling    = avg_field("coupling_avg")
    avg_complexity  = avg_field("total_complexity")
    avg_duplication = avg_field("duplication_count")

    result = {}
    c = pct_change(current.get("coupling_avg", 0), avg_coupling)
    x = pct_change(current.get("total_complexity", 0), avg_complexity)
    d = pct_change(current.get("duplication_count", 0), avg_duplication)
    if c is not None:  result["coupling_pct"]    = c
    if x is not None:  result["complexity_pct"]  = x
    if d is not None:  result["duplication_pct"] = d

    return result
