"""
config.py — Drift Configuration Loader

Reads .driftconfig.json from the repository root.
Provides typed defaults when the file is absent or partially defined.

Schema:
{
    "ignore_boundaries": ["common", "utils", "shared", "core"],
    "allowed_dependencies": {
        "billing": ["auth"],
        "users": ["core"]
    },
    "strict_mode": false,
    "min_lines_for_duplication": 10,
    "duplication_threshold": 0.85,
    "high_coupling_percentile": 85,
    "ignore_test_dirs": true
}
"""

import json
from pathlib import Path

CONFIG_FILENAME = ".driftconfig.json"

# Boundaries that are intentionally shared across the codebase.
# Violations into/from these are suppressed by default.
DEFAULT_IGNORE_BOUNDARIES = {"shared", "common", "utils", "core", "lib", "helpers", "base"}

DEFAULTS = {
    "ignore_boundaries": list(DEFAULT_IGNORE_BOUNDARIES),
    "allowed_dependencies": {},
    "strict_mode": False,
    "min_lines_for_duplication": 10,
    "duplication_threshold": 0.85,
    "high_coupling_percentile": 85,
    "ignore_test_dirs": True,
    "max_modules_for_duplication": 200,
}


class DriftConfig:
    """
    Typed configuration object loaded from .driftconfig.json.

    All fields fall back to safe defaults when not specified.
    """

    def __init__(self, raw: dict) -> None:
        self.ignore_boundaries: set[str] = set(
            raw.get("ignore_boundaries", DEFAULTS["ignore_boundaries"])
        )
        self.allowed_dependencies: dict[str, list[str]] = raw.get(
            "allowed_dependencies", DEFAULTS["allowed_dependencies"]
        )
        self.strict_mode: bool = raw.get("strict_mode", DEFAULTS["strict_mode"])
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

    def is_boundary_ignored(self, boundary: str) -> bool:
        """Return True if this boundary should not generate violations."""
        if self.strict_mode:
            return False
        return boundary in self.ignore_boundaries

    def is_dependency_allowed(self, source_boundary: str, target_boundary: str) -> bool:
        """
        Return True if source_boundary is explicitly allowed to import target_boundary.

        Example config:
            "allowed_dependencies": { "billing": ["auth"] }
        means billing -> auth is permitted and should not be flagged.
        """
        allowed = self.allowed_dependencies.get(source_boundary, [])
        return target_boundary in allowed

    def to_dict(self) -> dict:
        """Serialise config for snapshot storage."""
        return {
            "ignore_boundaries": sorted(self.ignore_boundaries),
            "allowed_dependencies": self.allowed_dependencies,
            "strict_mode": self.strict_mode,
            "min_lines_for_duplication": self.min_lines_for_duplication,
            "duplication_threshold": self.duplication_threshold,
            "high_coupling_percentile": self.high_coupling_percentile,
            "ignore_test_dirs": self.ignore_test_dirs,
            "max_modules_for_duplication": self.max_modules_for_duplication,
        }


def load_config(root_path: str) -> DriftConfig:
    """
    Load .driftconfig.json from the repository root.

    Falls back to defaults gracefully if the file is absent or malformed.

    Args:
        root_path: Repository root directory path.

    Returns:
        DriftConfig instance with all fields populated.
    """
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
