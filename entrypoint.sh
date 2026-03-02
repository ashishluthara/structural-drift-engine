#!/bin/sh
# entrypoint.sh — Docker Action Entrypoint
#
# Reads INPUT_* environment variables set by GitHub Actions and builds
# the argument list for main.py without passing any empty strings.

set -e

# Resolve path: INPUT_PATH is relative to GITHUB_WORKSPACE
TARGET_PATH="${GITHUB_WORKSPACE}/${INPUT_PATH:-.}"

ARGS="--path $TARGET_PATH"
ARGS="$ARGS --threshold ${INPUT_THRESHOLD:-10}"
ARGS="$ARGS --max-drop ${INPUT_DRIFT_THRESHOLD:-5}"
ARGS="$ARGS --output-env"

if [ "${INPUT_SAVE_SNAPSHOT}" = "true" ]; then
  ARGS="$ARGS --save-snapshot"
fi

if [ "${INPUT_POST_PR_COMMENT}" = "true" ]; then
  ARGS="$ARGS --pr-comment"
fi

# Log the exact command for debugging
echo "entrypoint: python /app/main.py $ARGS"
exec python /app/main.py $ARGS
