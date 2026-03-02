# structural-drift-engine

> We measure structural entropy in high-velocity codebases.

---

A command-line tool and GitHub Action that detects architectural drift in Python repositories. It parses your import graph, identifies circular dependencies and boundary violations, computes a normalized health score, and posts a structured report directly to your pull requests.

No ML. No database. No external dependencies. Just your code and a number.

---

## What problem it solves

Architecture decays silently. A codebase that scores 95 today drifts to 70 over six months of feature pressure — not through any single bad decision, but through hundreds of small ones: a shortcut import here, a cross-boundary reference there. By the time it's painful, it's structural.

`structural-drift-engine` makes drift visible and quantifiable before it compounds. When it runs on every pull request, architectural violations become as visible as failing tests.

---

## How it works

1. **Scans** your repository recursively for `.py` files
2. **Parses** each file with the `ast` module to extract all `import` and `from x import y` statements
3. **Builds** a module-level dependency graph (nodes = modules, edges = imports)
4. **Detects** circular dependencies via DFS, and cross-boundary violations based on top-level folder structure
5. **Scores** the codebase on a normalized 0–100 scale
6. **Compares** against a saved baseline to compute score delta
7. **Posts** a structured comment to your pull request

---

## Scoring

All penalties are normalized against repo size. A 500-module codebase with 5 cycles is structurally healthier than a 20-module codebase with the same 5 cycles.

```
cycle_penalty     = min(40, ceil((n_cycles     / n_modules) × 50))
violation_penalty = min(35, ceil((n_violations / n_edges  ) × 50))
coupling_penalty  = min(20, ceil((n_hc_modules / n_modules) × 20))

drift_score = max(0, 100 − cycle_penalty − violation_penalty − coupling_penalty)
```

| Signal | What it measures |
|---|---|
| Circular dependency | Module A → B → A (detected via DFS) |
| Boundary violation | Module in `billing/` importing from `auth/` |
| High coupling | Module with more dependencies than the configured threshold |

**Boundaries** are inferred from top-level folder structure. If your repo has `billing/`, `auth/`, and `users/`, those are your three boundaries. Any import crossing them is a violation.

---

## Installation

No pip install. Copy the engine files into your repo or reference the GitHub Action.

```
your-repo/
├── .github/
│   └── workflows/
│       └── drift-check.yml     ← workflow file
├── structural_drift/            ← engine source (copy from this repo)
│   ├── main.py
│   ├── scanner.py
│   ├── graph_builder.py
│   ├── metrics.py
│   ├── drift.py
│   ├── snapshot.py
│   ├── utils.py
│   └── pr_comment.py
└── .drift_baseline.json         ← created automatically on first run
```

---

## CLI usage

**Basic scan:**
```bash
python structural_drift/main.py --path /path/to/repo
```

**Custom high-coupling threshold:**
```bash
python structural_drift/main.py --path . --threshold 7
```

**Save as new baseline:**
```bash
python structural_drift/main.py --path . --save-snapshot
```

**Skip snapshot entirely:**
```bash
python structural_drift/main.py --path . --no-snapshot
```

**All options:**
```
--path             Repository root to analyse (required)
--threshold        Dependency count threshold for high-coupling (default: 10)
--save-snapshot    Overwrite the baseline snapshot
--no-snapshot      Skip snapshot load/save
--output-env       Write outputs to .drift_env (used by GitHub Actions)
--pr-comment       Post/update a PR comment via GitHub REST API
--drift-threshold  Min acceptable delta before CI fails (default: -5)
```

---

## Docker usage

```bash
# Build
docker build -t structural-drift .

# Analyse a local repo
docker run --rm -v /path/to/your/repo:/repo structural-drift

# With flags
docker run --rm -v /path/to/your/repo:/repo structural-drift --threshold 7 --no-snapshot
```

---

## GitHub Action

Add to `.github/workflows/drift-check.yml`:

```yaml
name: Structural Drift Check

on:
  pull_request:
    branches: ["main"]
    paths: ["**.py"]

jobs:
  drift-check:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Restore baseline from target branch
        run: |
          git show origin/${{ github.base_ref }}:.drift_baseline.json \
            > .drift_baseline.json 2>/dev/null || true

      - name: Run Structural Drift
        uses: your-org/structural-drift-engine@v1
        with:
          path: "."
          threshold: "10"
          drift_threshold: "-5"    # fail PR if score drops > 5 points
          post_pr_comment: "true"
```

### Action inputs

| Input | Default | Description |
|---|---|---|
| `path` | `.` | Path to analyse, relative to workspace |
| `threshold` | `10` | High-coupling dependency threshold |
| `drift_threshold` | `-5` | Min score delta before CI fails |
| `save_snapshot` | `false` | Save current run as new baseline |
| `post_pr_comment` | `true` | Post/update PR comment |
| `upload_report` | `true` | Upload `drift_report.json` as artifact |

### Action outputs

| Output | Description |
|---|---|
| `drift_score` | Current score (0–100) |
| `score_delta` | Change vs baseline |
| `cycles_count` | Number of cycles detected |
| `violations_count` | Number of boundary violations |

---

## Example PR comment

```
🟡 Structural Drift Report

  Drift Score      74 / 100
  Delta            ↓ -8 from baseline  ⚠️ threshold breached
  Modules          43
  Total Edges      61
  Avg Dependencies 1.42

Circular Dependencies
  2 detected
  - billing.service → auth.utils → billing.service
  - payments.gateway → billing.models → users.profile → payments.gateway

Boundary Violations
  6 detected
  - auth.utils → billing.service
  - billing.models → users.profile
  ...

High-Coupling Modules
  None detected ✓

Penalty breakdown
  Circular dependencies   2   4.65%    −3
  Boundary violations     6   9.84%    −5
  High-coupling modules   0   0.00%    −0
```

---

## Running tests

**Unit tests with cycle detection validation:**
```bash
python tests/generate_test_repo.py
```

**Stress tests against 3 repo archetypes:**
```bash
python tests/stress_test.py
```

Expected output:
```
  Clean Modular             ████████████████████  100/100
  Messy Monolith            ███████████░░░░░░░░░   59/100
  Circular Injection        ███████████░░░░░░░░░   58/100

  19 passed  |  0 failed
```

---

## Baseline management

On the first run, the engine saves `.drift_baseline.json` at the repo root. Subsequent runs compare against it and output a score delta.

**When to update the baseline:**
- After a deliberate refactor that improves structure
- When onboarding the tool to a codebase that already has violations you accept for now

**Recommended workflow:**
- The GitHub Action auto-saves the baseline on every merge to `main` (see the `update-baseline` job in the provided workflow)
- Commit `.drift_baseline.json` to version control so PRs always diff against the agreed-upon state of `main`

---

## Limitations

- **Python only.** Other languages are not supported.
- **Static analysis only.** Dynamic imports (`importlib`, `__import__`) are not detected.
- **Boundaries are folder-based.** There is no config file for exceptions yet. Shared packages that all boundaries legitimately import from will generate violations if they are inside a top-level folder.
- **No relative import tracking.** Relative imports (`from . import x`) are skipped.
- **No monorepo awareness.** Sub-projects within a monorepo are treated as a single codebase.

---

## Philosophy

Architecture is not a property of code. It is a property of the rate of change in code. A codebase with 20 violations that hasn't changed in two years is more predictable than one with 3 violations and a PR merged every hour.

This tool measures structural entropy — the tendency of import graphs to collapse toward a tangled equilibrium under development pressure. The score is not a grade. It is a signal.

Use it to track direction, not destination.

---

## Roadmap

- `--fail-under <score>` — hard gate on absolute score (vs delta)
- `.driftignore` — exclude known-intentional cross-boundary imports
- Multi-project monorepo support with per-package scoring
- GitHub Actions PR annotation (inline file-level comments)
