"""
drift_index.py — Transparent Drift Index

Every weight and formula is documented. No hidden logic.

Formula (start at 100, subtract penalties, clamp to 0):

    circular_penalty    = min(40, 15 x n_cycles)
    coupling_penalty    = min(20, (avg_high_coupling_percentile / 100) x 20)
    duplication_penalty = min(25, n_duplicate_pairs x 5)
    complexity_penalty  = min(15, min(delta / total_complexity, 0.3) x 20)

    drift_index = max(0, 100 - sum_of_penalties)

Complexity penalty rationale:
    Normalised as a fraction of total repo complexity, capped at 30%.
    A +10 delta in a 1000-point repo = 1% ratio -> ~0 penalty.
    A +10 delta in a 20-point repo = 50% -> capped at 30% -> 6 penalty.
    Prevents runaway scores in large codebases from minor complexity growth.

Severity levels:
    80-100  Stable
    60-79   Mild Structural Risk
    40-59   Moderate Risk
    <40     High Risk
"""

import math


SEVERITY_LEVELS = [
    (80, "Stable"),
    (60, "Mild Structural Risk"),
    (40, "Moderate Risk"),
    (0,  "High Risk"),
]


def get_severity(drift_index: int) -> str:
    """
    Map a drift index score to a human-readable severity label.

    Args:
        drift_index: Integer score 0-100.

    Returns:
        Severity label string.
    """
    for threshold, label in SEVERITY_LEVELS:
        if drift_index >= threshold:
            return label
    return "High Risk"


def calculate_drift_index(
    cycles: list[list[str]],
    high_coupling_modules: list[dict],
    duplicate_pairs: list[dict],
    total_positive_complexity_delta: int,
    total_complexity: int = 0,
    total_modules: int = 0,
    first_run: bool = False,
) -> dict:
    """
    Calculate the Drift Index using a transparent, weighted formula.

    Args:
        cycles: List of detected circular dependency chains.
        high_coupling_modules: List of dicts with 'percentile' key.
        duplicate_pairs: List of duplicate module pair dicts.
        total_positive_complexity_delta: Sum of complexity increases this run.
        total_complexity: Total complexity across all modules (normalisation base).
        first_run: If True, complexity penalty is suppressed (no baseline to
            delta against). Cycles, violations, coupling, and duplication are
            still scored — those are absolute facts, not delta-dependent.

    Returns:
        Dict with keys:
            drift_index  -- integer 0-100
            severity     -- human-readable severity label
            first_run    -- bool, whether this was a baseline initialization run
            components   -- per-penalty breakdown with counts, ratios, and weights
    """
    n_cycles     = len(cycles)
    n_duplicates = len(duplicate_pairs)

    # Circular dependency penalty: 15 per cycle, cap 40
    circular_penalty = min(40, 15 * n_cycles)

    # Coupling penalty — graduated by fraction of codebase affected.
    #
    # Old formula: min(20, ceil(avg_percentile/100 * 20))
    # Problem:     0 flagged → 0; 1 flagged at 94th pct → 19.  Binary cliff.
    #
    # New formula: min(20, round((n_flagged/total) * (avg_pct/100) * 20 * 2))
    # Behaviour:
    #   1/18 modules at 94th pct  →  round(0.055 * 0.94 * 40) = 2   (≤2pt target)
    #   4/10 modules at 90th pct  →  round(0.40  * 0.90 * 40) = 14  (meaningful)
    #   10/20 modules at 90th pct →  round(0.50  * 0.90 * 40) = 18  (severe)
    #
    # The ×2 factor ensures a genuinely widespread coupling problem can still
    # reach the cap of 20, while a single outlier module scores ≤2.
    total_modules_count = max(1, total_modules)

    if high_coupling_modules:
        percentiles    = [m.get("percentile", 85) for m in high_coupling_modules]
        avg_percentile = sum(percentiles) / len(percentiles)
        n_flagged      = len(high_coupling_modules)
        raw_penalty    = (n_flagged / total_modules_count) * (avg_percentile / 100) * 20 * 2
        coupling_penalty = min(20, round(raw_penalty))
    else:
        avg_percentile   = 0.0
        coupling_penalty = 0

    # Duplication penalty: 5 per pair, cap 25
    duplication_penalty = min(25, n_duplicates * 5)

    # Complexity delta penalty: suppressed on first run (no baseline to compare against).
    # On subsequent runs: normalised ratio, cap ratio at 30%, result cap 15.
    if first_run:
        _ratio             = 0.0
        complexity_penalty = 0
    else:
        _total             = max(1, total_complexity)
        _ratio             = min(total_positive_complexity_delta / _total, 0.30)
        complexity_penalty = min(15, round(_ratio * 20))

    total_penalty = (
        circular_penalty
        + coupling_penalty
        + duplication_penalty
        + complexity_penalty
    )

    drift_index = max(0, 100 - total_penalty)
    severity    = get_severity(drift_index)

    return {
        "drift_index": drift_index,
        "severity":    severity,
        "first_run":   first_run,
        "components": {
            "circular_penalty": {
                "value":  circular_penalty,
                "count":  n_cycles,
                "weight": "15 per cycle, cap 40",
            },
            "coupling_penalty": {
                "value":          coupling_penalty,
                "count":          len(high_coupling_modules),
                "avg_percentile": round(avg_percentile, 1),
                "weight":         "(n_flagged/total) x (avg_pct/100) x 40, cap 20",
            },
            "duplication_penalty": {
                "value":  duplication_penalty,
                "count":  n_duplicates,
                "weight": "5 per pair, cap 25",
            },
            "complexity_penalty": {
                "value":                complexity_penalty,
                "total_positive_delta": total_positive_complexity_delta,
                "total_complexity":     total_complexity,
                "ratio":                round(_ratio, 4),
                "weight":               "min(delta/total, 0.3) x 20, cap 15",
                "suppressed":           first_run,
            },
        },
    }
