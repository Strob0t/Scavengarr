Run the full test suite and report results.

Steps:
1. Run `poetry run pytest -v` to execute all tests
2. Summarize: total passed, failed, errors, warnings
3. If there are failures: show the failing test names and a brief explanation of each failure
4. If all pass: confirm with the count

If an argument is provided, run only the matching tests:
- `$ARGUMENTS` can be a test file path, test name pattern, or marker
- Example: `poetry run pytest -v -k "$ARGUMENTS"`
