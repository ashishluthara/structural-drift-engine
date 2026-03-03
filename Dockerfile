# ── Structural Drift Engine — Docker Action Image ─────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.title="structural-drift-engine"
LABEL org.opencontainers.image.description="Detects architectural drift in Python repositories."
LABEL org.opencontainers.image.source="https://github.com/your-org/structural-drift-engine"

# Non-root user for safe execution
RUN useradd --create-home --shell /bin/bash drift

WORKDIR /app

# Copy engine source — no pip install needed (stdlib only)
COPY --chown=drift:drift \
    main.py \
    scanner.py \
    graph_builder.py \
    metrics.py \
    drift.py \
    drift_index.py \
    snapshot.py \
    utils.py \
    config.py \
    complexity.py \
    duplication.py \
    pr_comment.py \
    entrypoint.sh \
    ./

RUN chmod +x /app/entrypoint.sh

# Runtime mount point for the target repository
RUN mkdir -p /repo && chown drift:drift /repo

USER drift

ENTRYPOINT ["python", "/app/main.py"]
CMD ["--path", "/repo"]
