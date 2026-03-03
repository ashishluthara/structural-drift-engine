"""
drift_index.py — Transparent Drift Index  (Phase 3)

Phase 3 additions:
    - Weighted moving average: 0.7 × current + 0.3 × 5-run MA
    - Per-run drop cap: max −10 points vs previous run
    - Size normalization: penalties scaled by log(modules+1) / log(30+1)
      to avoid punishing small repos disproportionately

Phase 2 formula (now used for the "raw structural score"):
    circular_penalty    = min(40, 15 × n_cycles)
    coupling_penalty    = min(20, (n_flagged/total) × (avg_pct/100) × 40)
    duplication_penalty = min(25, n_pairs × 5)
    complexity_penalty  = min(15, min(delta/total, 0.3) × 20)

Phase 3 pipeline:
    raw_score       = 100 − sum(size_normalized_penalties)
    smoothed_score  = 0.7 × raw_score + 0.3 × moving_average (if history exists)
    final_score     = max(prev_score − 10, smoothed_score)   (drop cap)

Severity levels:
    80–100  Stable
    60–79   Mild Structural Risk
    40–59   Moderate Risk
    <40     High Risk
"""

import math

SEVERITY_LEVELS = [
    (80, "Stable"),
    (60, "Mild Structural Risk"),
    (40, "Moderate Risk"),
    (0,  "High Risk"),
]

# Smoothing: weight current vs historical MA
WEIGHT_CURRENT  = 0.7
WEIGHT_HISTORY  = 0.3

# Cap on max drop in a single run
MAX_SINGLE_RUN_DROP = 10

# Size normalization: repos with 30+ modules get full penalties
SIZE_NORM_BASELINE = 30


def get_severity(drift_index: int) -> str:
    for threshold, label in SEVERITY_LEVELS:
        if drift_index >= threshold:
            return label
    return "High Risk"


def _size_scale(total_modules: int) -> float:
    """
    Scale factor in (0, 1] that reduces penalties for small repos.

    scale = log(n+1) / log(SIZE_NORM_BASELINE+1)
    Clamped to [0.2, 1.0] so tiny repos still get some signal.
    """
    if total_modules <= 0:
        return 0.2
    scale = math.log(total_modules + 1) / math.log(SIZE_NORM_BASELINE + 1)
    return max(0.2, min(1.0, scale))


def calculate_drift_index(
    cycles: list[list[str]],
    high_coupling_modules: list[dict],
    duplicate_pairs: list[dict],
    total_positive_complexity_delta: int,
    total_complexity: int = 0,
    total_modules: int = 0,
    first_run: bool = False,
    moving_average: float | None = None,
    previous_index: int | None = None,
) -> dict:
    """
    Calculate the Drift Index with smoothing, drop-capping, and size normalization.

    Args:
        cycles: Penalised (non-approved) cycles only.
        high_coupling_modules: Modules above percentile threshold.
        duplicate_pairs: Detected duplicate pairs.
        total_positive_complexity_delta: Sum of complexity increases this run.
        total_complexity: Total complexity (normalisation base).
        total_modules: Total module count.
        first_run: If True, complexity penalty is suppressed.
        moving_average: 5-run MA of drift index (None if no history).
        previous_index: Last run's drift index (for drop capping).

    Returns:
        Dict with drift_index, severity, first_run, raw_score,
        smoothing_applied, drop_capped, and components.
    """
    n_cycles     = len(cycles)
    n_duplicates = len(duplicate_pairs)
    scale        = _size_scale(total_modules)

    # ── Circular dependency penalty ───────────────────────────────────────────
    raw_circular   = min(40, 15 * n_cycles)
    circular_penalty = round(raw_circular * scale)

    # ── Coupling penalty (graduated by prevalence) ────────────────────────────
    total_mod_count = max(1, total_modules)
    if high_coupling_modules:
        percentiles    = [m.get("percentile", 85) for m in high_coupling_modules]
        avg_percentile = sum(percentiles) / len(percentiles)
        n_flagged      = len(high_coupling_modules)
        raw_coupling   = (n_flagged / total_mod_count) * (avg_percentile / 100) * 40
        coupling_penalty = round(min(20, raw_coupling) * scale)
    else:
        avg_percentile   = 0.0
        coupling_penalty = 0

    # ── Duplication penalty ───────────────────────────────────────────────────
    raw_dup           = min(25, n_duplicates * 5)
    duplication_penalty = round(raw_dup * scale)

    # ── Complexity delta penalty (suppressed on first run) ────────────────────
    if first_run:
        _ratio             = 0.0
        complexity_penalty = 0
    else:
        _total             = max(1, total_complexity)
        _ratio             = min(total_positive_complexity_delta / _total, 0.30)
        raw_cx             = min(15, round(_ratio * 20))
        complexity_penalty = round(raw_cx * scale)

    total_penalty = (
        circular_penalty
        + coupling_penalty
        + duplication_penalty
        + complexity_penalty
    )

    raw_score = max(0, 100 - total_penalty)

    # ── Weighted moving average smoothing ─────────────────────────────────────
    smoothing_applied = False
    smoothed_score    = raw_score

    if moving_average is not None and not first_run:
        smoothed_score    = round(WEIGHT_CURRENT * raw_score + WEIGHT_HISTORY * moving_average)
        smoothing_applied = True

    # ── Per-run drop cap ──────────────────────────────────────────────────────
    drop_capped         = False
    drop_cap_msg        = None
    final_score         = smoothed_score

    if previous_index is not None and not first_run:
        max_allowed = previous_index - MAX_SINGLE_RUN_DROP
        if smoothed_score < max_allowed:
            final_score  = max_allowed
            drop_capped  = True
            drop_cap_msg = (
                f"Score drop capped at {MAX_SINGLE_RUN_DROP}. "
                f"Raw: {raw_score}, Smoothed: {smoothed_score}, Capped to: {final_score}."
            )

    drift_index = max(0, min(100, final_score))
    severity    = get_severity(drift_index)

    return {
        "drift_index":        drift_index,
        "raw_score":          raw_score,
        "severity":           severity,
        "first_run":          first_run,
        "smoothing_applied":  smoothing_applied,
        "drop_capped":        drop_capped,
        "drop_cap_message":   drop_cap_msg,
        "size_scale":         round(scale, 3),
        "components": {
            "circular_penalty": {
                "value":    circular_penalty,
                "raw":      raw_circular,
                "count":    n_cycles,
                "weight":   "15 per cycle, cap 40, size-scaled",
            },
            "coupling_penalty": {
                "value":          coupling_penalty,
                "count":          len(high_coupling_modules),
                "avg_percentile": round(avg_percentile, 1),
                "weight":         "(n_flagged/total) x (avg_pct/100) x 40, cap 20, size-scaled",
            },
            "duplication_penalty": {
                "value":  duplication_penalty,
                "raw":    raw_dup,
                "count":  n_duplicates,
                "weight": "5 per pair, cap 25, size-scaled",
            },
            "complexity_penalty": {
                "value":                complexity_penalty,
                "total_positive_delta": total_positive_complexity_delta,
                "total_complexity":     total_complexity,
                "ratio":                round(_ratio, 4),
                "suppressed":           first_run,
                "weight":               "min(delta/total, 0.3) x 20, cap 15, size-scaled",
            },
        },
    }
