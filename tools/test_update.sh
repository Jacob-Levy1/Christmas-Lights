#!/usr/bin/env bash
#
# Check update.sh against a pretend CIRCUITPY drive.
#
#     bash tools/test_update.sh
#
# The drive starts where a real one does: cloned at an older commit that
# still tracked the operating system's files.  The point of most of these
# checks is that those files survive, because on Windows git cannot delete
# "System Volume Information" and a plain pull stops there half-done.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

FAILED=0
# check <name> <command...> -- passes when the command succeeds
check() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then printf 'PASS %s\n' "$name"
    else printf 'FAIL %s\n' "$name"; FAILED=$((FAILED + 1)); fi
}
# checkn <name> <command...> -- passes when the command FAILS
checkn() {
    local name="$1"; shift
    if "$@" >/dev/null 2>&1; then printf 'FAIL %s\n' "$name"; FAILED=$((FAILED + 1))
    else printf 'PASS %s\n' "$name"; fi
}
same() { [ "$1" = "$2" ]; }
tracked() { git ls-files --error-unmatch -- "$1" >/dev/null 2>&1; }
any_artefact_tracked() {
    git ls-files | grep -qiE "System Volume|fseventsd|Trash|metadata|boot_out|settings\.toml"
}
clean_tree() { [ -z "$(git status --porcelain --untracked-files=no)" ]; }

# An origin, and a drive sitting on the commit before the artefacts were
# untracked -- the state a drive cloned months ago would be in.
git clone --quiet --bare "$ROOT" "$WORK/origin.git"
BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)"
git --git-dir="$WORK/origin.git" symbolic-ref HEAD "refs/heads/$BRANCH"
# The commit that removed the artefacts; its parent is a drive that still
# has them, which is where a long-untouched clone sits.
REMOVED="$(git -C "$ROOT" log --format=%H --all -- "System Volume Information" | head -1)"
[ -n "$REMOVED" ] || { echo "cannot find the commit that untracked the artefacts"; exit 1; }
OLD="$REMOVED^"

git clone --quiet "$WORK/origin.git" "$WORK/drive"
cd "$WORK/drive"
git checkout --quiet "$OLD"
git checkout --quiet -B main

ARTEFACTS=("System Volume Information/IndexerVolumeGuid" ".fseventsd/no_log"
           ".Trashes" ".Trash-1000" ".metadata_never_index" "boot_out.txt" "settings.toml")

check "the pretend drive starts with the OS files tracked" tracked "System Volume Information"

# Put the script on the drive the way the first run does -- by hand, not
# by a pull.  Using the working tree's copy rather than origin's means a
# change being worked on is what gets tested.
cp "$ROOT/update.sh" update.sh

check "it runs cleanly" bash update.sh --branch "$BRANCH"

for path in "${ARTEFACTS[@]}"; do
    check "left alone on disk: $path" test -e "$path"
done

checkn "and none of them is tracked any more" any_artefact_tracked
check "the drive is on the latest commit" \
    same "$(git rev-parse HEAD)" "$(git rev-parse "origin/$BRANCH")"
check "the board's own files are there" test -f www/index.html.gz
check "and so is the firmware" test -f code.py
check "and the password hashing" test -f pwhash.py
check "the worktree is clean" clean_tree

# The run above replaced update.sh underneath the shell reading it.
check "the script survived rewriting itself" grep -q TREE_UPDATE_COPY update.sh

bash update.sh --branch "$BRANCH" 2>&1 | grep -q "Already up to date"
if [ "${PIPESTATUS[1]:-1}" = "0" ]; then printf 'PASS %s\n' "running it again does nothing"
else printf 'FAIL %s\n' "running it again does nothing"; FAILED=$((FAILED + 1)); fi

# Settings the board wrote must never be swept away.
echo '{"turns": 9}' > config.json
echo "# edited" >> code.py
checkn "a local edit stops the update" bash update.sh --branch "$BRANCH"
check "and nothing was changed behind your back" grep -q edited code.py

check "--force succeeds" bash update.sh --branch "$BRANCH" --force
checkn "--force discards the edit" grep -q edited code.py
check "--force keeps your saved settings" same "$(cat config.json)" '{"turns": 9}' 

cd "$ROOT"
echo
if [ "$FAILED" -ne 0 ]; then echo "$FAILED FAILURES"; exit 1; fi
echo "all update-script checks passed"
