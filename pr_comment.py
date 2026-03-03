"""
pr_comment.py — GitHub PR Comment Bot  (Phase 3)

New sections:
    - Trend analysis (moving avg, slope, volatility)
    - % change analytics vs moving average
    - Approved Architectural Exceptions
    - Drop-cap / smoothing transparency notices
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

COMMENT_MARKER   = "<!-- structural-drift-engine-v2 -->"
MAX_COMMENT_LENGTH = 8000


def _api_request(method, url, token, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload else None
    headers = {
        "Authorization":       f"Bearer {token}",
        "Accept":              "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type":        "application/json",
        "User-Agent":          "structural-drift-engine",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {exc.code}: {detail}") from exc


def _find_existing_comment(token, repo, pr_number):
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    try:
        comments = _api_request("GET", url, token)
    except RuntimeError as exc:
        print(f"  [WARN] Could not fetch comments: {exc}")
        return None
    if not isinstance(comments, list):
        return None
    for c in comments:
        if COMMENT_MARKER in c.get("body", ""):
            return c["id"]
    return None


# ── Section builders ──────────────────────────────────────────────────────────

def _severity_icon(score: int) -> str:
    if score >= 80:   return "🟢"
    if score >= 60:   return "🟡"
    if score >= 40:   return "🟠"
    return "🔴"


def _delta_line(index_delta, max_drop):
    if index_delta is None:
        return "_No baseline — first run establishes the baseline._"
    if index_delta > 0:
        return f"↑ **+{index_delta}** from baseline"
    if index_delta < 0:
        breach  = abs(index_delta) > max_drop
        suffix  = " ⚠️ **threshold breached**" if breach else ""
        return f"↓ **{index_delta}** from baseline{suffix}"
    return "→ No change from baseline"


def _section_trend(trend_analytics: dict) -> str:
    n = trend_analytics.get("run_count", 0)
    if n < 2:
        return ""

    ts    = trend_analytics.get("trend_string", "")
    label = trend_analytics.get("trend_label", "")
    vol   = trend_analytics.get("volatility", 0.0)
    high_vol = trend_analytics.get("high_volatility", False)
    ma    = trend_analytics.get("moving_avg")
    pct   = trend_analytics.get("pct_changes", {})

    lines = ["**📈 Drift Trend**"]
    if ts:
        lines.append(f"`{ts}`")
    if label:
        lines.append(f"Trend: {label}")
    if ma is not None:
        lines.append(f"7-run average: **{ma}**")

    vol_label = "⚠️ High" if high_vol else ("Moderate" if vol >= 2.0 else "Low")
    lines.append(f"Volatility: {vol_label} (σ={vol})")

    # % change rows
    pct_lines = []
    if "coupling_pct" in pct and abs(pct["coupling_pct"]) >= 5:
        pct_lines.append(f"Coupling: {_pct_str(pct['coupling_pct'])} vs avg")
    if "complexity_pct" in pct and abs(pct["complexity_pct"]) >= 5:
        pct_lines.append(f"Complexity: {_pct_str(pct['complexity_pct'])} vs avg")
    if "duplication_pct" in pct and abs(pct["duplication_pct"]) >= 5:
        pct_lines.append(f"Duplication: {_pct_str(pct['duplication_pct'])} vs avg")
    if pct_lines:
        lines.append("% vs moving avg: " + "  ·  ".join(pct_lines))

    return "\n".join(lines) + "\n"


def _pct_str(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"**{sign}{pct}%**"


def _section_approved_exceptions(approved_cycles: list, approved_exceptions: list) -> str:
    if not approved_cycles and not approved_exceptions:
        return ""
    lines = ["**✅ Approved Architectural Exceptions**"]
    for ac in approved_cycles:
        lines.append(f"- `{ac['label']}` — cycle approved in config")
    for av in approved_exceptions:
        reason = av.get("reason", "approved")
        lines.append(f"- `{av['source']}` → `{av['target']}` — {reason}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _section_cycles(cycles):
    if not cycles:
        return "🔴 **Circular Dependencies** — ✓ None\n"
    lines = [f"🔴 **Circular Dependencies** ({len(cycles)})"]
    for c in cycles[:8]:
        lines.append(f"- `{'` → `'.join(c)}`")
    if len(cycles) > 8:
        lines.append(f"- _...and {len(cycles) - 8} more_")
    return "\n".join(lines) + "\n"


def _section_coupling(modules):
    if not modules:
        return "🟠 **High Coupling Modules** — ✓ None\n"
    lines = [f"🟠 **High Coupling Modules** ({len(modules)})"]
    for m in modules[:8]:
        lines.append(f"- `{m['module']}` — {m['dependency_count']} deps ({m['percentile']}th pct)")
    if len(modules) > 8:
        lines.append(f"- _...and {len(modules) - 8} more_")
    return "\n".join(lines) + "\n"


def _section_duplicates(pairs):
    if not pairs:
        return "🟡 **Duplicate Modules** — ✓ None\n"
    lines = [f"🟡 **Duplicate Modules** ({len(pairs)})"]
    for p in pairs[:6]:
        lines.append(f"- `{p['module_a']}` ↔ `{p['module_b']}` ({int(p['similarity']*100)}%)")
    if len(pairs) > 6:
        lines.append(f"- _...and {len(pairs) - 6} more_")
    return "\n".join(lines) + "\n"


def _section_complexity(complexity_summary, comparison):
    increases = (comparison or {}).get("complexity_increases", {})
    lines = ["🔵 **Complexity**"]
    if increases:
        lines.append(f"_Increased in {len(increases)} module(s):_")
        for mod, delta in sorted(increases.items(), key=lambda x: -x[1])[:6]:
            lines.append(f"- `{mod}` (+{delta})")
    else:
        top = complexity_summary.get("top_complex_modules", [])
        if top:
            lines.append(f"_No changes. Top: `{top[0]['module']}` (score {top[0]['complexity_score']})_")
        else:
            lines.append("✓ No complexity changes")
    return "\n".join(lines) + "\n"


def _section_changes(comparison):
    if not comparison:
        return ""
    parts = []
    if comparison.get("new_cycles"):
        parts.append(f"**+{len(comparison['new_cycles'])} cycle(s)**")
    if comparison.get("resolved_cycles"):
        parts.append(f"**−{len(comparison['resolved_cycles'])} cycle(s) ✓**")
    if comparison.get("new_violations"):
        parts.append(f"**+{len(comparison['new_violations'])} violation(s)**")
    if comparison.get("resolved_violations"):
        parts.append(f"**−{len(comparison['resolved_violations'])} violation(s) ✓**")
    if comparison.get("new_duplicates"):
        parts.append(f"**+{len(comparison['new_duplicates'])} duplicate pair(s)**")
    if not parts:
        return ""
    return "**Changes this PR:** " + " · ".join(parts) + "\n"


WHY_THIS_MATTERS = {
    "cycles":      "Circular dependencies increase change fragility — modifying one module can break any module in the cycle.",
    "coupling":    "High coupling makes modules harder to test, replace, or understand in isolation.",
    "duplication": "Duplicate logic diverges over time. Bugs fixed in one copy silently persist in the other.",
    "violations":  "Cross-boundary imports erode module ownership and create hidden coupling between teams.",
}


def _section_why_this_matters(cycles, high_coupling, duplicates, violations):
    reasons = []
    if cycles:         reasons.append(WHY_THIS_MATTERS["cycles"])
    if high_coupling:  reasons.append(WHY_THIS_MATTERS["coupling"])
    if duplicates:     reasons.append(WHY_THIS_MATTERS["duplication"])
    if violations:     reasons.append(WHY_THIS_MATTERS["violations"])
    if not reasons:
        return ""
    lines = ["**Why this matters:**"]
    for r in reasons:
        lines.append(f"- {r}")
    lines.append("")
    return "\n".join(lines) + "\n"


# ── Main builder ──────────────────────────────────────────────────────────────

def build_comment_body(
    drift_index: int,
    severity: str,
    index_delta: int | None,
    cycles: list,
    approved_cycles: list,
    violations: list,
    approved_exceptions: list,
    coupling_metrics: dict,
    duplicate_pairs: list,
    complexity_summary: dict,
    drift_result: dict,
    comparison: dict | None,
    trend_analytics: dict,
    first_run: bool = False,
    max_drop: int = 5,
) -> str:
    icon  = _severity_icon(drift_index)
    delta = _delta_line(index_delta, max_drop)
    now   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    first_run_notice = (
        "\n> ℹ️ **Baseline initialized.** No delta comparison available. "
        "Complexity penalty suppressed on first run.\n"
        if first_run else ""
    )

    drop_cap_notice = ""
    if drift_result.get("drop_capped"):
        drop_cap_notice = f"\n> ⚠️ **Large structural shift detected.** {drift_result['drop_cap_message']}\n"

    raw_score = drift_result.get("raw_score", drift_index)
    smoothing_note = ""
    if drift_result.get("smoothing_applied"):
        smoothing_note = f" _(raw: {raw_score}, smoothed with 7-run avg)_"

    comp = drift_result.get("components", {})
    cp = comp.get("circular_penalty", {})
    kp = comp.get("coupling_penalty", {})
    dp = comp.get("duplication_penalty", {})
    xp = comp.get("complexity_penalty", {})
    scale = drift_result.get("size_scale", 1.0)

    body = f"""{COMMENT_MARKER}
## {icon} Structural Drift Report
{first_run_notice}{drop_cap_notice}
| | |
|---|---|
| **Drift Index** | `{drift_index} / 100`{smoothing_note} |
| **Severity** | **{severity}** |
| **Delta** | {delta} |
| **Modules** | {coupling_metrics.get('total_modules', '?')} · {coupling_metrics.get('total_edges', '?')} edges |

{_section_changes(comparison)}
{_section_trend(trend_analytics)}
{_section_approved_exceptions(approved_cycles, approved_exceptions)}
---

{_section_cycles(cycles)}
{_section_coupling(coupling_metrics.get('high_coupling_modules', []))}
{_section_duplicates(duplicate_pairs)}
{_section_complexity(complexity_summary, comparison)}

---

### Boundary Violations ({len(violations)})
"""

    if violations:
        for v in violations[:12]:
            body += f"- `{v['source']}` → `{v['target']}`\n"
        if len(violations) > 12:
            body += f"- _...and {len(violations) - 12} more_\n"
    else:
        body += "✓ None detected\n"

    body += f"""
{_section_why_this_matters(cycles, coupling_metrics.get('high_coupling_modules', []), duplicate_pairs, violations)}
<details>
<summary>Drift Index breakdown  (size scale: {scale}×)</summary>

| Component | Penalty | Detail |
|---|---|---|
| Circular dependencies | −{cp.get('value', 0)} | {cp.get('count', 0)} cycle(s) × 15, cap 40 |
| High coupling | −{kp.get('value', 0)} | avg {kp.get('avg_percentile', 0)}th pct |
| Duplication | −{dp.get('value', 0)} | {dp.get('count', 0)} pair(s) × 5, cap 25 |
| Complexity delta | {'-' + str(xp.get('value', 0)) if not first_run else '—'} | {'suppressed on first run' if first_run else '+' + str(xp.get('total_positive_delta', 0)) + ' total delta'} |

</details>

<sub>structural-drift-engine · {now}</sub>
"""

    if len(body) > MAX_COMMENT_LENGTH:
        body = body[:MAX_COMMENT_LENGTH - 100] + "\n\n_[report truncated]_\n"

    return body


def post_pr_comment(
    drift_index: int,
    severity: str,
    index_delta: int | None,
    cycles: list,
    approved_cycles: list,
    violations: list,
    approved_exceptions: list,
    coupling_metrics: dict,
    duplicate_pairs: list,
    complexity_summary: dict,
    drift_result: dict,
    comparison: dict | None,
    trend_analytics: dict,
    first_run: bool = False,
    max_drop: int = 5,
) -> bool:
    token  = os.environ.get("GITHUB_TOKEN", "")
    repo   = os.environ.get("GITHUB_REPOSITORY", "")
    pr_str = os.environ.get("PR_NUMBER", "")

    if not all([token, repo, pr_str]):
        print("  [INFO] GITHUB env vars not set — skipping PR comment.")
        return False

    try:
        pr_number = int(pr_str)
    except ValueError:
        print(f"  [WARN] Invalid PR_NUMBER: {pr_str!r}")
        return False

    body = build_comment_body(
        drift_index=drift_index, severity=severity, index_delta=index_delta,
        cycles=cycles, approved_cycles=approved_cycles,
        violations=violations, approved_exceptions=approved_exceptions,
        coupling_metrics=coupling_metrics, duplicate_pairs=duplicate_pairs,
        complexity_summary=complexity_summary, drift_result=drift_result,
        comparison=comparison, trend_analytics=trend_analytics,
        first_run=first_run, max_drop=max_drop,
    )

    existing_id = _find_existing_comment(token, repo, pr_number)

    try:
        if existing_id:
            url = f"https://api.github.com/repos/{repo}/issues/comments/{existing_id}"
            _api_request("PATCH", url, token, {"body": body})
            print(f"  PR comment updated (id={existing_id})")
        else:
            url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
            _api_request("POST", url, token, {"body": body})
            print(f"  PR comment posted on PR #{pr_number}")
    except RuntimeError as exc:
        print(f"  [WARN] Failed to post PR comment: {exc}")
        return False

    if index_delta is not None and abs(index_delta) > max_drop and index_delta < 0:
        print(f"  [FAIL] Index delta {index_delta} breaches max-drop of {max_drop}.")
        return True

    return False
