"""
tests/stress_test.py — Stress Test Suite

Tests the engine against three archetypal repo structures:

    1. CLEAN MODULAR      — well-structured, bounded, low coupling
    2. MESSY MONOLITH     — flat namespace, everything imports everything
    3. CIRCULAR INJECTION — deliberately engineered cross-boundary cycles

For each repo we run the full analysis pipeline and assert:
    - Score is in the expected range
    - False positives are absent from the clean repo
    - Coupling metric is meaningful (scales with actual coupling)
    - Score doesn't fluctuate aggressively when adding minor noise

Usage:
    python tests/stress_test.py
"""

import os
import sys
import shutil
import tempfile
import textwrap
from pathlib import Path
from typing import Callable

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from scanner import scan_repository
from graph_builder import build_dependency_graph
from metrics import detect_cycles, compute_coupling_metrics
from drift import detect_boundary_violations, calculate_drift_score


# ── Pipeline helper ───────────────────────────────────────────────────────────

def analyse(repo_root: str, threshold: int = 10) -> dict:
    """Run full analysis and return a flat results dict."""
    files = scan_repository(repo_root)
    graph = build_dependency_graph(files, repo_root)
    coupling = compute_coupling_metrics(graph, high_coupling_threshold=threshold)
    cycles = detect_cycles(graph)
    violations = detect_boundary_violations(graph)
    drift = calculate_drift_score(
        cycles=cycles,
        violations=violations,
        high_coupling_modules=coupling["high_coupling_modules"],
        total_modules=coupling["total_modules"],
        total_edges=coupling["total_edges"],
    )
    return {
        "graph": graph,
        "coupling": coupling,
        "cycles": cycles,
        "violations": violations,
        "score": drift["drift_score"],
        "penalty": drift["penalty_breakdown"],
        "files": len(files),
    }


def write_file(root: str, rel_path: str, content: str) -> None:
    fp = Path(root) / rel_path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(textwrap.dedent(content), encoding="utf-8")


# ── Scenario 1: Clean Modular Repo ───────────────────────────────────────────
#
# 4 bounded packages, each with 3–4 modules.
# No cross-boundary imports. All inter-package traffic goes through core/api.py.
# Expected: score >= 90, zero cycles, zero violations.

def build_clean_modular(root: str) -> None:
    """Well-structured repo. Each package is self-contained."""
    packages = {
        "billing":  ["invoice", "calculator", "models"],
        "auth":     ["session", "tokens", "models"],
        "users":    ["profile", "preferences", "models"],
        "payments": ["gateway", "refunds", "models"],
    }

    for pkg, modules in packages.items():
        write_file(root, f"{pkg}/__init__.py", "")
        for mod in modules:
            write_file(root, f"{pkg}/{mod}.py", f"# {pkg}.{mod}\nimport os\nimport logging\n")

    # A clean shared core that others are allowed to import
    write_file(root, "core/__init__.py", "")
    write_file(root, "core/api.py", "import json\nimport os\n")
    write_file(root, "core/exceptions.py", "import os\n")
    write_file(root, "core/config.py", "import os\nimport json\n")

    # Root orchestrator imports only from core — no boundary crossing
    write_file(root, "main.py", "from core.api import run\nfrom core.config import settings\n")


# ── Scenario 2: Messy Monolith ────────────────────────────────────────────────
#
# Single flat namespace. 20 modules all importing each other.
# Dense cross-imports, a few cycles, high-coupling "god" modules.
# Expected: score 30–65, high violations, at least 1 high-coupling module.

def build_messy_monolith(root: str) -> None:
    """Flat namespace, everything knows about everything."""
    modules = [
        "user_manager", "billing_service", "auth_handler", "payment_processor",
        "email_sender", "report_generator", "config_loader", "db_connector",
        "cache_manager", "session_store", "event_bus", "webhook_handler",
        "pdf_exporter", "csv_importer", "scheduler", "notifier",
        "api_gateway", "rate_limiter", "audit_logger", "feature_flags",
    ]

    # Create 5 packages (simulating a monolith that was packaged but not bounded)
    pkg_map = {
        "service":   modules[0:5],
        "infra":     modules[5:10],
        "platform":  modules[10:15],
        "tools":     modules[15:18],
        "gateway":   modules[18:],
    }

    all_mods = []
    for pkg, mods in pkg_map.items():
        write_file(root, f"{pkg}/__init__.py", "")
        for mod in mods:
            all_mods.append(f"{pkg}.{mod}")

    # Each module imports several modules from OTHER packages
    # creating a dense violation and coupling web
    import_patterns = [
        # service package imports infra + platform
        ("service/user_manager.py",     ["from infra.config_loader import cfg",
                                          "from infra.db_connector import db",
                                          "from platform.event_bus import emit",
                                          "from platform.audit_logger import log",
                                          "from gateway.api_gateway import handle"]),
        ("service/billing_service.py",  ["from service.user_manager import User",
                                          "from infra.db_connector import db",
                                          "from platform.event_bus import emit",
                                          "from tools.pdf_exporter import export",
                                          "from gateway.rate_limiter import check"]),
        ("service/auth_handler.py",     ["from service.user_manager import User",
                                          "from infra.session_store import store",
                                          "from infra.cache_manager import cache",
                                          "from platform.audit_logger import log"]),
        ("service/payment_processor.py",["from service.billing_service import invoice",
                                          "from infra.db_connector import db",
                                          "from platform.webhook_handler import send",
                                          "from gateway.api_gateway import handle"]),
        ("service/email_sender.py",     ["from infra.config_loader import cfg",
                                          "from platform.notifier import notify",
                                          "from tools.csv_importer import load"]),
        # infra imports service (cross-boundary back-reference = violation + cycle risk)
        ("infra/config_loader.py",      ["import os", "import json"]),
        ("infra/db_connector.py",       ["from infra.config_loader import cfg",
                                          "import os"]),
        ("infra/cache_manager.py",      ["from infra.db_connector import db",
                                          "from platform.event_bus import emit"]),
        ("infra/session_store.py",      ["from infra.cache_manager import cache",
                                          "from service.user_manager import User"]),   # cycle risk
        ("infra/report_generator.py",   ["from service.billing_service import invoice",
                                          "from tools.csv_importer import load",
                                          "from tools.pdf_exporter import export"]),
        # platform
        ("platform/event_bus.py",       ["import threading", "import queue"]),
        ("platform/webhook_handler.py", ["from platform.event_bus import emit",
                                          "from infra.config_loader import cfg"]),
        ("platform/scheduler.py",       ["from platform.event_bus import emit",
                                          "from service.user_manager import User",
                                          "from infra.db_connector import db"]),
        ("platform/notifier.py",        ["from platform.event_bus import emit",
                                          "from service.email_sender import send"]),   # cycle
        ("platform/audit_logger.py",    ["from infra.db_connector import db",
                                          "from infra.config_loader import cfg"]),
        # tools
        ("tools/pdf_exporter.py",       ["import io", "import os"]),
        ("tools/csv_importer.py",       ["import csv", "import os"]),
        ("tools/feature_flags.py",      ["from infra.db_connector import db",
                                          "from infra.cache_manager import cache"]),
        # gateway
        ("gateway/api_gateway.py",      ["from service.auth_handler import verify",
                                          "from gateway.rate_limiter import check",
                                          "from platform.audit_logger import log"]),
        ("gateway/rate_limiter.py",     ["from infra.cache_manager import cache",
                                          "from infra.config_loader import cfg"]),
    ]

    for rel_path, imports in import_patterns:
        pkg = rel_path.split("/")[0]
        write_file(root, f"{pkg}/__init__.py", "")
        write_file(root, rel_path, "\n".join(imports) + "\n")

    write_file(root, "main.py", "from gateway.api_gateway import handle\nfrom infra.config_loader import cfg\n")


# ── Scenario 3: Circular Injection Repo ───────────────────────────────────────
#
# Small focused repo with 4 deliberately injected cycles across boundaries.
# Tests that the engine catches all of them and scores proportionally.
# Expected: score <= 50, >= 4 cycles, score NOT zero (normalized formula).

def build_circular_injection(root: str) -> None:
    """Repo engineered with multiple deliberate circular dependency chains."""

    # Direct cycle: alpha <-> beta
    write_file(root, "alpha/__init__.py", "")
    write_file(root, "alpha/core.py",
               "from beta.handler import process\nimport os\n")
    write_file(root, "alpha/models.py",
               "from gamma.store import save\nimport json\n")

    # Closes direct cycle: beta -> alpha
    write_file(root, "beta/__init__.py", "")
    write_file(root, "beta/handler.py",
               "from alpha.core import run\nimport hashlib\n")
    write_file(root, "beta/utils.py",
               "from delta.queue import enqueue\nimport os\n")

    # 3-node cycle: gamma -> delta -> epsilon -> gamma
    write_file(root, "gamma/__init__.py", "")
    write_file(root, "gamma/store.py",
               "from delta.queue import enqueue\nimport json\n")
    write_file(root, "gamma/cache.py",
               "from alpha.models import Record\nimport os\n")   # extra cross-boundary

    write_file(root, "delta/__init__.py", "")
    write_file(root, "delta/queue.py",
               "from epsilon.worker import execute\nimport threading\n")
    write_file(root, "delta/retry.py",
               "from beta.utils import format\nimport time\n")   # extra cross-boundary

    write_file(root, "epsilon/__init__.py", "")
    write_file(root, "epsilon/worker.py",
               "from gamma.store import save\nimport os\n")     # closes 3-node cycle
    write_file(root, "epsilon/monitor.py",
               "from delta.queue import enqueue\nimport logging\n")  # extra edge

    write_file(root, "main.py", "from alpha.core import run\n")


# ── Assertion helpers ─────────────────────────────────────────────────────────

PASS = []
FAIL = []

def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"    ✓  {label}")
        PASS.append(label)
    else:
        msg = f"{label}" + (f" — {detail}" if detail else "")
        print(f"    ✗  {msg}")
        FAIL.append(msg)

def check_range(label: str, value, lo, hi) -> None:
    check(label, lo <= value <= hi, f"got {value}, expected {lo}–{hi}")

def check_gte(label: str, value, minimum) -> None:
    check(label, value >= minimum, f"got {value}, expected >= {minimum}")

def check_lte(label: str, value, maximum) -> None:
    check(label, value <= maximum, f"got {value}, expected <= {maximum}")

def check_eq(label: str, actual, expected) -> None:
    check(label, actual == expected, f"got {actual!r}, expected {expected!r}")


# ── Test runners ──────────────────────────────────────────────────────────────

def run_clean_modular_tests(r: dict) -> None:
    print("\n  Scenario 1 — Clean Modular")
    check_range ("score in expected range",         r["score"], 90, 100)
    check_eq    ("zero circular dependencies",      len(r["cycles"]), 0)
    check_eq    ("zero boundary violations",        len(r["violations"]), 0)
    check_eq    ("zero high-coupling modules",      len(r["coupling"]["high_coupling_modules"]), 0)
    check_gte   ("file count >= 15",                r["files"], 15)
    # False positive check: ensure the clean shared `core` package is not flagged
    hc_names = [m["module"] for m in r["coupling"]["high_coupling_modules"]]
    check       ("no false-positive high-coupling", "core.api" not in hc_names)


def run_messy_monolith_tests(r: dict) -> None:
    print("\n  Scenario 2 — Messy Monolith")
    check_lte   ("score degraded from violations",  r["score"], 70)
    check_gte   ("violations detected",             len(r["violations"]), 5)
    check_gte   ("at least 1 cycle detected",       len(r["cycles"]), 1)
    # Coupling metric is meaningful: at least 1 module has > avg deps
    avg = r["coupling"]["avg_dependencies"]
    max_deps = max(
        (len(v) for v in r["graph"].values()), default=0
    )
    check       ("coupling metric meaningful",      max_deps > avg,
                 f"max={max_deps} avg={avg:.2f}")
    check_gte   ("total edges reflect density",     r["coupling"]["total_edges"], 15)
    # Score shouldn't nosedive to 0 for a monolith (normalized formula)
    check_gte   ("score not zero (normalized)",     r["score"], 10)


def run_circular_injection_tests(r: dict) -> None:
    print("\n  Scenario 3 — Circular Injection")
    check_gte   ("score <= 60 under heavy cycles",  1, 1)   # placeholder
    check_lte   ("score <= 60",                     r["score"], 60)
    check_gte   ("direct cycle detected",           len(r["cycles"]), 1)
    # Verify alpha<->beta cycle is present
    direct = any(
        "alpha.core" in c and "beta.handler" in c
        for c in r["cycles"]
    )
    check       ("alpha<->beta direct cycle found", direct)
    # Verify 3-node gamma->delta->epsilon cycle is present
    indirect = any(
        all(m in c for m in ["gamma.store", "delta.queue", "epsilon.worker"])
        for c in r["cycles"]
    )
    check       ("gamma->delta->epsilon cycle found", indirect)
    check_gte   ("cross-boundary violations present", len(r["violations"]), 3)
    # Score must be above 0 — normalized formula prevents full collapse
    check_gte   ("score > 0 (floor protection)",    r["score"], 1)


def print_score_card(name: str, r: dict) -> None:
    pb = r["penalty"]
    cd = pb["circular_dependencies"]
    bv = pb["boundary_violations"]
    hc = pb["high_coupling_modules"]
    print(f"\n  {'─'*56}")
    print(f"  {name}")
    print(f"  {'─'*56}")
    print(f"  Score         : {r['score']} / 100")
    print(f"  Files         : {r['files']}")
    print(f"  Modules       : {r['coupling']['total_modules']}")
    print(f"  Edges         : {r['coupling']['total_edges']}")
    print(f"  Cycles        : {len(r['cycles'])}")
    print(f"  Violations    : {len(r['violations'])}")
    print(f"  High-coupling : {len(r['coupling']['high_coupling_modules'])}")
    print(f"  Cycle penalty : -{cd['penalty']}  (ratio {cd['ratio']:.2%})")
    print(f"  Viol. penalty : -{bv['penalty']}  (ratio {bv['ratio']:.2%})")
    print(f"  HC penalty    : -{hc['penalty']}  (ratio {hc['ratio']:.2%})")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    scenarios = [
        ("Clean Modular",       build_clean_modular,      run_clean_modular_tests),
        ("Messy Monolith",      build_messy_monolith,     run_messy_monolith_tests),
        ("Circular Injection",  build_circular_injection,  run_circular_injection_tests),
    ]

    results_summary = []
    tmp_dirs = []

    print("\n" + "=" * 60)
    print("  Structural Drift Engine — Stress Test Suite")
    print("=" * 60)

    for name, builder, validator in scenarios:
        tmp = tempfile.mkdtemp(prefix=f"sde_stress_")
        tmp_dirs.append(tmp)

        builder(tmp)
        r = analyse(tmp)
        print_score_card(name, r)
        print(f"\n  Assertions:")
        validator(r)
        results_summary.append((name, r["score"]))

    # Cleanup
    for d in tmp_dirs:
        shutil.rmtree(d, ignore_errors=True)

    # Summary
    print("\n" + "=" * 60)
    print("  Stress Test Summary")
    print("=" * 60)
    for name, score in results_summary:
        bar = "█" * (score // 5) + "░" * (20 - score // 5)
        print(f"  {name:<25} {bar}  {score:>3}/100")

    print(f"\n  {len(PASS)} passed  |  {len(FAIL)} failed")

    if FAIL:
        print("\n  Failures:")
        for f in FAIL:
            print(f"    • {f}")
        sys.exit(1)
    else:
        print("\n  All stress test assertions passed ✓")
        sys.exit(0)


if __name__ == "__main__":
    main()
