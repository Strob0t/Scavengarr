#!/bin/bash
# Post-Edit/Write hook: auto-format and lint changed Python files with ruff

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Skip if no file path
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Only process Python files
if [[ ! "$FILE_PATH" =~ \.py$ ]]; then
  exit 0
fi

# Skip if file doesn't exist (was deleted)
if [ ! -f "$FILE_PATH" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR" || exit 0

# Format with ruff (auto-fix)
poetry run ruff format "$FILE_PATH" 2>/dev/null || true

# Lint with ruff (auto-fix imports + common issues)
poetry run ruff check --fix "$FILE_PATH" 2>/dev/null || true

exit 0
