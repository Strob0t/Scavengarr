#!/usr/bin/env bash
# usage-guard.sh: keeps every Claude Code session on this machine out of Anthropic's rate limit.
#
# Wired as user-level hooks (~/.claude/settings.json), so every session and worktree gets it:
#   PreToolUse        while the account's 5-hour window is at or above the block threshold, every
#                     tool call is refused (exit 2) with the release time, the cron expression of a
#                     wake-up one minute after it and the parking instructions in the message. The
#                     tools a parked session needs pass: CronCreate (and ToolSearch to load it),
#                     SendMessage, ListAgents, the Task tools, and plain git add/commit/push/status.
#   UserPromptSubmit  prints one context line with the current usage, so a session can judge an
#                     expensive step before it starts.
#
# Source: claude/status.json in the private repo Strob0t/shared-state (written every 5 minutes from
# the API's rate-limit headers). Fetched with gh, cached for 60 s under ~/.cache/claude-usage/ and
# refreshed in the background, so a tool call never waits on the network. Unknown usage (no gh or
# jq, a failed fetch, a status older than 20 minutes) never blocks: the guard fails open and says so
# in the context line. Nothing here reads credentials or the usage API directly.
#
# Environment: CLAUDE_USAGE_BLOCK_PCT (default 90: the status lags the API by up to six minutes
# and three sessions burn in parallel), CLAUDE_USAGE_WEEK_BLOCK_PCT (default 97: the weekly limit
# is a hard rate limit too, there is no weekly pacing), CLAUDE_USAGE_GUARD=off disables the guard.
set -u

[ "${CLAUDE_USAGE_GUARD:-on}" = "off" ] && exit 0
command -v jq >/dev/null 2>&1 || exit 0

input=$(cat)
event=$(jq -r '.hook_event_name // ""' <<<"$input" 2>/dev/null)
tool=$(jq -r '.tool_name // ""' <<<"$input" 2>/dev/null)

# The tools a parked session needs pass the guard
if [ "$event" = PreToolUse ]; then
  case "$tool" in
    CronCreate|CronList|CronDelete|ToolSearch|SendMessage|ListAgents|TaskList|TaskGet|TaskUpdate|TaskCreate) exit 0 ;;
    Bash)
      cmd=$(jq -r '.tool_input.command // ""' <<<"$input" 2>/dev/null)
      case "$cmd" in
        "git add "*|"git commit "*|"git push "*|"git status"*) exit 0 ;;
      esac
      ;;
  esac
fi

threshold=${CLAUDE_USAGE_BLOCK_PCT:-90}
week_threshold=${CLAUDE_USAGE_WEEK_BLOCK_PCT:-97}
cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/claude-usage"
file="$cache_dir/status.json"
mkdir -p "$cache_dir"
now=$(date +%s)

fetch() {
  command -v gh >/dev/null 2>&1 || return 1
  timeout 15 gh api -H "Accept: application/vnd.github.raw+json" \
    repos/Strob0t/shared-state/contents/claude/status.json 2>/dev/null
}

store() {  # fetch into the cache file; keep the old file when the fetch fails
  if fetch >"$file.tmp" && jq -e .five_hour "$file.tmp" >/dev/null 2>&1; then
    mv "$file.tmp" "$file"
  else
    rm -f "$file.tmp"
  fi
}

refresh() {  # synchronous when nothing is cached, detached otherwise
  if [ ! -f "$file" ]; then
    store
    return
  fi
  if [ ! -f "$file.lock" ] || [ $((now - $(stat -c %Y "$file.lock"))) -ge 30 ]; then
    touch "$file.lock"
    (
      store
      rm -f "$file.lock"
    ) </dev/null >/dev/null 2>&1 &
    disown
  fi
}

if [ ! -f "$file" ] || [ $((now - $(stat -c %Y "$file"))) -ge 60 ]; then
  refresh
fi

if [ ! -f "$file" ]; then
  [ "$event" = UserPromptSubmit ] && echo "usage-guard: usage unknown (no status fetched), not blocking; tell the user"
  exit 0
fi

US=$'\x1f'
IFS=$US read -r pct reset wpct wreset updated < <(
  jq -r '[
      (.five_hour.pct // "" | tostring),
      (.five_hour.resets_at // "" | tostring),
      (.seven_day.pct // "" | tostring),
      (.seven_day.resets_at // "" | tostring),
      (.updated_at // "")
    ] | join("\u001f")' "$file" 2>/dev/null
)

updated_s=$(date -d "$updated" +%s 2>/dev/null || echo 0)
age=$((now - updated_s))
stale=0
[ "$age" -gt 1200 ] && stale=1
pct_i=${pct%%.*}
wpct_i=${wpct%%.*}
[ -n "$pct_i" ] || pct_i=0
[ -n "$wpct_i" ] || wpct_i=0

fmt() { date -d "@$1" '+%H:%M %Z' 2>/dev/null; }
fmt_berlin() { TZ=Europe/Berlin date -d "@$1" '+%H:%M %Z' 2>/dev/null; }
fmt_day() { date -d "@$1" '+%a %H:%M %Z' 2>/dev/null; }

blocked=""
release=""
if [ "$stale" -eq 0 ]; then
  if [ -n "$reset" ] && [ "$now" -lt "$reset" ] && [ "$pct_i" -ge "$threshold" ]; then
    blocked="5-hour window ${pct}% used, blocks at ${threshold}%"
    release=$reset
  elif [ -n "$wreset" ] && [ "$now" -lt "$wreset" ] && [ "$wpct_i" -ge "$week_threshold" ]; then
    blocked="weekly limit ${wpct}% used, blocks at ${week_threshold}%"
    release=$wreset
  fi
fi

if [ "$event" = UserPromptSubmit ]; then
  line="usage-guard: 5h ${pct:-?}% (reset $(fmt "$reset") = $(fmt_berlin "$reset")), 7d ${wpct:-?}% (reset $(fmt_day "$wreset")); blocks at ${threshold}%; status ${age}s old"
  [ "$stale" -eq 1 ] && line="$line; STALE: not blocking, tell the user the status feed stopped"
  [ -n "$blocked" ] && line="$line; BLOCKED until $(fmt "$release")"
  echo "$line"
  exit 0
fi

[ "$event" = PreToolUse ] || exit 0
[ -z "$blocked" ] && exit 0

wake=$((release + 60))
cron_expr="$(date -d "@$wake" '+%-M %-H %-d %-m') *"
{
  echo "Usage budget exceeded ($blocked). Tool calls are blocked until $(fmt "$release") ($(fmt_berlin "$release"))."
  echo "Do not retry and run nothing else. Commit what is finished with plain git add/commit/push calls (they pass this guard; run no tests), then call CronCreate once (it passes too; load it with ToolSearch if needed): recurring=false, durable=false, cron \"$cron_expr\" (local time, one minute after the release), prompt = your full state (done, open, branch, last commit hash, running agents, what to do next). Tell the user in one line that you are parked until $(fmt_berlin "$release") and end the turn."
} >&2
exit 2
