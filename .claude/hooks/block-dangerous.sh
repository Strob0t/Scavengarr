#!/bin/bash
# PreToolUse(Bash) hook: block destructive commands.
# Exit 2 = block (stderr is shown to Claude); any other exit code lets the command run.
# Keep this file LF-only: a CRLF shebang fails with exit 127, which silently allows everything.

INPUT=$(cat)
COMMAND=$(jq -r '.tool_input.command // empty' <<<"$INPUT")
[[ -z $COMMAND ]] && exit 0

block() {
  echo "BLOCKED: $1" >&2
  exit 2
}

# Regexes live in variables: special chars inside a literal [[ =~ ]] break bash parsing.
WS='[[:space:]]'
ARGS="[^;&|"$'\n'"]*" # arguments of one command, never across `&&`, `;`, `|`, newlines
GIT_PUSH="git$WS+push$ARGS"

# rm with a recursive flag on an absolute or home path (-rf, -fr, -r -f, --recursive)
RM_FLAGS="(-[^[:space:]]+$WS+)*(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)($WS+-[^[:space:]]+)*"
RE_RM="(^|[;&|(]|$WS)rm$WS+$RM_FLAGS$WS+[\"']?(/|~|\\\$HOME)"
# --force, -f (also combined like -uf), +refspec; --force-with-lease stays allowed
RE_FORCE_PUSH="$GIT_PUSH$WS(--force($WS|=|\$)|-[a-zA-Z]*f|\\+[^[:space:]])"
# explicit push to main: `origin main`, `staging:main`, `HEAD:refs/heads/main`
RE_PUSH_MAIN="$GIT_PUSH[[:space:]:/]main($WS|\$)"
RE_COMMIT_OR_PUSH="git$WS+(commit|push)"
RE_RESET_HARD="git$WS+reset$ARGS--hard"
RE_CLEAN_FORCE="git$WS+clean$ARGS$WS-[a-zA-Z]*f"

[[ $COMMAND =~ $RE_RM ]] && block "recursive rm on an absolute or home path is not allowed"
[[ $COMMAND =~ $RE_FORCE_PUSH ]] && block "git push --force is not allowed"
[[ $COMMAND =~ $RE_PUSH_MAIN ]] && block "pushing to main is not allowed, use staging"
[[ $COMMAND =~ $RE_RESET_HARD ]] && block "git reset --hard is not allowed"
[[ $COMMAND =~ $RE_CLEAN_FORCE ]] && block "git clean -f is not allowed"

# commit or implicit push while main is checked out (git only runs when needed)
if [[ $COMMAND =~ $RE_COMMIT_OR_PUSH ]]; then
  CWD=$(jq -r '.cwd // empty' <<<"$INPUT")
  [[ $(git -C "${CWD:-.}" branch --show-current 2>/dev/null) == main ]] &&
    block "main is checked out; commit and push on staging instead"
fi

exit 0
