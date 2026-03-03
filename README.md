# structural-drift-engine

> Architecture decays silently. This measures the rate.

A zero-dependency CLI tool and GitHub Action that monitors architectural health in Python codebases — across every pull request and over time.

No ML. No database. No external dependencies. Just your import graph and a number that moves in one direction if you stop paying attention.

---

## What it does

Runs on every PR and after every merge to `main`. Builds your import graph from AST, computes a **Drift Index** (0–100), compares against a stored baseline, and posts a structured report to the PR. After three runs it starts producing trend analytics — moving averages, slope, volatility warnings. After ten it tells you whether your architecture is improving or degrading over time.

```
🟡  Drift Index: 74  —  Mild Structural Risk  (↓ -6 from baseline)
     (Raw structural score: 71  ·  Smoothed with 7-run MA)

Drift Trend:
  84 → 83 → 82 → 80 → 79 → 77 → 74
  Trend: Gradual degradation (-10 over 7 runs)
  Volatility: Low  (σ=1.2)
  % vs moving avg:  coupling +18%
```

---

## Table of contents

- [How it works](#how-it-works)
- [Drift Index formula](#drift-index-formula)
- [Scoring stability](#scoring-stability)
- [Configuration](#configuration)
  - [Ignore boundaries](#ignore-boundaries)
  - [Domain modeling](#domain-modeling)
  - [Justification overrides](#justification-overrides)
- [Installation](#installation)
- [CLI usage](#cli-usage)
- [Docker usage](#docker-usage)
- [GitHub Action](#github-action)
- [History and trend analytics](#history-and-trend-analytics)
- [PR comment format](#pr-comment-format)
- [Baseline management](#baseline-management)
- [Running tests](#running-tests)
- [Limitations](#limitations)
- [Philosophy](#philosophy)

---

## How it works

1. **Scan** — finds all `.py` files, excludes test directories by default
2. **Parse** — extracts `import` and `from x import y` statements using the `ast` module
3. **Filter** — removes stdlib and third-party imports; only internal project modules appear in the graph
4. **Graph** — builds a directed dependency graph (nodes = modules, edges = imports)
5. **Detect** — finds circular dependencies (DFS), high-coupling modules (85th percentile), boundary violations (folder or domain), and duplicate modules (TF-IDF cosine similarity)
6. **Score** — computes the Drift Index with size normalization and optional smoothing
7. **Compare** — diffs against the saved baseline snapshot
8. **Trend** — computes moving average, slope, and volatility from `.drift_history.json`
9. **Report** — writes `drift_report.json` and optionally posts to PR

---

## Drift Index formula

The score starts at 100 and subtracts four penalties. All penalties are scaled by a **size normalization factor** to avoid penalizing small repos disproportionately.

```
size_scale = clamp(log(n_modules + 1) / log(31), 0.2, 1.0)

circular_penalty    = round(min(40, 15 × n_cycles) × size_scale)
coupling_penalty    = round(min(20, (n_flagged/n_total) × (avg_pct/100) × 40) × size_scale)
duplication_penalty = round(min(25, n_dup_pairs × 5) × size_scale)
complexity_penalty  = round(min(15, min(Δ_complexity/total, 0.30) × 20) × size_scale)

raw_score  = max(0, 100 − sum of penalties)
```

**Coupling penalty** uses a graduated formula — `(n_flagged / n_total)` ensures a single outlier module doesn't trigger the full penalty. One module crossing the percentile threshold in an 18-module repo scores 2 points, not 19.

**Complexity penalty** is delta-based, normalized against total codebase complexity. A +10 delta in a 1,000-point repo contributes 0 penalty. A +300 delta in the same repo contributes 6 points.

**First run:** complexity penalty is always suppressed (no baseline to delta against). Report is marked *Baseline initialized.*

### Severity labels

| Score | Label |
|---|---|
| 80–100 | Stable |
| 60–79 | Mild Structural Risk |
| 40–59 | Moderate Risk |
| 0–39 | High Risk |

---

## Scoring stability

Three mechanisms prevent score shock from incidental changes:

**1. Weighted moving average**

After the first run, the Drift Index blends the current raw score with the 5-run moving average:

```
drift_index = round(0.7 × raw_score + 0.3 × moving_average)
```

Small structural changes — adding one import, one module crossing the coupling percentile — produce ≤2 point fluctuations rather than 10–20 point cliffs.

**2. Per-run drop cap**

If the blended score drops more than 10 points vs the previous run, it is capped at `previous − 10`. A warning is added to the report:

```
⚠️  Large structural shift detected. Score drop capped at 10.
    Raw: 58, Smoothed: 61, Capped to: 72.
```

The true score approaches over subsequent runs. This prevents a single bad PR from triggering a false alarm while still tracking genuine degradation.

**3. Size normalization**

A 5-module repo that has one cycle gets a penalty of `round(15 × 0.27) = 4`, not 15. At 30+ modules the scale factor reaches 1.0 and penalties apply in full.

---

## Configuration

Create `.driftconfig.json` in the repository root. All fields are optional — the engine works without it.

```json
{
  "ignore_boundaries": ["shared", "utils", "common"],
  "allowed_dependencies": {
    "billing": ["auth"]
  },
  "strict_mode": false,

  "domains": {
    "billing":  ["billing.*", "invoicing.*"],
    "auth":     ["auth.*", "jwt.*"],
    "users":    ["users.*", "profiles.*"]
  },
  "allowed_domain_dependencies": {
    "billing": ["auth"]
  },

  "approved_cycles": [
    ["billing.service", "auth.utils"]
  ],
  "approved_violations": [
    { "source": "billing", "target": "auth" }
  ],

  "high_coupling_percentile": 85,
  "duplication_threshold": 0.85,
  "min_lines_for_duplication": 10,
  "max_modules_for_duplication": 200,
  "ignore_test_dirs": true
}
```

### Ignore boundaries

`ignore_boundaries` suppresses violations from or to shared packages that are legitimately imported across the codebase. Default list: `shared`, `common`, `utils`, `core`, `lib`, `helpers`, `base`.

`allowed_dependencies` whitelists specific cross-boundary imports without suppressing the entire boundary.

Set `strict_mode: true` to disable all suppression and report every violation.

### Domain modeling

By default, boundaries are inferred from top-level folder names. If your architecture doesn't map cleanly to folders — because `billing` logic lives in both `billing/` and `invoicing/` — use explicit domains.

```json
"domains": {
  "billing": ["billing.*", "invoicing.*"],
  "auth":    ["auth.*"]
}
```

Patterns use `fnmatch` glob syntax. When domains are defined, domain-based detection replaces folder-based detection entirely. Modules that don't match any domain are excluded from violation detection.

`allowed_domain_dependencies` whitelists known cross-domain imports. Allowed pairs appear in the report as *Approved Architectural Exceptions* rather than violations.

### Justification overrides

Intentional design choices — a deliberate cycle, a known boundary exception — shouldn't force you to disable the tool. Add them to the approved lists instead.

```json
"approved_cycles": [
  ["billing.service", "auth.utils"]
],
"approved_violations": [
  { "source": "billing", "target": "auth" }
]
```

Approved items are **removed from penalty calculation** and reported separately:

```
Approved Architectural Exceptions:
  ✓ billing.service ↔ auth.utils  (cycle — approved in config)
  ✓ billing → auth  (violation — approved in config)
```

Cycle matching is order-independent and entry-point-independent. A cycle detected as `[billing.service, auth.utils, billing.service]` matches the approval regardless of where the DFS entered.

---

## Installation

No pip install required. Copy the engine source into your repo or reference it as a GitHub Action.

**Required Python:** 3.11+  
**Dependencies:** none (stdlib only)

```
your-repo/
├── .github/
│   └── workflows/
│       └── drift-check.yml
├── structural_drift/
│   ├── main.py
│   ├── scanner.py
│   ├── graph_builder.py
│   ├── metrics.py
│   ├── drift.py
│   ├── drift_index.py
│   ├── complexity.py
│   ├── duplication.py
│   ├── history.py
│   ├── config.py
│   ├── snapshot.py
│   ├── utils.py
│   └── pr_comment.py
├── .driftconfig.json          ← optional
├── .drift_history.json        ← created automatically
└── .drift_baseline.json       ← created automatically (backward compat)
```

---

## CLI usage

**First run (initialises history):**
```bash
python main.py --path /path/to/repo
```

**Save snapshot after a deliberate refactor:**
```bash
python main.py --path . --save-snapshot
```

**Skip all history loading and saving:**
```bash
python main.py --path . --no-snapshot
```

**Post PR comment and fail CI if index drops > 8:**
```bash
python main.py --path . --pr-comment --max-drop 8
```

**All options:**

| Flag | Default | Description |
|---|---|---|
| `--path` | required | Repository root to analyse |
| `--save-snapshot` | false | Append to history and overwrite baseline |
| `--no-snapshot` | false | Skip all history I/O |
| `--output-env` | false | Write `.drift_env` for GitHub Actions |
| `--pr-comment` | false | Post/update PR comment via GitHub API |
| `--max-drop` | 5 | Max index drop before CI returns exit code 1 |

---

## Docker usage

```bash
# Build
docker build -t structural-drift .

# Analyse a local repo
docker run --rm -v /path/to/your/repo:/repo structural-drift --path /repo

# With flags
docker run --rm -v /path/to/your/repo:/repo structural-drift \
  --path /repo --save-snapshot
```

---

## GitHub Action

Add to `.github/workflows/drift-check.yml`:

```yaml
name: Structural Drift Check

on:
  push:
    branches: ["main", "master"]
  pull_request:
    branches: ["main", "master"]

jobs:
  drift-check:
    name: Analyse Architectural Drift
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Restore history from target branch
        run: |
          git show origin/${{ github.base_ref }}:.drift_history.json \
            > .drift_history.json 2>/dev/null || true
          git show origin/${{ github.base_ref }}:.drift_baseline.json \
            > .drift_baseline.json 2>/dev/null || true

      - name: Run Structural Drift Engine
        id: drift
        uses: ./
        with:
          path: "."
          fail_under: "60"
          save_snapshot: "false"
          upload_report: "true"

      - name: Post drift summary
        if: always()
        run: |
          echo "## Structural Drift" >> $GITHUB_STEP_SUMMARY
          echo "| Metric | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|--------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Drift Index | ${{ steps.drift.outputs.drift_score }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Score Delta | ${{ steps.drift.outputs.score_delta }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Cycles | ${{ steps.drift.outputs.cycles_count }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Violations | ${{ steps.drift.outputs.violations_count }} |" >> $GITHUB_STEP_SUMMARY

  update-baseline:
    name: Update Drift Baseline
    runs-on: ubuntu-latest
    needs: drift-check
    if: github.ref == 'refs/heads/main' && github.event_name == 'push'

    steps:
      - uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Run Drift Engine (save snapshot)
        uses: ./
        with:
          path: "."
          save_snapshot: "true"
          upload_report: "false"

      - name: Commit updated history
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add .drift_history.json .drift_baseline.json
          git diff --cached --quiet || git commit -m "chore: update drift baseline [skip ci]"
          git push
```

### Action inputs

| Input | Default | Description |
|---|---|---|
| `path` | `.` | Path to analyse, relative to workspace |
| `fail_under` | `60` | Exit code 1 if index falls below this absolute score |
| `save_snapshot` | `false` | Append run to history and save baseline |
| `upload_report` | `true` | Upload `drift_report.json` as a workflow artifact |

### Action outputs

| Output | Description |
|---|---|
| `drift_score` | Current Drift Index (0–100) |
| `score_delta` | Change vs baseline |
| `cycles_count` | Active (non-approved) cycles detected |
| `violations_count` | Active (non-approved) violations detected |

---

## History and trend analytics

The engine stores up to 50 runs in `.drift_history.json`. After 2+ runs it computes:

**Moving average** — 7-run rolling mean of the Drift Index. Used as the smoothing anchor for the weighted blend.

**Slope** — linear regression over the last 5 runs. Drives the trend label:

| Slope | Label |
|---|---|
| < −2.0 / run | Rapid degradation |
| −2.0 to −0.3 | Gradual degradation |
| −0.3 to +0.3 | Stable |
| +0.3 to +2.0 | Gradual improvement |
| > +2.0 / run | Rapid improvement |

**Volatility** — population standard deviation of the last 5 Drift Index values. If σ > 5, a warning is surfaced: `⚠️ Drift volatility high. Scoring instability detected.`

**% change analytics** — compares current coupling average, total complexity, and duplication count against the moving average. Values below 5% are suppressed. Capped at ±200% to prevent distortion from near-zero baselines.

```
% vs moving avg:  coupling +18%  ·  complexity +6%
```

Commit `.drift_history.json` to version control. The `update-baseline` job in the provided workflow handles this automatically on every merge to `main`.

---

## PR comment format

```
🟡 Structural Drift Report

  Drift Index   74 / 100  (raw: 71, smoothed with 7-run avg)
  Severity      Mild Structural Risk
  Delta         ↓ -6 from baseline

📈 Drift Trend
  84 → 83 → 82 → 80 → 79 → 77 → 74
  Trend: Gradual degradation (-10 over 7 runs)
  7-run average: 79.9
  Volatility: Low (σ=1.2)
  % vs moving avg: Coupling +18%

✅ Approved Architectural Exceptions
  billing.service ↔ auth.utils — cycle approved in config

🔴 Circular Dependencies (1)
  - payments.gateway → billing.models → payments.gateway

🟠 High Coupling Modules (2)
  - billing.service — 8 deps (94th pct)
  - auth.middleware — 6 deps (89th pct)

🟡 Duplicate Modules (3)
  - run_clm_no_trainer ↔ run_mlm_no_trainer (97%)

🔵 Complexity
  Increased in 2 module(s):
  - billing.service (+14)
  - payments.gateway (+9)

Boundary Violations (2)
  - users.profile → auth.middleware
  - payments.gateway → users.profile

Why this matters:
  - Circular dependencies increase change fragility — modifying one module
    can break any module in the cycle.
  - Cross-boundary imports erode module ownership and create hidden coupling
    between teams.

[Drift Index breakdown]
  Component           Penalty   Detail
  Circular deps         −9      1 cycle × 15, cap 40
  High coupling         −4      (2/18) × (91st pct) × 40, cap 20
  Duplication           −8      3 pairs × 5, cap 25
  Complexity delta      −4      +23 delta, cap 15
  Size scale            0.94×   17 modules
```

---

## Baseline management

**`.drift_history.json`** is the primary persistence file. It stores the last 50 run records used for trend analytics, moving averages, and % change comparisons. Commit this file.

**`.drift_baseline.json`** stores the full snapshot of the last saved run — graph, coupling metrics, cycles, violations, complexity map. Used for per-item diffing (new cycles, resolved violations, etc.). Written automatically alongside history. Commit this file.

**When to save a new snapshot manually:**

- After a deliberate refactor that improves structure
- When onboarding the tool to a codebase where existing violations are accepted
- After updating `.driftconfig.json` to add approved exceptions

```bash
python main.py --path . --save-snapshot
git add .drift_history.json .drift_baseline.json
git commit -m "chore: update drift baseline after auth refactor"
```

**In CI:** the provided `update-baseline` workflow job handles this automatically on every merge to `main`.

---

## Running tests

**Phase 3 stress tests** (5 scenarios, 23 assertions):
```bash
python tests/stress_test_v3.py
```

**Phase 2 regression tests** (6 scenarios, 24 assertions):
```bash
python tests/stress_test_v2.py
```

Expected output:
```
  1. Gradual Coupling Increase (5 simulated runs)
    ✓  5 runs recorded
    ✓  Score non-increasing as coupling grows
    ✓  Moving average computed
    Scores: 100 → 100 → 100 → 99 → 99
    Trend: Gradual degradation (-1 over 5 runs)  slope=-0.3

  2. Approved Cycle Exception
    ✓  Cycle detected before approval
    ✓  Active cycles = 0 after approval
    ✓  Score improves with approval

  3. Drop Cap — Sudden Major Change
    ✓  Raw score dropped significantly
    ✓  Drop-cap applied (final >= prev - 10)
    ✓  drop_capped flag is True
    Clean: 100  Raw after catastrophe: 73  Capped to: 90

  4. Domain-Based Boundary Detection
    ✓  Within-domain import NOT flagged
    ✓  Cross-domain import IS flagged
    ✓  allowed_domain_dependency suppresses violation

  5. Multi-Run History + % Change Analytics
    ✓  10 runs in history
    ✓  Moving average reflects recent stable runs
    ✓  coupling_pct shows significant increase

  23 passed  |  0 failed  |  0.19s
```

---

## Limitations

- **Python only.** The AST parser is Python-specific.
- **Static analysis only.** Dynamic imports (`importlib`, `__import__`, lazy loaders) are not traced.
- **Relative imports partially supported.** Resolved relative to the file's package when possible; skipped when ambiguous.
- **Edge count gaps in large frameworks.** Repos that use `__init__.py` re-exports or lazy import patterns (HuggingFace Transformers, Flask) will show sparse edge counts. The structural signals that don't depend on edges (complexity, duplication, per-file cycles) remain accurate.
- **Duplication is semantic, not syntactic.** The TF-IDF similarity model operates on AST token sets — function names, argument names, import paths — not raw text. Template files that are intentionally similar (e.g. Django migrations, training script scaffolds) will be flagged. Add them to `approved_violations` or scope your `--path` to exclude them.
- **O(n²) duplication capped at 200 modules.** Configurable via `max_modules_for_duplication`.

---

## Breaking changes from v2

| Area | v2 | v3 |
|---|---|---|
| `detect_boundary_violations()` | returns `list` | returns `(violations, approved)` tuple |
| `detect_cycles()` | all cycles used for scoring | use `partition_cycles()` to split active vs approved |
| Persistence | `snapshot.py` + `.drift_baseline.json` | `history.py` + `.drift_history.json` (baseline still written) |
| `calculate_drift_index()` | no smoothing params | accepts `moving_average`, `previous_index`, `total_modules` |
| Score formula | raw penalties | size-scaled + smoothed |

`.drift_baseline.json` is still written on every save for backward compatibility with tooling that reads it directly.

---

## Philosophy

Architecture is not a property of code. It is a property of the rate of change in code.

A codebase with 20 violations that hasn't changed in two years is more predictable than one with 3 violations and a PR merged every hour. The score is not a grade — it's a trend signal. The number you should watch is the slope.

The tool is deliberately opinionated about what it refuses to do:

- No ML-based anomaly detection — every penalty is a formula you can read and argue with
- No web server or dashboard — the report lives in the PR where the decision is being made
- No external dependencies — it runs anywhere Python 3.11 runs, in any CI, with no setup

A tool that requires infrastructure becomes part of the infrastructure. This one just runs.
