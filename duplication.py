"""
duplication.py — Semantic Duplication Detector

Detects structurally similar modules using TF-IDF cosine similarity.
Implemented from scratch using the standard library only — no sklearn,
no sentence-transformers, no external dependencies.

Algorithm:
    1. Extract token vocabulary from each module's source text
    2. Build TF-IDF vectors (term frequency × inverse document frequency)
    3. Compute pairwise cosine similarity
    4. Flag pairs above the configured threshold

Optimisations:
    - Skips modules below a minimum line count
    - Caps at max_modules to avoid O(n²) explosion on large repos
    - Strips comments and docstrings before tokenising
    - Uses sparse representation (only non-zero terms stored)
"""

import ast
import math
import re
from pathlib import Path


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_source_tokens(file_path: str, root_path: str) -> list[str] | None:
    """
    Extract meaningful tokens from a Python source file.

    Strips comments, docstrings, and string literals.
    Returns identifier tokens: variable names, function names, class names.

    Args:
        file_path: Relative path to the .py file.
        root_path: Repository root path.

    Returns:
        List of lowercase token strings, or None if file cannot be read/parsed.
    """
    abs_path = Path(root_path) / file_path

    try:
        source = abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    tokens: list[str] = []

    for node in ast.walk(tree):
        # Function and class names
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            tokens.append(node.name.lower())
            for arg in node.args.args:
                tokens.append(arg.arg.lower())
        elif isinstance(node, ast.ClassDef):
            tokens.append(node.name.lower())

        # Variable assignments
        elif isinstance(node, ast.Name):
            tokens.append(node.id.lower())

        # Attribute access (e.g. self.foo → foo)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr.lower())

        # Import names
        elif isinstance(node, ast.Import):
            for alias in node.names:
                tokens.extend(alias.name.lower().split("."))

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                tokens.extend(node.module.lower().split("."))

    # Filter: keep only meaningful identifiers (length 3+, no dunders)
    tokens = [
        t for t in tokens
        if len(t) >= 3 and not t.startswith("__")
    ]

    return tokens


def _count_source_lines(file_path: str, root_path: str) -> int:
    """Count non-empty, non-comment lines in a Python file."""
    abs_path = Path(root_path) / file_path
    try:
        lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return sum(
            1 for line in lines
            if line.strip() and not line.strip().startswith("#")
        )
    except OSError:
        return 0


# ── TF-IDF implementation ─────────────────────────────────────────────────────

def _build_tf(tokens: list[str]) -> dict[str, float]:
    """
    Compute term frequency for a list of tokens.

    TF(t, d) = count(t in d) / len(d)
    """
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    total = len(tokens)
    return {t: c / total for t, c in counts.items()}


def _build_idf(documents: list[dict[str, float]]) -> dict[str, float]:
    """
    Compute inverse document frequency across all documents.

    IDF(t) = log(N / df(t)) + 1  (smoothed)
    """
    n = len(documents)
    df: dict[str, int] = {}
    for doc in documents:
        for term in doc:
            df[term] = df.get(term, 0) + 1
    return {
        term: math.log(n / count) + 1
        for term, count in df.items()
    }


def _build_tfidf(tf: dict[str, float], idf: dict[str, float]) -> dict[str, float]:
    """Multiply TF by IDF for each term present in this document."""
    return {term: tf_val * idf.get(term, 1.0) for term, tf_val in tf.items()}


def _cosine_similarity(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """
    Compute cosine similarity between two sparse TF-IDF vectors.

    Returns value in [0.0, 1.0].
    """
    # Dot product (only over shared terms)
    shared = set(vec_a) & set(vec_b)
    if not shared:
        return 0.0

    dot = sum(vec_a[t] * vec_b[t] for t in shared)
    mag_a = math.sqrt(sum(v * v for v in vec_a.values()))
    mag_b = math.sqrt(sum(v * v for v in vec_b.values()))

    if mag_a == 0 or mag_b == 0:
        return 0.0

    return dot / (mag_a * mag_b)


# ── Public API ────────────────────────────────────────────────────────────────

def detect_duplicates(
    file_paths: list[str],
    root_path: str,
    module_map: dict[str, str],
    threshold: float = 0.85,
    min_lines: int = 10,
    max_modules: int = 200,
) -> list[dict]:
    """
    Detect semantically similar module pairs using TF-IDF cosine similarity.

    Args:
        file_paths: Relative .py file paths from scanner.
        root_path: Repository root path.
        module_map: file_path -> module_name mapping.
        threshold: Similarity threshold above which pairs are flagged (0–1).
        min_lines: Skip modules with fewer non-empty lines than this.
        max_modules: Cap to avoid O(n²) performance explosion.

    Returns:
        List of dicts: {module_a, module_b, similarity}
        Sorted by similarity descending.
    """
    # Filter: minimum line count + module must be known
    candidates: list[tuple[str, str]] = []  # (file_path, module_name)

    for fp in file_paths:
        mod = module_map.get(fp)
        if not mod:
            continue
        if _count_source_lines(fp, root_path) < min_lines:
            continue
        candidates.append((fp, mod))

    # Cap to avoid combinatorial explosion
    if len(candidates) > max_modules:
        candidates = candidates[:max_modules]

    if len(candidates) < 2:
        return []

    # Build token lists
    token_lists: list[list[str]] = []
    valid_candidates: list[tuple[str, str]] = []

    for fp, mod in candidates:
        tokens = _extract_source_tokens(fp, root_path)
        if tokens and len(tokens) >= 5:
            token_lists.append(tokens)
            valid_candidates.append((fp, mod))

    if len(valid_candidates) < 2:
        return []

    # Build TF-IDF vectors
    tf_docs = [_build_tf(tokens) for tokens in token_lists]
    idf = _build_idf(tf_docs)
    tfidf_vecs = [_build_tfidf(tf, idf) for tf in tf_docs]

    # Pairwise cosine similarity
    duplicates: list[dict] = []
    n = len(valid_candidates)

    for i in range(n):
        for j in range(i + 1, n):
            sim = _cosine_similarity(tfidf_vecs[i], tfidf_vecs[j])
            if sim >= threshold:
                _, mod_a = valid_candidates[i]
                _, mod_b = valid_candidates[j]
                duplicates.append({
                    "module_a": mod_a,
                    "module_b": mod_b,
                    "similarity": round(sim, 4),
                })

    return sorted(duplicates, key=lambda x: -x["similarity"])
