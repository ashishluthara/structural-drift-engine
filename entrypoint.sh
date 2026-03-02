#!/bin/sh
# entrypoint.sh — Docker Action Entrypoint
#
# Reads INPUT_* environment variables set by GitHub Actions and
# builds the main.py argument list conditionally.
# This avoids passing empty-string args that break argparse.

set -e

ARGS="--path ${INPUT_PATH:-.}"
ARGS="$ARGS --threshold ${INPUT_THRESHOLD:-10}"
ARGS="$ARGS --drift-threshold ${INPUT_DRIFT_THRESHOLD:--5}"
ARGS="$ARGS --output-env"

if [ "${INPUT_SAVE_SNAPSHOT}" = "true" ]; then
  ARGS="$ARGS --save-snapshot"
fi

if [ "${INPUT_POST_PR_COMMENT}" = "true" ]; then
  ARGS="$ARGS --pr-comment"
fi

echo "Running: python /app/main.py $ARGS"
exec python /app/main.py $ARGS
