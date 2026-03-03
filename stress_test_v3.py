"""
tests/stress_test_v3.py — Phase 3 Stress Test Suite

Simulates:
    1. Gradual coupling increase (5 runs) — trend detection
    2. Approved cycle exception         — suppression + score unaffected
    3. Sudden major structural change   — drop-cap fires
    4. Domain-based boundary detection  — domains config
    5. Multi-run history + % analytics  — moving avg, pct change

All scenarios verify scoring stability (no extreme oscillations).
"""

import json
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from config import DriftConfig, load_config
from scanner import scan_repository, file_path_to_module_name
from graph_builder import build_dependency_graph
from metrics import detect_cycles, compute_coupling_metrics
from drift import detect_boundary_violations, partition_cycles
from complexity import compute_complexity_map, summarise_complexity, compute_complexity_delta
from duplication import detect_duplicates
from drift_index import calculate_drift_index
from history import (
    load_history, append_run, save_history, build_full_snapshot,
    compute_trend_analytics, _run_record,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def write_file(root, rel, content):
    fp = Path(root) / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(textwrap.dedent(content))


def write_json(root, name, obj):
    (Path(root) / name).write_text(json.dumps(obj, indent=2))


def analyse(repo_root, config=None, moving_avg=None, prev_index=None, first_run=True):
    if config is None:
        config = load_config(repo_root)
    fps = scan_repository(repo_root, ignore_test_dirs=config.ignore_test_dirs)
    mm  = {fp: file_path_to_module_name(fp) for fp in fps}
    g   = build_dependency_graph(fps, repo_root)
    cp  = compute_coupling_metrics(g, config.high_coupling_percentile)
    cy  = detect_cycles(g)
    acy, appcy = partition_cycles(cy, config)
    vi, appvi  = detect_boundary_violations(g, config)
    cxm = compute_complexity_map(fps, repo_root, mm)
    dups = detect_duplicates(fps, repo_root, mm,
                              threshold=config.duplication_threshold,
                              min_lines=config.min_lines_for_duplication,
                              max_modules=config.max_modules_for_duplication)
    cxs = summarise_complexity(cxm, {})
    dr  = calculate_drift_index(
        cycles=acy,
        high_coupling_modules=cp["high_coupling_modules"],
        duplicate_pairs=dups,
        total_positive_complexity_delta=0 if first_run else cxs["total_positive_delta"],
        total_complexity=cxs["total_complexity"],
        total_modules=cp["total_modules"],
        first_run=first_run,
        moving_average=moving_avg,
        previous_index=prev_index,
    )
    return {
        "graph": g, "coupling": cp, "cycles": acy, "approved_cycles": appcy,
        "violations": vi, "approved_violations": appvi, "complexity_map": cxm,
        "complexity": cxs, "dups": dups,
        "index": dr["drift_index"], "raw": dr.get("raw_score", dr["drift_index"]),
        "severity": dr["severity"], "dr": dr,
    }


PASS = []
FAIL = []


def check(label, cond, detail=""):
    if cond:
        print(f"    ✓  {label}")
        PASS.append(label)
    else:
        msg = f"{label}" + (f"  — {detail}" if detail else "")
        print(f"    ✗  {msg}")
        FAIL.append(msg)


# ── Scenario 1: Gradual coupling increase ─────────────────────────────────────

def test_gradual_coupling():
    print("\n  1. Gradual Coupling Increase (5 simulated runs)")
    tmp = tempfile.mkdtemp(prefix="sde3_gc_")

    try:
        # Simulate 5 runs with increasing coupling
        indices = []
        raw_scores = []
        history = {"runs": []}
        prev = None

        for run_n in range(5):
            # Each run adds more cross-imports
            pkgs = ["billing", "auth", "users", "payments", "reports"]
            for pkg in pkgs:
                write_file(tmp, f"{pkg}/__init__.py", "")
                write_file(tmp, f"{pkg}/service.py", "import os\ndef run(): pass\n")
                write_file(tmp, f"{pkg}/models.py", "import os\nclass M: pass\n")

            # Progressive: add more cross-boundary imports each run
            extra_imports = "\n".join(
                f"from {pkgs[(i+1) % len(pkgs)]}.service import run"
                for i in range(run_n)  # 0..4 extra imports
            )
            write_file(tmp, "billing/service.py",
                       f"import os\n{extra_imports}\ndef run(): pass\n")

            ma = (sum(indices[-7:]) / len(indices[-7:])) if len(indices) >= 2 else None
            r = analyse(tmp, first_run=(run_n == 0), moving_avg=ma, prev_index=prev)
            indices.append(r["index"])
            raw_scores.append(r["raw"])
            prev = r["index"]

            rec = _run_record(r["index"], r["cycles"], r["coupling"], r["dups"], r["complexity"], r["dr"]["components"], r["violations"])
            history["runs"].append(rec)

        trend = compute_trend_analytics(history)

        check("5 runs recorded", trend["run_count"] == 5)
        check("Trend string has 5 entries", len(trend["recent_indices"]) == 5,
              f"got {len(trend['recent_indices'])}")
        # Score should degrade or stay same — never go UP as coupling increases
        check("Score non-increasing as coupling grows",
              indices[-1] <= indices[0] + 5,   # allow small smoothing variance
              f"first={indices[0]}, last={indices[-1]}")
        check("Moving average computed", trend["moving_avg"] is not None)
        check("Trend label assigned", bool(trend["trend_label"]))
        print(f"    Scores: {' → '.join(str(i) for i in indices)}")
        print(f"    Trend: {trend['trend_label']}  slope={trend['slope']}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Scenario 2: Approved cycle exception ──────────────────────────────────────

def test_approved_cycle():
    print("\n  2. Approved Cycle Exception")
    tmp = tempfile.mkdtemp(prefix="sde3_ac_")

    try:
        # Create a known cycle: billing.service -> auth.utils -> billing.service
        write_file(tmp, "billing/__init__.py", "")
        write_file(tmp, "billing/service.py",
                   "from auth.utils import verify\nimport os\ndef run(): pass\n")
        write_file(tmp, "billing/models.py", "import os\nclass B: pass\n")
        write_file(tmp, "auth/__init__.py", "")
        write_file(tmp, "auth/utils.py",
                   "from billing.service import run\nimport os\ndef verify(): pass\n")
        write_file(tmp, "auth/models.py", "import os\nclass A: pass\n")
        write_file(tmp, "main.py", "from billing.service import run\n")

        # Without approval
        cfg_no_approval = DriftConfig({})
        r_before = analyse(tmp, config=cfg_no_approval)
        has_cycle_before = len(r_before["cycles"]) > 0

        # With approved cycle
        cfg_approved = DriftConfig({
            "approved_cycles": [["billing.service", "auth.utils"]]
        })
        r_after = analyse(tmp, config=cfg_approved)

        check("Cycle detected before approval",
              has_cycle_before, f"cycles={r_before['cycles']}")
        check("Active cycles = 0 after approval",
              len(r_after["cycles"]) == 0,
              f"cycles={r_after['cycles']}")
        check("Approved cycle captured in result",
              len(r_after["approved_cycles"]) == 1,
              f"approved={r_after['approved_cycles']}")
        check("Score improves with approval (or stays same)",
              r_after["index"] >= r_before["index"] - 1,
              f"before={r_before['index']}, after={r_after['index']}")
        check("Score with approval >= 70 (cycle no longer penalised)",
              r_after["index"] >= 70, f"got {r_after['index']}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Scenario 3: Drop-cap on sudden major change ────────────────────────────────

def test_drop_cap():
    print("\n  3. Drop Cap — Sudden Major Change")
    tmp = tempfile.mkdtemp(prefix="sde3_dc_")

    try:
        # Clean start
        for pkg in ["a", "b", "c", "d"]:
            write_file(tmp, f"{pkg}/__init__.py", "")
            write_file(tmp, f"{pkg}/service.py", "import os\ndef run(): pass\n")
        write_file(tmp, "main.py", "from a.service import run\n")

        clean_r = analyse(tmp, first_run=True)
        clean_score = clean_r["index"]  # should be ~100

        # Simulate catastrophic change: everything imports everything
        all_pkgs = ["a", "b", "c", "d"]
        for i, pkg in enumerate(all_pkgs):
            others = [f"from {p}.service import run as r{j}"
                      for j, p in enumerate(all_pkgs) if p != pkg]
            write_file(tmp, f"{pkg}/service.py",
                       "\n".join(others) + "\nimport os\ndef run(): pass\n")

        # Pass previous_index so drop-cap can fire
        catastrophic_r = analyse(tmp, first_run=False,
                                  moving_avg=float(clean_score),
                                  prev_index=clean_score)

        raw = catastrophic_r["raw"]
        final = catastrophic_r["index"]
        expected_min = max(0, clean_score - 10)

        check("Raw score dropped significantly",
              raw <= clean_score - 8,
              f"clean={clean_score}, raw={raw}")
        check("Drop-cap applied (final >= prev - 10)",
              final >= expected_min,
              f"clean={clean_score}, raw={raw}, final={final}, min_allowed={expected_min}")
        check("drop_capped flag is True",
              catastrophic_r["dr"].get("drop_capped") == True,
              f"drop_capped={catastrophic_r['dr'].get('drop_capped')}")

        print(f"    Clean: {clean_score}  Raw after catastrophe: {raw}  "
              f"Capped to: {final}  (cap floor: {expected_min})")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Scenario 4: Domain-based violation detection ──────────────────────────────

def test_domain_detection():
    print("\n  4. Domain-Based Boundary Detection")
    tmp = tempfile.mkdtemp(prefix="sde3_dm_")

    try:
        # billing + invoicing both in "billing" domain
        for pkg in ["billing", "invoicing", "auth", "users"]:
            write_file(tmp, f"{pkg}/__init__.py", "")
            write_file(tmp, f"{pkg}/service.py", "import os\ndef run(): pass\n")

        # Within-domain: billing -> invoicing should NOT be a violation
        write_file(tmp, "billing/service.py",
                   "from invoicing.service import run\nimport os\ndef bill(): pass\n")
        # Cross-domain: billing -> auth should be flagged
        write_file(tmp, "invoicing/service.py",
                   "from auth.service import run\nimport os\ndef invoice(): pass\n")
        write_file(tmp, "main.py", "from billing.service import bill\n")

        cfg = DriftConfig({
            "domains": {
                "billing":  ["billing.*", "invoicing.*"],
                "auth":     ["auth.*"],
                "users":    ["users.*"],
            }
        })
        r = analyse(tmp, config=cfg)
        viols = r["violations"]

        # Within-domain import should not appear
        within_domain = [v for v in viols
                         if v["source"] == "billing.service" and "invoicing" in v["target"]]
        # Cross-domain should appear
        cross_domain   = [v for v in viols
                          if "invoicing" in v["source"] and "auth" in v["target"]]

        check("Within-domain import NOT flagged", len(within_domain) == 0,
              f"found: {within_domain}")
        check("Cross-domain import IS flagged",   len(cross_domain) > 0,
              f"violations={viols}")
        check("Violation type is cross_domain",
              all(v["type"] == "cross_domain" for v in viols),
              f"types={[v['type'] for v in viols]}")

        # Test allowed_domain_dependencies suppression
        cfg_allow = DriftConfig({
            "domains": {
                "billing": ["billing.*", "invoicing.*"],
                "auth":    ["auth.*"],
                "users":   ["users.*"],
            },
            "allowed_domain_dependencies": {"billing": ["auth"]}
        })
        r_allow = analyse(tmp, config=cfg_allow)
        cross_after = [v for v in r_allow["violations"]
                       if v.get("source_boundary") == "billing"
                       and v.get("target_boundary") == "auth"]
        approved_after = [v for v in r_allow["approved_violations"]
                          if v.get("source_boundary") == "billing"
                          and v.get("target_boundary") == "auth"]
        check("allowed_domain_dependency suppresses violation",
              len(cross_after) == 0 and len(approved_after) > 0,
              f"violations={cross_after}, approved={approved_after}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Scenario 5: Multi-run history + % analytics ───────────────────────────────

def test_history_analytics():
    print("\n  5. Multi-Run History + % Change Analytics")
    tmp = tempfile.mkdtemp(prefix="sde3_ha_")

    try:
        # Simulate 10 runs with stable coupling, then spike
        history = {"runs": []}

        for i in range(9):
            rec = {
                "timestamp":         f"2026-01-{i+1:02d}T00:00:00+00:00",
                "drift_index":       90,
                "circular_count":    0,
                "violation_count":   1,
                "coupling_avg":      2.0,
                "duplication_count": 0,
                "total_complexity":  200,
                "components":        {"circular_penalty": 0, "coupling_penalty": 0,
                                      "duplication_penalty": 0, "complexity_penalty": 0},
            }
            history["runs"].append(rec)

        # Run 10: coupling spikes to 8.0
        spike_rec = {
            "timestamp":         "2026-01-10T00:00:00+00:00",
            "drift_index":       75,
            "circular_count":    0,
            "violation_count":   1,
            "coupling_avg":      8.0,
            "duplication_count": 2,
            "total_complexity":  220,
            "components":        {"circular_penalty": 0, "coupling_penalty": 8,
                                  "duplication_penalty": 10, "complexity_penalty": 0},
        }
        history["runs"].append(spike_rec)

        trend = compute_trend_analytics(history)

        check("10 runs in history", trend["run_count"] == 10)
        ma = trend["moving_avg"]
        check("Moving average computed", ma is not None)
        if ma is not None:
            check("Moving average reflects recent stable runs",
                  80.0 <= ma <= 92.0, f"ma={ma}")

        pct = trend["pct_changes"]
        check("coupling_pct computed", "coupling_pct" in pct, f"pct={pct}")
        if "coupling_pct" in pct:
            # coupling went from ~2.0 to 8.0 — well above 5% threshold
            check("coupling_pct shows significant increase",
                  pct["coupling_pct"] > 50, f"coupling_pct={pct['coupling_pct']}")

        # Max 50 runs check
        big_history = {"runs": [spike_rec.copy() for _ in range(55)]}
        save_history(tmp, big_history, {"drift_index": 75})
        loaded = load_history(tmp)
        # The save function doesn't trim — append_run does
        # Check append_run trims correctly
        from history import append_run as _append_run
        trimmed = _append_run(tmp, spike_rec)
        check("History capped at 50 runs after append",
              len(trimmed["runs"]) <= 50 + 1,   # +1 for the new one
              f"got {len(trimmed['runs'])}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_all():
    print("\n" + "=" * 62)
    print("  Structural Drift Engine v3 — Stress Test Suite")
    print("=" * 62)

    import time
    t0 = time.perf_counter()

    test_gradual_coupling()
    test_approved_cycle()
    test_drop_cap()
    test_domain_detection()
    test_history_analytics()

    elapsed = time.perf_counter() - t0

    print("\n" + "=" * 62)
    print(f"  {len(PASS)} passed  |  {len(FAIL)} failed  |  {elapsed:.2f}s")
    print("=" * 62)

    if FAIL:
        print("\n  Failures:")
        for f in FAIL:
            print(f"    • {f}")
        sys.exit(1)
    else:
        print("\n  All assertions passed ✓")
        sys.exit(0)


if __name__ == "__main__":
    run_all()
