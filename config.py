"""
config.py — Drift Configuration Loader  (Phase 3)

Adds to Phase 2:
    - domains: explicit module grouping beyond folder heuristics
    - allowed_domain_dependencies: cross-domain whitelist
    - approved_cycles: known circular deps that should not be penalised
    - approved_violations: known cross-boundary imports to suppress

Full schema:
{
    "ignore_boundaries": ["common", "utils"],
    "allowed_dependencies": { "billing": ["auth"] },
    "strict_mode": false,

    "domains": {
        "billing": ["billing.*", "invoicing.*"],
        "auth":    ["auth.*", "jwt.*"]
    },
    "allowed_domain_dependencies": {
        "billing": ["auth"]
    },

    "approved_cycles": [
        ["billing.service", "auth.utils"]
    ],
    "approved_violations": [
        {"source": "billing", "target": "auth"}
    ],

    "min_lines_for_duplication": 10,
    "duplication_threshold": 0.85,
    "high_coupling_percentile": 85,
    "ignore_test_dirs": true,
    "max_modules_for_duplication": 200
}
"""

import fnmatch
import json
from pathlib import Path

CONFIG_FILENAME = ".driftconfig.json"

DEFAULT_IGNORE_BOUNDARIES = {"shared", "common", "utils", "core", "lib", "helpers", "base"}

DEFAULTS = {
    "ignore_boundaries":          list(DEFAULT_IGNORE_BOUNDARIES),
    "allowed_dependencies":       {},
    "strict_mode":                False,
    "domains":                    {},
    "allowed_domain_dependencies": {},
    "approved_cycles":            [],
    "approved_violations":        [],
    "min_lines_for_duplication":  10,
    "duplication_threshold":      0.85,
    "high_coupling_percentile":   85,
    "ignore_test_dirs":           True,
    "max_modules_for_duplication": 200,
}


class DriftConfig:
    def __init__(self, raw: dict) -> None:
        self.ignore_boundaries: set[str] = set(
            raw.get("ignore_boundaries", DEFAULTS["ignore_boundaries"])
        )
        self.allowed_dependencies: dict[str, list[str]] = raw.get(
            "allowed_dependencies", DEFAULTS["allowed_dependencies"]
        )
        self.strict_mode: bool = raw.get("strict_mode", DEFAULTS["strict_mode"])

        # Domain modeling
        self.domains: dict[str, list[str]] = raw.get("domains", {})
        self.allowed_domain_dependencies: dict[str, list[str]] = raw.get(
            "allowed_domain_dependencies", {}
        )

        # Justification overrides
        self._approved_cycles_raw: list[list[str]] = raw.get("approved_cycles", [])
        self._approved_violations_raw: list[dict] = raw.get("approved_violations", [])

        # Pre-compute sets for fast lookup
        self._approved_cycle_sets: list[frozenset] = [
            frozenset(c) for c in self._approved_cycles_raw
        ]
        self._approved_violation_pairs: set[tuple] = {
            (v["source"], v["target"])
            for v in self._approved_violations_raw
            if "source" in v and "target" in v
        }

        # Duplication / coupling config
        self.min_lines_for_duplication: int = raw.get(
            "min_lines_for_duplication", DEFAULTS["min_lines_for_duplication"]
        )
        self.duplication_threshold: float = raw.get(
            "duplication_threshold", DEFAULTS["duplication_threshold"]
        )
        self.high_coupling_percentile: int = raw.get(
            "high_coupling_percentile", DEFAULTS["high_coupling_percentile"]
        )
        self.ignore_test_dirs: bool = raw.get(
            "ignore_test_dirs", DEFAULTS["ignore_test_dirs"]
        )
        self.max_modules_for_duplication: int = raw.get(
            "max_modules_for_duplication", DEFAULTS["max_modules_for_duplication"]
        )

    # ── Boundary helpers ──────────────────────────────────────────────────────

    def is_boundary_ignored(self, boundary: str) -> bool:
        if self.strict_mode:
            return False
        return boundary in self.ignore_boundaries

    def is_dependency_allowed(self, source_boundary: str, target_boundary: str) -> bool:
        allowed = self.allowed_dependencies.get(source_boundary, [])
        return target_boundary in allowed

    # ── Domain helpers ────────────────────────────────────────────────────────

    def has_domains(self) -> bool:
        """Return True if explicit domain config is present."""
        return bool(self.domains)

    def resolve_domain(self, module_name: str) -> str | None:
        """
        Map a module name to its configured domain.

        Uses fnmatch glob patterns (e.g. "billing.*" matches "billing.service").
        Returns None if no domain matches — module is ungrouped.
        """
        for domain, patterns in self.domains.items():
            for pattern in patterns:
                if fnmatch.fnmatch(module_name, pattern):
                    return domain
        return None

    def is_domain_dependency_allowed(self, source_domain: str, target_domain: str) -> bool:
        allowed = self.allowed_domain_dependencies.get(source_domain, [])
        return target_domain in allowed

    # ── Justification override helpers ────────────────────────────────────────

    def is_cycle_approved(self, cycle: list[str]) -> bool:
        """
        Return True if a detected cycle is in the approved_cycles list.

        Matching is set-based (order-independent): a cycle through
        [billing.service, auth.utils] matches regardless of entry point.
        """
        cycle_set = frozenset(cycle)
        for approved in self._approved_cycle_sets:
            if approved.issubset(cycle_set):
                return True
        return False

    def is_violation_approved(self, source_boundary: str, target_boundary: str) -> bool:
        """Return True if this cross-boundary import is in approved_violations."""
        return (source_boundary, target_boundary) in self._approved_violation_pairs

    def get_approved_cycle_label(self, cycle: list[str]) -> str:
        """Return the matching approved cycle as a readable string."""
        cycle_set = frozenset(cycle)
        for i, approved in enumerate(self._approved_cycle_sets):
            if approved.issubset(cycle_set):
                parts = self._approved_cycles_raw[i]
                return " ↔ ".join(parts)
        return " ↔ ".join(cycle)

    def to_dict(self) -> dict:
        return {
            "ignore_boundaries":           sorted(self.ignore_boundaries),
            "allowed_dependencies":        self.allowed_dependencies,
            "strict_mode":                 self.strict_mode,
            "domains":                     self.domains,
            "allowed_domain_dependencies": self.allowed_domain_dependencies,
            "approved_cycles":             self._approved_cycles_raw,
            "approved_violations":         self._approved_violations_raw,
            "min_lines_for_duplication":   self.min_lines_for_duplication,
            "duplication_threshold":       self.duplication_threshold,
            "high_coupling_percentile":    self.high_coupling_percentile,
            "ignore_test_dirs":            self.ignore_test_dirs,
            "max_modules_for_duplication": self.max_modules_for_duplication,
        }


def load_config(root_path: str) -> DriftConfig:
    config_path = Path(root_path) / CONFIG_FILENAME
    if not config_path.exists():
        return DriftConfig({})
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            print(f"  [WARN] {CONFIG_FILENAME} is not a JSON object — using defaults.")
            return DriftConfig({})
        return DriftConfig(raw)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [WARN] Could not parse {CONFIG_FILENAME}: {exc} — using defaults.")
        return DriftConfig({})
