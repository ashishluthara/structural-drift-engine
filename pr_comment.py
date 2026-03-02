"""
pr_comment.py — GitHub Pull Request Comment Bot

Posts a structured drift report as a PR comment using the GitHub REST API.
Uses only the standard library (urllib) — no external dependencies.

Behavior:
- Posts a new comment on first run.
- Finds and UPDATES the existing comment on subsequent runs (no spam).
- Exits with code 1 if score delta breaches the configured threshold.

Environment variables required when posting:
    GITHUB_TOKEN       — Personal access token or Actions GITHUB_TOKEN
    GITHUB_REPOSITORY  — e.g. "your-org/your-repo"
    PR_NUMBER          — Pull request number as a string
"""

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# Marker injected into every comment body so we can find and update it.
COMMENT_MARKER = "<!-- structural-drift-engine -->"


def _api_request(
    method: str,
    url: str,
    token: str,
    payload: dict | None = None,
) -> dict | list | None:
    """
    Make an authenticated GitHub REST API request.

    Args:
        method: HTTP verb (GET, POST, PATCH).
        url: Full API URL.
        token: GitHub token for Authorization header.
        payload: Optional JSON body dict.

    Returns:
        Parsed JSON response, or None for 204 responses.

    Raises:
        RuntimeError: On non-2xx HTTP responses.
    """
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
        raise RuntimeError(
            f"GitHub API error {exc.code} on {method} {url}: {detail}"
        ) from exc


def _find_existing_comment(
    token: str,
    repo: str,
    pr_number: int,
) -> int | None:
    """
    Search PR comments for an existing drift report (identified by marker).

    Args:
        token: GitHub token.
        repo: Repository slug (owner/name).
        pr_number: Pull request number.

    Returns:
        Comment ID if found, else None.
    """
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments?per_page=100"

    try:
        comments = _api_request("GET", url, token)
    except RuntimeError as exc:
        print(f"  [WARN] Could not fetch existing comments: {exc}")
        return None

    if not isinstance(comments, list):
        return None

    for comment in comments:
        if COMMENT_MARKER in comment.get("body", ""):
            return comment["id"]

    return None


def _build_comment_body(
    drift_score: int,
    score_delta: int | None,
    cycles: list[list[str]],
    violations: list[dict],
    coupling_metrics: dict,
    penalty_breakdown: dict,
    max_drop: int,
) -> str:
    """
    Render the PR comment as GitHub-flavored Markdown.

    Args:
        drift_score: Current drift score (0–100).
        score_delta: Score change vs baseline, or None if no baseline.
        cycles: Detected dependency cycles.
        violations: Boundary violations.
        coupling_metrics: Coupling metrics dict.
        penalty_breakdown: Penalty breakdown dict from drift result.
        max_drop: Max points score may drop before CI fails (e.g. 5 = fail if score drops > 5).

    Returns:
        Full markdown string ready to post as a GitHub comment.
    """
    # Score badge
    if drift_score >= 80:
        badge = "🟢"
    elif drift_score >= 60:
        badge = "🟡"
    else:
        badge = "🔴"

    # Delta line
    if score_delta is None:
        delta_line = "_No baseline — this run establishes the baseline._"
    elif score_delta > 0:
        delta_line = f"↑ **+{score_delta}** from baseline"
    elif score_delta < 0:
        breach = score_delta < -max_drop
        marker = " ⚠️ **threshold breached**" if breach else ""
        delta_line = f"↓ **{score_delta}** from baseline{marker}"
    else:
        delta_line = "→ No change from baseline"

    # Cycles section
    if cycles:
        cycle_lines = "\n".join(
            f"- `{'` → `'.join(c)}`" for c in cycles[:10]
        )
        if len(cycles) > 10:
            cycle_lines += f"\n- _...and {len(cycles) - 10} more_"
        cycles_section = f"**{len(cycles)} detected**\n{cycle_lines}"
    else:
        cycles_section = "✓ None detected"

    # Violations section
    if violations:
        viol_lines = "\n".join(
            f"- `{v['source']}` → `{v['target']}`"
            for v in violations[:15]
        )
        if len(violations) > 15:
            viol_lines += f"\n- _...and {len(violations) - 15} more_"
        violations_section = f"**{len(violations)} detected**\n{viol_lines}"
    else:
        violations_section = "✓ None detected"

    # High coupling
    hc = coupling_metrics.get("high_coupling_modules", [])
    if hc:
        hc_lines = "\n".join(
            f"- `{m['module']}` — {m['dependency_count']} deps"
            for m in hc[:10]
        )
        hc_section = f"**{len(hc)} modules**\n{hc_lines}"
    else:
        hc_section = "✓ None detected"

    # Penalty table
    pb = penalty_breakdown
    cd = pb["circular_dependencies"]
    bv = pb["boundary_violations"]
    hcm = pb["high_coupling_modules"]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return f"""{COMMENT_MARKER}
## {badge} Structural Drift Report

| | |
|---|---|
| **Drift Score** | `{drift_score} / 100` |
| **Delta** | {delta_line} |
| **Modules** | {coupling_metrics.get('total_modules', '?')} |
| **Total Edges** | {coupling_metrics.get('total_edges', '?')} |
| **Avg Dependencies** | {coupling_metrics.get('avg_dependencies', '?')} |

---

### Circular Dependencies
{cycles_section}

### Boundary Violations
{violations_section}

### High-Coupling Modules
{hc_section}

---

<details>
<summary>Penalty breakdown</summary>

| Component | Count | Ratio | Penalty |
|---|---|---|---|
| Circular dependencies | {cd['count']} | {cd['ratio']:.2%} | −{cd['penalty']} |
| Boundary violations | {bv['count']} | {bv['ratio']:.2%} | −{bv['penalty']} |
| High-coupling modules | {hcm['count']} | {hcm['ratio']:.2%} | −{hcm['penalty']} |

_Penalties are normalized against repo size. See [scoring formula](https://github.com/your-org/structural-drift-engine#scoring)._

</details>

<sub>structural-drift-engine · {now}</sub>
"""


def post_pr_comment(
    drift_score: int,
    score_delta: int | None,
    cycles: list,
    violations: list,
    coupling_metrics: dict,
    penalty_breakdown: dict,
    max_drop: int = 5,
) -> bool:
    """
    Post or update a drift report comment on the current pull request.

    Reads GITHUB_TOKEN, GITHUB_REPOSITORY, and PR_NUMBER from the environment.
    Safe to call outside of a PR context — exits cleanly with a warning.

    Args:
        drift_score: Current drift score.
        score_delta: Delta vs baseline (None if no baseline exists).
        cycles: Detected cycles.
        violations: Detected violations.
        coupling_metrics: Coupling metrics dict.
        penalty_breakdown: Penalty breakdown from drift result.
        max_drop: Max points score may drop vs baseline before CI fails (default 5).

    Returns:
        True if the threshold was breached (caller should exit 1).
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    repo  = os.environ.get("GITHUB_REPOSITORY", "")
    pr_str = os.environ.get("PR_NUMBER", "")

    if not all([token, repo, pr_str]):
        print("  [INFO] GITHUB_TOKEN / GITHUB_REPOSITORY / PR_NUMBER not set — skipping PR comment.")
        return False

    try:
        pr_number = int(pr_str)
    except ValueError:
        print(f"  [WARN] Invalid PR_NUMBER: {pr_str!r}")
        return False

    body = _build_comment_body(
        drift_score=drift_score,
        score_delta=score_delta,
        cycles=cycles,
        violations=violations,
        coupling_metrics=coupling_metrics,
        penalty_breakdown=penalty_breakdown,
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

    # Determine threshold breach
    if score_delta is not None and score_delta < -max_drop:
        print(
            f"  [FAIL] Score delta {score_delta} breaches max-drop threshold of -{max_drop}."
        )
        return True

    return False
