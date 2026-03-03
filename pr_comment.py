"""
pr_comment.py — GitHub PR Comment Bot (v2)

Improved comment format with:
- Emoji severity indicators
- Drift Index prominently featured
- Duplicate module detection section
- Complexity increase section
- Actionable, concise layout
- 8000 char cap to avoid GitHub limits
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

COMMENT_MARKER = "<!-- structural-drift-engine-v2 -->"
MAX_COMMENT_LENGTH = 8000


def _api_request(method: str, url: str, token: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "structural-drift-engine",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {exc.code} on {method} {url}: {detail}") from exc


def _find_existing_comment(token: str, repo: str, pr_number: int) -> int | None:
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments?per_page=100"
    try:
        comments = _api_request("GET", url, token)
    except RuntimeError as exc:
        print(f"  [WARN] Could not fetch comments: {exc}")
        return None
    if not isinstance(comments, list):
        return None
    for comment in comments:
        if COMMENT_MARKER in comment.get("body", ""):
            return comment["id"]
    return None


def _severity_icon(score: int) -> str:
    if score >= 80:
        return "🟢"
    elif score >= 60:
        return "🟡"
    elif score >= 40:
        return "🟠"
    return "🔴"


def _delta_line(index_delta: int | None, max_drop: int) -> str:
    if index_delta is None:
        return "_No baseline — first run establishes the baseline._"
    if index_delta > 0:
        return f"↑ **+{index_delta}** from baseline"
    if index_delta < 0:
        breach = abs(index_delta) > max_drop
        suffix = " ⚠️ **threshold breached**" if breach else ""
        return f"↓ **{index_delta}** from baseline{suffix}"
    return "→ No change from baseline"


def _section_cycles(cycles: list) -> str:
    if not cycles:
        return "🔴 **Circular Dependencies** — ✓ None detected\n"
    lines = [f"🔴 **Circular Dependencies** ({len(cycles)})"]
    for c in cycles[:8]:
        lines.append(f"- `{'` → `'.join(c)}`")
    if len(cycles) > 8:
        lines.append(f"- _...and {len(cycles) - 8} more_")
    return "\n".join(lines) + "\n"


def _section_coupling(modules: list) -> str:
    if not modules:
        return "🟠 **High Coupling Modules** — ✓ None detected\n"
    lines = [f"🟠 **High Coupling Modules** ({len(modules)})"]
    for m in modules[:8]:
        lines.append(f"- `{m['module']}` — {m['dependency_count']} deps ({m['percentile']}th percentile)")
    if len(modules) > 8:
        lines.append(f"- _...and {len(modules) - 8} more_")
    return "\n".join(lines) + "\n"


def _section_duplicates(pairs: list) -> str:
    if not pairs:
        return "🟡 **Duplicate Modules** — ✓ None detected\n"
    lines = [f"🟡 **Duplicate Modules** ({len(pairs)})"]
    for p in pairs[:6]:
        pct = int(p["similarity"] * 100)
        lines.append(f"- `{p['module_a']}` ↔ `{p['module_b']}` ({pct}% similar)")
    if len(pairs) > 6:
        lines.append(f"- _...and {len(pairs) - 6} more_")
    return "\n".join(lines) + "\n"


def _section_complexity(complexity_summary: dict, comparison: dict | None) -> str:
    increases = (comparison or {}).get("complexity_increases", {})
    top_complex = complexity_summary.get("top_complex_modules", [])

    lines = ["🔵 **Complexity**"]

    if increases:
        lines.append(f"_Increased in {len(increases)} module(s) this PR:_")
        for mod, delta in sorted(increases.items(), key=lambda x: -x[1])[:6]:
            lines.append(f"- `{mod}` (+{delta})")
    elif top_complex:
        lines.append(f"_Top complex modules (no change from baseline):_")
        for m in top_complex[:3]:
            lines.append(f"- `{m['module']}` — score {m['complexity_score']}")
    else:
        lines.append("✓ No significant complexity changes")

    return "\n".join(lines) + "\n"


def _section_changes(comparison: dict) -> str:
    parts = []

    if comparison.get("new_cycles"):
        parts.append(f"**+{len(comparison['new_cycles'])} new cycle(s)**")
    if comparison.get("resolved_cycles"):
        parts.append(f"**−{len(comparison['resolved_cycles'])} cycle(s) resolved ✓**")
    if comparison.get("new_violations"):
        parts.append(f"**+{len(comparison['new_violations'])} new violation(s)**")
    if comparison.get("resolved_violations"):
        parts.append(f"**−{len(comparison['resolved_violations'])} violation(s) resolved ✓**")
    if comparison.get("new_duplicates"):
        parts.append(f"**+{len(comparison['new_duplicates'])} new duplicate pair(s)**")

    if not parts:
        return ""
    return "**Changes this PR:** " + " · ".join(parts) + "\n"


WHY_THIS_MATTERS = {
    "cycles":      "Circular dependencies increase change fragility — modifying one module can break any module in the cycle.",
    "coupling":    "High coupling makes modules harder to test, replace, or understand in isolation.",
    "duplication": "Duplicate logic diverges over time. Bugs fixed in one copy silently persist in the other.",
    "violations":  "Cross-boundary imports erode module ownership and create hidden coupling between teams.",
}


def _section_why_this_matters(
    cycles: list,
    high_coupling: list,
    duplicates: list,
    violations: list,
) -> str:
    """
    Emit a short educational 'Why This Matters' block for whichever
    signals are actually present. Empty if everything is clean.
    """
    reasons = []
    if cycles:
        reasons.append(WHY_THIS_MATTERS["cycles"])
    if high_coupling:
        reasons.append(WHY_THIS_MATTERS["coupling"])
    if duplicates:
        reasons.append(WHY_THIS_MATTERS["duplication"])
    if violations:
        reasons.append(WHY_THIS_MATTERS["violations"])

    if not reasons:
        return ""

    lines = ["**Why this matters:**"]
    for r in reasons:
        lines.append(f"- {r}")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_comment_body(
    drift_index: int,
    severity: str,
    index_delta: int | None,
    cycles: list,
    violations: list,
    coupling_metrics: dict,
    duplicate_pairs: list,
    complexity_summary: dict,
    drift_components: dict,
    comparison: dict | None,
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

    comp = drift_components
    cp = comp.get("circular_penalty", {})
    kp = comp.get("coupling_penalty", {})
    dp = comp.get("duplication_penalty", {})
    xp = comp.get("complexity_penalty", {})

    body = f"""{COMMENT_MARKER}
## {icon} Structural Drift Report
{first_run_notice}
| | |
|---|---|
| **Drift Index** | `{drift_index} / 100` |
| **Severity** | **{severity}** |
| **Delta** | {delta} |
| **Modules** | {coupling_metrics.get('total_modules', '?')} · {coupling_metrics.get('total_edges', '?')} edges |

{_section_changes(comparison) if comparison else ""}
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
<summary>Drift Index breakdown</summary>

| Component | Penalty | Detail |
|---|---|---|
| Circular dependencies | −{cp.get('value', 0)} | {cp.get('count', 0)} cycle(s) × 15, cap 40 |
| High coupling | −{kp.get('value', 0)} | avg {kp.get('avg_percentile', 0)}th percentile, cap 20 |
| Duplication | −{dp.get('value', 0)} | {dp.get('count', 0)} pair(s) × 5, cap 25 |
| Complexity delta | {'-' + str(xp.get('value', 0)) if not first_run else '—'} | {'suppressed on first run' if first_run else '+' + str(xp.get('total_positive_delta', 0)) + ' total delta, cap 15'} |

</details>

<sub>structural-drift-engine · {now}</sub>
"""

    # Hard cap to stay within GitHub's comment size limits
    if len(body) > MAX_COMMENT_LENGTH:
        body = body[:MAX_COMMENT_LENGTH - 100] + "\n\n_[report truncated]_\n"

    return body


def post_pr_comment(
    drift_index: int,
    severity: str,
    index_delta: int | None,
    cycles: list,
    violations: list,
    coupling_metrics: dict,
    duplicate_pairs: list,
    complexity_summary: dict,
    drift_components: dict,
    comparison: dict | None,
    first_run: bool = False,
    max_drop: int = 5,
) -> bool:
    """
    Post or update a drift report comment on the current GitHub PR.

    Returns True if threshold was breached (caller should exit 1).
    """
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
        drift_index=drift_index,
        severity=severity,
        index_delta=index_delta,
        cycles=cycles,
        violations=violations,
        coupling_metrics=coupling_metrics,
        duplicate_pairs=duplicate_pairs,
        complexity_summary=complexity_summary,
        drift_components=drift_components,
        comparison=comparison,
        first_run=first_run,
        max_drop=max_drop,
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
