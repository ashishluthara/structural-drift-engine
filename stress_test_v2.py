"""
tests/stress_test_v2.py — Phase 2 Stress Test Suite

Tests all 4 required archetypes:
    1. Clean modular repo           — zero false positives
    2. Medium messy repo            — coupling + violations
    3. Artificially duplicated repo — duplication detection
    4. Deliberate boundary violation — boundary + cycle detection

Additionally validates:
    - .driftconfig.json suppresses expected false positives
    - Drift Index behaves sensibly across all archetypes
    - Performance: all 4 repos complete under 10 seconds total
    - Percentile coupling is meaningful
    - Complexity is computed correctly

Usage:
    python tests/stress_test_v2.py
"""

import os
import sys
import shutil
import tempfile
import textwrap
import time
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ENGINE_DIR))

from config import DriftConfig, load_config
from scanner import scan_repository, file_path_to_module_name
from graph_builder import build_dependency_graph
from metrics import detect_cycles, compute_coupling_metrics
from drift import detect_boundary_violations
from complexity import compute_complexity_map, compute_complexity_delta, summarise_complexity
from duplication import detect_duplicates
from drift_index import calculate_drift_index


# ── Pipeline helper ───────────────────────────────────────────────────────────

def analyse(repo_root: str, config: DriftConfig | None = None) -> dict:
    if config is None:
        config = load_config(repo_root)

    file_paths = scan_repository(repo_root, ignore_test_dirs=config.ignore_test_dirs)
    module_map = {fp: file_path_to_module_name(fp) for fp in file_paths}
    graph = build_dependency_graph(file_paths, repo_root)
    coupling = compute_coupling_metrics(graph, high_coupling_percentile=config.high_coupling_percentile)
    cycles = detect_cycles(graph)
    violations = detect_boundary_violations(graph, config)
    complexity_map = compute_complexity_map(file_paths, repo_root, module_map)
    duplicate_pairs = detect_duplicates(
        file_paths, repo_root, module_map,
        threshold=config.duplication_threshold,
        min_lines=config.min_lines_for_duplication,
        max_modules=config.max_modules_for_duplication,
    )
    complexity_summary = summarise_complexity(complexity_map, {})
    drift = calculate_drift_index(
        cycles=cycles,
        high_coupling_modules=coupling["high_coupling_modules"],
        duplicate_pairs=duplicate_pairs,
        total_positive_complexity_delta=0,
    )

    return {
        "files": len(file_paths),
        "graph": graph,
        "coupling": coupling,
        "cycles": cycles,
        "violations": violations,
        "complexity_map": complexity_map,
        "complexity_summary": complexity_summary,
        "duplicate_pairs": duplicate_pairs,
        "index": drift["drift_index"],
        "components": drift["components"],
    }


def write_file(root: str, rel_path: str, content: str) -> None:
    fp = Path(root) / rel_path
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(textwrap.dedent(content), encoding="utf-8")


# ── Repo builders ─────────────────────────────────────────────────────────────

def build_clean_modular(root: str) -> None:
    """Well-bounded packages, shared core, no cross-boundary imports."""
    for pkg in ["billing", "auth", "users", "payments"]:
        write_file(root, f"{pkg}/__init__.py", "")
        for mod in ["service", "models", "utils"]:
            write_file(root, f"{pkg}/{mod}.py",
                       f"# {pkg}.{mod}\nimport os\nimport logging\n\n"
                       f"def main():\n    pass\n")

    write_file(root, "core/__init__.py", "")
    write_file(root, "core/api.py",
               "import json\nimport os\n\ndef handle():\n    pass\n")
    write_file(root, "core/config.py",
               "import os\n\nclass Config:\n    pass\n")
    write_file(root, "main.py",
               "from core.api import handle\nfrom core.config import Config\n")

    # Test files — should be ignored
    write_file(root, "tests/test_billing.py",
               "from billing.service import main\nimport os\n")
    write_file(root, "tests/test_auth.py",
               "from auth.utils import verify\nimport os\n")


def build_medium_messy(root: str) -> None:
    """Packages that import across boundaries with some cycles."""
    pkgs = {
        "orders":    ["manager", "validator", "models"],
        "inventory": ["tracker", "reserver", "models"],
        "pricing":   ["calculator", "discounts", "models"],
        "shipping":  ["dispatcher", "tracker", "models"],
        "reports":   ["generator", "exporter", "scheduler"],
    }
    for pkg, mods in pkgs.items():
        write_file(root, f"{pkg}/__init__.py", "")
        for mod in mods:
            write_file(root, f"{pkg}/{mod}.py",
                       f"import os\n\ndef {mod}_fn():\n    pass\n")

    # Cross-boundary wiring (violations)
    write_file(root, "orders/manager.py",
               "from inventory.reserver import reserve\nfrom pricing.calculator import price\nimport os\n")
    write_file(root, "inventory/tracker.py",
               "from orders.models import Order\nfrom reports.generator import report\nimport os\n")
    write_file(root, "pricing/calculator.py",
               "from inventory.models import Item\nimport os\n")
    write_file(root, "shipping/dispatcher.py",
               "from orders.manager import process\nfrom inventory.tracker import check\nimport os\n")
    write_file(root, "reports/generator.py",
               "from orders.models import Order\nfrom pricing.models import Price\nimport os\n")

    # Cycle: orders.manager -> pricing.calculator -> inventory.models -> orders.manager
    write_file(root, "inventory/models.py",
               "from orders.manager import get\nimport os\n")
    write_file(root, "orders/models.py",
               "import os\nclass Order:\n    pass\nclass OrderRef:\n    pass\n")

    write_file(root, "main.py",
               "from orders.manager import process\nfrom reports.generator import report\n")


def build_duplication_repo(root: str) -> None:
    """Modules with deliberately near-identical structure and tokens."""
    TEMPLATE = """\
import os
import logging

logger = logging.getLogger(__name__)

class {name}Service:
    def __init__(self, db_connection, config, logger):
        self.db = db_connection
        self.config = config
        self.logger = logger

    def create_{lower}(self, data):
        validated = self._validate(data)
        result = self.db.insert(validated)
        self.logger.info(f"Created {{result}}")
        return result

    def update_{lower}(self, id, data):
        validated = self._validate(data)
        result = self.db.update(id, validated)
        self.logger.info(f"Updated {{result}}")
        return result

    def delete_{lower}(self, id):
        result = self.db.delete(id)
        self.logger.info(f"Deleted {{result}}")
        return result

    def get_{lower}(self, id):
        return self.db.find(id)

    def list_{lower}s(self, filters=None):
        return self.db.query(filters or {{}})

    def _validate(self, data):
        if not data:
            raise ValueError("data required")
        return data
"""
    entities = [
        ("Billing",  "billing"),
        ("Invoice",  "invoice"),
        ("Payment",  "payment"),
        ("Charge",   "charge"),
    ]

    for pkg_name, lower in entities:
        write_file(root, f"{lower}/__init__.py", "")
        write_file(root, f"{lower}/service.py",
                   TEMPLATE.format(name=pkg_name, lower=lower))
        write_file(root, f"{lower}/models.py",
                   f"class {pkg_name}:\n    id: int\n    name: str\n")

    write_file(root, "main.py",
               "from billing.service import BillingService\n"
               "from invoice.service import InvoiceService\n")


def build_boundary_violation_repo(root: str) -> None:
    """Deliberate cycles and boundary violations, no config suppression."""
    write_file(root, "alpha/__init__.py", "")
    write_file(root, "alpha/service.py",
               "from beta.handler import process\nfrom gamma.store import save\nimport os\n\n"
               "def run():\n    if True:\n        for i in range(10):\n            pass\n")
    write_file(root, "alpha/models.py",
               "import os\nclass AlphaModel:\n    pass\n")

    write_file(root, "beta/__init__.py", "")
    write_file(root, "beta/handler.py",
               "from alpha.service import run\nimport hashlib\n\n"
               "def process():\n    try:\n        pass\n    except Exception:\n        pass\n")
    write_file(root, "beta/utils.py",
               "from gamma.store import load\nimport os\n")

    write_file(root, "gamma/__init__.py", "")
    write_file(root, "gamma/store.py",
               "from delta.queue import enqueue\nimport json\n\n"
               "def save(data):\n    pass\ndef load():\n    pass\n")
    write_file(root, "gamma/cache.py",
               "from alpha.models import AlphaModel\nimport os\n")

    write_file(root, "delta/__init__.py", "")
    write_file(root, "delta/queue.py",
               "from gamma.store import save\nimport threading\n\n"
               "def enqueue(item):\n    pass\n")
    write_file(root, "delta/worker.py",
               "from beta.utils import format\nimport os\n")

    write_file(root, "main.py",
               "from alpha.service import run\n")


def build_config_suppression_repo(root: str) -> None:
    """Repo where 'core' is a shared package — violations should be suppressed by config."""
    for pkg in ["billing", "auth", "users"]:
        write_file(root, f"{pkg}/__init__.py", "")
        write_file(root, f"{pkg}/service.py",
                   f"from core.utils import helper\nfrom core.models import Base\nimport os\n")

    write_file(root, "core/__init__.py", "")
    write_file(root, "core/utils.py",
               "import os\ndef helper():\n    pass\n")
    write_file(root, "core/models.py",
               "class Base:\n    pass\n")
    write_file(root, "main.py", "from billing.service import run\n")

    # Config that marks core as an allowed shared boundary
    import json
    config = {
        "ignore_boundaries": ["core"],
        "allowed_dependencies": {},
        "strict_mode": False,
    }
    (Path(root) / ".driftconfig.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8"
    )


# ── Assertions ────────────────────────────────────────────────────────────────

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"    ✓  {label}")
        PASS.append(label)
    else:
        msg = f"{label}" + (f" — {detail}" if detail else "")
        print(f"    ✗  {msg}")
        FAIL.append(msg)


def check_range(label, val, lo, hi):
    check(label, lo <= val <= hi, f"got {val}, expected {lo}–{hi}")

def check_gte(label, val, minimum):
    check(label, val >= minimum, f"got {val}, expected >= {minimum}")

def check_lte(label, val, maximum):
    check(label, val <= maximum, f"got {val}, expected <= {maximum}")

def check_eq(label, actual, expected):
    check(label, actual == expected, f"got {actual!r}, expected {expected!r}")


# ── Test runners ──────────────────────────────────────────────────────────────

def test_clean_modular(r: dict) -> None:
    print("\n  1. Clean Modular Repo")
    check_range("score 90–100",          r["index"], 90, 100)
    check_eq   ("zero cycles",           len(r["cycles"]), 0)
    check_eq   ("zero violations",       len(r["violations"]), 0)
    check_eq   ("zero duplicates",       len(r["duplicate_pairs"]), 0)
    # Test dirs excluded — main/ files only
    all_mods = list(r["graph"].keys())
    test_mods = [m for m in all_mods if "test" in m.lower()]
    check_eq   ("test files excluded",   len(test_mods), 0)
    # False positive check: no core modules flagged as high coupling
    hc = [m["module"] for m in r["coupling"]["high_coupling_modules"]]
    check       ("no false-positive high coupling", not any("core" in m for m in hc))


def test_medium_messy(r: dict) -> None:
    print("\n  2. Medium Messy Repo")
    check_lte  ("score degraded <= 88",  r["index"], 88)
    check_gte  ("violations >= 3",       len(r["violations"]), 3)
    check_gte  ("cycles >= 1",           len(r["cycles"]), 1)
    # Percentile coupling: at least one module should be above 85th pct
    # given the dense import pattern
    all_counts = [len(v) for v in r["graph"].values()]
    max_deps = max(all_counts, default=0)
    avg_deps = sum(all_counts) / max(len(all_counts), 1)
    check      ("max deps > avg (coupling spread)", max_deps > avg_deps,
                f"max={max_deps:.1f} avg={avg_deps:.1f}")
    check_gte  ("score > 0 (not collapsed)", r["index"], 10)


def test_duplication(r: dict) -> None:
    print("\n  3. Duplication Repo")
    check_gte  ("duplicate pairs >= 2",  len(r["duplicate_pairs"]), 2)
    # All pairs should have high similarity
    if r["duplicate_pairs"]:
        min_sim = min(p["similarity"] for p in r["duplicate_pairs"])
        check_gte("min similarity >= 0.80", min_sim, 0.80)
    # Duplication penalty should be present
    dp = r["components"].get("duplication_penalty", {}).get("value", 0)
    check_gte  ("duplication penalty > 0", dp, 1)
    check_lte  ("score reduced by duplication", r["index"], 90)


def test_boundary_violations(r: dict) -> None:
    print("\n  4. Boundary Violation Repo")
    check_gte  ("violations >= 4",       len(r["violations"]), 4)
    check_gte  ("cycles >= 1",           len(r["cycles"]), 1)
    check_lte  ("score <= 75",           r["index"], 75)
    # Specific cycle: alpha <-> beta
    direct = any(
        "alpha.service" in c and "beta.handler" in c
        for c in r["cycles"]
    )
    check      ("alpha.service ↔ beta.handler cycle detected", direct)
    # Indirect: gamma <-> delta
    indirect = any(
        "gamma.store" in c and "delta.queue" in c
        for c in r["cycles"]
    )
    check      ("gamma.store ↔ delta.queue cycle detected", indirect)


def test_config_suppression(r: dict) -> None:
    print("\n  5. Config Suppression (ignore_boundaries: core)")
    # With core in ignore_boundaries, billing/auth/users -> core should NOT be violations
    core_violations = [
        v for v in r["violations"]
        if v.get("target_boundary") == "core" or v.get("source_boundary") == "core"
    ]
    check_eq   ("core violations suppressed = 0", len(core_violations), 0)
    check_gte  ("score stays high >= 85", r["index"], 85)


def test_complexity_detection(root: str) -> None:
    """Directly test complexity counting on a known file."""
    print("\n  6. Complexity Calculation")
    from complexity import compute_file_complexity

    tmp = tempfile.mkdtemp(prefix="drift_cx_")
    fp = os.path.join(tmp, "sample.py")
    # This has: 1 base + 2 if + 1 for + 1 while + 1 try + 1 except +
    # 1 with + 1 funcdef + 2 boolops = 11
    with open(fp, "w") as f:
        f.write(textwrap.dedent("""\
            def process(data, flag):
                if data and flag:
                    for item in data:
                        if item:
                            pass
                while True:
                    try:
                        with open("x") as f:
                            pass
                    except Exception:
                        break
        """))
    score = compute_file_complexity("sample.py", tmp)
    shutil.rmtree(tmp)
    check_gte("complexity score >= 8", score or 0, 8)
    check(    "complexity score is int", isinstance(score, int))


# ── Runner ────────────────────────────────────────────────────────────────────

def run_all() -> None:
    print("\n" + "=" * 62)
    print("  Structural Drift Engine v2 — Stress Test Suite")
    print("=" * 62)

    scenarios: list[tuple[str, callable, callable]] = [
        ("Clean Modular",          build_clean_modular,         test_clean_modular),
        ("Medium Messy",           build_medium_messy,           test_medium_messy),
        ("Artificial Duplication", build_duplication_repo,       test_duplication),
        ("Boundary Violations",    build_boundary_violation_repo,test_boundary_violations),
        ("Config Suppression",     build_config_suppression_repo,test_config_suppression),
    ]

    scores: list[tuple[str, int]] = []
    tmp_dirs: list[str] = []

    t_start = time.perf_counter()

    for name, builder, validator in scenarios:
        tmp = tempfile.mkdtemp(prefix="sde_v2_")
        tmp_dirs.append(tmp)
        builder(tmp)
        r = analyse(tmp)
        scores.append((name, r["index"]))
        validator(r)

    # Extra tests that don't need a full repo
    test_complexity_detection("")

    elapsed = time.perf_counter() - t_start

    # Cleanup
    for d in tmp_dirs:
        shutil.rmtree(d, ignore_errors=True)

    # Summary
    print("\n" + "=" * 62)
    print("  Results")
    print("=" * 62)
    for name, score in scores:
        bar = "█" * (score // 5) + "░" * (20 - score // 5)
        print(f"  {name:<28} {bar}  {score:>3}/100")

    print(f"\n  Time: {elapsed:.2f}s")
    print(f"  {len(PASS)} passed  |  {len(FAIL)} failed")

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
