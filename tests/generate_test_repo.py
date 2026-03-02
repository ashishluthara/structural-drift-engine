"""
tests/generate_test_repo.py — Test Repo Generator & Validator

Creates a synthetic Python repository with known structural problems,
runs the drift engine against it, and asserts the results are correct.

Usage:
    python tests/generate_test_repo.py

Exit codes:
    0 — all assertions passed
    1 — one or more assertions failed
"""

import json
import os
import sys
import shutil
import tempfile
import textwrap
from pathlib import Path

# Resolve engine modules relative to this file
ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from scanner import scan_repository
from graph_builder import build_dependency_graph
from metrics import detect_cycles, compute_coupling_metrics
from drift import detect_boundary_violations, calculate_drift_score


# ── Test repo layout ──────────────────────────────────────────────────────────
#
# Boundaries:  billing/  auth/  users/  payments/  core/
#
# Designed violations:
#   Cycle 1 (direct):    billing.service  ←→  auth.utils
#   Cycle 2 (indirect):  payments.gateway → billing.models → users.profile → payments.gateway
#   Boundary violations: billing→auth, auth→billing, payments→billing,
#                        billing→users, users→payments
#   High coupling:       core.hub  (11 deps, threshold=10)
#   Clean modules:       core.utils (no outgoing deps)

REPO_STRUCTURE: dict[str, str] = {
    # ── billing ───────────────────────────────────────────────────────────────
    "billing/__init__.py": "",
    "billing/service.py": textwrap.dedent("""\
        # billing.service imports auth.utils → creates direct cycle with auth.utils
        from auth.utils import verify_token
        from billing.models import Invoice
    """),
    "billing/models.py": textwrap.dedent("""\
        # billing.models imports users.profile → part of indirect cycle
        from users.profile import UserProfile
        import json
    """),
    # ── auth ──────────────────────────────────────────────────────────────────
    "auth/__init__.py": "",
    "auth/utils.py": textwrap.dedent("""\
        # auth.utils imports billing.service → closes the direct cycle
        from billing.service import charge
        import hashlib
    """),
    "auth/models.py": textwrap.dedent("""\
        import os
    """),
    # ── users ─────────────────────────────────────────────────────────────────
    "users/__init__.py": "",
    "users/profile.py": textwrap.dedent("""\
        # users.profile imports payments.gateway → closes the indirect cycle
        from payments.gateway import process
        import dataclasses
    """),
    "users/models.py": textwrap.dedent("""\
        import os
    """),
    # ── payments ──────────────────────────────────────────────────────────────
    "payments/__init__.py": "",
    "payments/gateway.py": textwrap.dedent("""\
        # payments.gateway imports billing.models → starts the indirect cycle
        from billing.models import Invoice
        import decimal
    """),
    # ── notifications ─────────────────────────────────────────────────────────
    "notifications/__init__.py": "",
    "notifications/email.py": textwrap.dedent("""\
        import smtplib
    """),
    "notifications/sms.py": textwrap.dedent("""\
        import os
    """),
    # ── reporting ─────────────────────────────────────────────────────────────
    "reporting/__init__.py": "",
    "reporting/generator.py": textwrap.dedent("""\
        import csv
    """),
    "reporting/exporter.py": textwrap.dedent("""\
        import json
    """),
    # ── core ──────────────────────────────────────────────────────────────────
    "core/__init__.py": "",
    "core/hub.py": textwrap.dedent("""\
        # core.hub imports 11 internal project modules → exceeds high-coupling threshold of 10
        from billing.service import run
        from billing.models import Invoice
        from auth.utils import verify_token
        from auth.models import AuthRecord
        from users.profile import UserProfile
        from users.models import UserBase
        from payments.gateway import process
        from notifications.email import send
        from notifications.sms import alert
        from reporting.generator import build
        from reporting.exporter import export
    """),
    "core/utils.py": textwrap.dedent("""\
        # core.utils is clean — no project imports
        import os
        import logging
    """),
    # ── root ──────────────────────────────────────────────────────────────────
    "main.py": textwrap.dedent("""\
        from billing.service import run
        from auth.utils import verify_token
    """),
}


# ── Repo builder ──────────────────────────────────────────────────────────────

def build_test_repo(base_dir: str) -> str:
    """
    Write the synthetic test repo to a temporary directory.

    Args:
        base_dir: Parent directory to create the test repo inside.

    Returns:
        Absolute path to the created repo root.
    """
    repo_root = os.path.join(base_dir, "test_repo")

    for rel_path, content in REPO_STRUCTURE.items():
        full_path = Path(repo_root) / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")

    return repo_root


# ── Assertion helpers ─────────────────────────────────────────────────────────

class AssertionError_(Exception):
    """Carries a human-readable description of the failed assertion."""


def assert_equal(label: str, actual, expected) -> None:
    if actual != expected:
        raise AssertionError_(f"FAIL [{label}]\n  expected: {expected!r}\n  actual:   {actual!r}")


def assert_in(label: str, item, container) -> None:
    if item not in container:
        raise AssertionError_(f"FAIL [{label}]\n  {item!r} not found in {container!r}")


def assert_gte(label: str, actual, minimum) -> None:
    if actual < minimum:
        raise AssertionError_(f"FAIL [{label}]\n  {actual!r} is less than minimum {minimum!r}")


def assert_true(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError_(f"FAIL [{label}]{('  ' + detail) if detail else ''}")


# ── Test cases ────────────────────────────────────────────────────────────────

def test_scanner(repo_root: str) -> list[str]:
    """scanner finds all .py files and none from ignored dirs."""
    files = scan_repository(repo_root)
    assert_gte("scanner: file count", len(files), 12)
    assert_true(
        "scanner: no __pycache__ files",
        not any("__pycache__" in f for f in files),
    )
    assert_true(
        "scanner: includes root main.py",
        any(f.endswith("main.py") or f == "main.py" for f in files),
    )
    return files


def test_graph_builder(repo_root: str, file_paths: list[str]) -> dict:
    """graph_builder resolves project-internal imports correctly."""
    graph = build_dependency_graph(file_paths, repo_root)

    assert_true("graph: billing.service exists", "billing.service" in graph)
    assert_true("graph: auth.utils exists", "auth.utils" in graph)
    assert_in("graph: billing.service → auth.utils", "auth.utils", graph["billing.service"])
    assert_in("graph: auth.utils → billing.service", "billing.service", graph["auth.utils"])
    assert_in("graph: payments.gateway → billing.models", "billing.models", graph["payments.gateway"])
    assert_in("graph: billing.models → users.profile", "users.profile", graph["billing.models"])
    assert_in("graph: users.profile → payments.gateway", "payments.gateway", graph["users.profile"])

    # core.utils should have no project deps
    assert_equal("graph: core.utils is clean", graph.get("core.utils", []), [])

    return graph


def test_cycle_detection(graph: dict) -> list:
    """metrics detects both the direct and indirect cycle."""
    cycles = detect_cycles(graph)

    assert_gte("cycles: at least 2 detected", len(cycles), 2)

    # Verify the direct cycle: billing.service ↔ auth.utils is present
    def contains_edge(cycle: list[str], a: str, b: str) -> bool:
        edges = list(zip(cycle, cycle[1:]))
        return (a, b) in edges or (b, a) in edges

    direct_found = any(
        "billing.service" in c and "auth.utils" in c for c in cycles
    )
    assert_true("cycles: direct billing↔auth cycle detected", direct_found)

    # Verify the indirect cycle contains all three participants
    indirect_found = any(
        all(mod in c for mod in ["payments.gateway", "billing.models", "users.profile"])
        for c in cycles
    )
    assert_true("cycles: indirect payments→billing→users cycle detected", indirect_found)

    return cycles


def test_coupling_metrics(graph: dict) -> dict:
    """metrics flags core.hub as high-coupling at threshold=10."""
    metrics = compute_coupling_metrics(graph, high_coupling_threshold=10)

    hc_modules = [m["module"] for m in metrics["high_coupling_modules"]]
    assert_in("coupling: core.hub is high-coupling", "core.hub", hc_modules)
    assert_true("coupling: core.utils not high-coupling", "core.utils" not in hc_modules)
    assert_gte("coupling: total edges >= 9", metrics["total_edges"], 9)

    return metrics


def test_boundary_violations(graph: dict) -> list:
    """drift detects all expected cross-boundary imports."""
    violations = detect_boundary_violations(graph)

    def has_violation(src: str, tgt: str) -> bool:
        return any(v["source"] == src and v["target"] == tgt for v in violations)

    assert_true("violations: billing.service → auth.utils", has_violation("billing.service", "auth.utils"))
    assert_true("violations: auth.utils → billing.service", has_violation("auth.utils", "billing.service"))
    assert_true("violations: payments.gateway → billing.models", has_violation("payments.gateway", "billing.models"))
    assert_true("violations: billing.models → users.profile", has_violation("billing.models", "users.profile"))
    assert_true("violations: users.profile → payments.gateway", has_violation("users.profile", "payments.gateway"))

    return violations


def test_drift_score(cycles: list, violations: list, metrics: dict) -> dict:
    """drift score is correctly calculated from known penalties."""
    hc = metrics["high_coupling_modules"]
    result = calculate_drift_score(
        cycles, violations, hc,
        total_modules=metrics["total_modules"],
        total_edges=metrics["total_edges"],
    )

    score = result["drift_score"]
    pb = result["penalty_breakdown"]

    # Score must be between 0 and 100
    assert_true("score: in valid range", 0 <= score <= 100, f"score={score}")

    # With 2 cycles in a ~20-module repo, cycle penalty should be > 0
    assert_gte("score: cycle penalty > 0", pb["circular_dependencies"]["penalty"], 1)

    # With 5+ violations, violation penalty should be > 0
    assert_gte("score: violation penalty > 0", pb["boundary_violations"]["penalty"], 1)

    # core.hub -> at least 1 high-coupling penalty
    assert_gte("score: coupling penalty >= 1", pb["high_coupling_modules"]["penalty"], 1)

    # Score should be noticeably degraded (normalized but still impacted)
    assert_true("score: degraded from violations", score <= 80, f"score={score} unexpectedly high")

    return result


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all_tests() -> None:
    """Build the test repo, run all test cases, and report results."""
    tmp_dir = tempfile.mkdtemp(prefix="drift_test_")
    passed = 0
    failed = 0
    failures: list[str] = []

    try:
        print(f"\nBuilding test repo in: {tmp_dir}")
        repo_root = build_test_repo(tmp_dir)
        print(f"Test repo created with {len(REPO_STRUCTURE)} files\n")

        test_cases = [
            ("Scanner",            lambda: test_scanner(repo_root)),
            ("Graph Builder",      lambda: test_graph_builder(repo_root, test_scanner(repo_root))),
            ("Cycle Detection",    lambda: _run_full_cycle_test(repo_root)),
            ("Coupling Metrics",   lambda: _run_full_coupling_test(repo_root)),
            ("Boundary Violations",lambda: _run_full_violation_test(repo_root)),
            ("Drift Score",        lambda: _run_full_score_test(repo_root)),
        ]

        for name, fn in test_cases:
            try:
                fn()
                print(f"  ✓  {name}")
                passed += 1
            except AssertionError_ as exc:
                print(f"  ✗  {name}")
                print(f"     {exc}")
                failures.append(str(exc))
                failed += 1
            except Exception as exc:
                print(f"  ✗  {name} (unexpected error)")
                print(f"     {type(exc).__name__}: {exc}")
                failures.append(f"{name}: {exc}")
                failed += 1

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"\n{'─' * 40}")
    print(f"Results:  {passed} passed  |  {failed} failed")

    if failed:
        print("\nFailure details:")
        for f in failures:
            print(f"  • {f}")
        sys.exit(1)
    else:
        print("\nAll assertions passed ✓")
        sys.exit(0)


# ── Pipeline helpers (run full pipeline to reach each test stage) ─────────────

def _get_graph(repo_root: str):
    files = scan_repository(repo_root)
    return build_dependency_graph(files, repo_root)


def _run_full_cycle_test(repo_root: str):
    return test_cycle_detection(_get_graph(repo_root))


def _run_full_coupling_test(repo_root: str):
    return test_coupling_metrics(_get_graph(repo_root))


def _run_full_violation_test(repo_root: str):
    return test_boundary_violations(_get_graph(repo_root))


def _run_full_score_test(repo_root: str):
    graph = _get_graph(repo_root)
    cycles = detect_cycles(graph)
    metrics = compute_coupling_metrics(graph, high_coupling_threshold=10)
    violations = detect_boundary_violations(graph)
    return test_drift_score(cycles, violations, metrics)


if __name__ == "__main__":
    run_all_tests()
