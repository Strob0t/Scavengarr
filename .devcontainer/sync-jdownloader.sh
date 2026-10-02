#!/usr/bin/env bash
# Keep the JDownloader reference sources in .devdata/JDownloader2/ in sync with SVN.
#
# plugins/ and controlling/ are SVN working copies of the JDownloader trunk.
# First run replaces plain copies with checkouts; later runs do `svn update`.
# Changes pulled in by the last sync are written to CHANGES.md so they can be
# reviewed (e.g. hoster plugins that affect our resolvers).
#
# Never fails: an unreachable SVN server must not block container startup.

set -uo pipefail

SVN_BASE="svn://svn.jdownloader.org/jdownloader/trunk/src/jd"
DEST=".devdata/JDownloader2"
DIRS=(plugins controlling)
CHANGES="$DEST/CHANGES.md"
TIMEOUT=600

log() { echo "[jd-sync] $*"; }

if ! command -v svn >/dev/null 2>&1; then
  log "svn not installed, skipping"
  exit 0
fi

mkdir -p "$DEST"
changes_tmp="$(mktemp)"
trap 'rm -f "$changes_tmp"' EXIT

checkout() {
  local url="$1" target="$2" staging="$2.svn-checkout"
  rm -rf "$staging"
  log "checkout $url"
  if ! timeout "$TIMEOUT" svn checkout -q "$url" "$staging"; then
    log "WARN: checkout failed, keeping existing $target"
    rm -rf "$staging"
    return 0
  fi
  # Swap only after a complete checkout so a failed run never loses the old copy.
  rm -rf "$target"
  mv "$staging" "$target"
  echo "- \`$target\`: fresh checkout at r$(svn info --show-item revision "$target")" >>"$changes_tmp"
}

update() {
  local target="$1" base head
  base="$(svn info --show-item revision "$target")"
  if ! timeout "$TIMEOUT" svn update -q "$target"; then
    log "WARN: update of $target failed"
    return 0
  fi
  head="$(svn info --show-item revision "$target")"
  if [ "$base" = "$head" ]; then
    return 0
  fi
  local files
  files="$(svn diff --summarize -r "$base:$head" "$target" 2>/dev/null)"
  if [ -z "$files" ]; then
    return 0
  fi
  {
    echo "### \`$target\` r$base → r$head"
    echo
    echo '```'
    echo "$files"
    echo '```'
    echo
    echo '```'
    svn log -r "$((base + 1)):$head" "$target" 2>/dev/null
    echo '```'
    echo
  } >>"$changes_tmp"
}

for dir in "${DIRS[@]}"; do
  target="$DEST/$dir"
  if [ -d "$target/.svn" ]; then
    update "$target"
  else
    checkout "$SVN_BASE/$dir" "$target"
  fi
done

# Keep the previous report when nothing changed.
if [ -s "$changes_tmp" ]; then
  {
    echo "# JDownloader SVN changes"
    echo
    echo "Last sync with changes: $(date -u '+%Y-%m-%d %H:%M UTC')"
    echo
    cat "$changes_tmp"
  } >"$CHANGES"
  log "changes written to $CHANGES"
else
  log "no changes"
fi

exit 0
