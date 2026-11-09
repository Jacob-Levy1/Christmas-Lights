#!/usr/bin/env bash
#
# Update the tree from GitHub.
#
#   bash update.sh            bring the drive in line with the latest code
#   bash update.sh --force    the same, discarding local edits to tracked files
#   bash update.sh --branch X track a branch other than main
#
# Plug the Pico in, open a terminal on the CIRCUITPY drive and run it.  It
# is safe to run repeatedly; when there is nothing new it says so and stops.
#
# Works in Terminal on macOS, any Linux shell, and Git Bash on Windows
# (which comes with Git for Windows).  Run it with `bash update.sh` rather
# than `./update.sh`: CIRCUITPY is a FAT volume and cannot record the
# executable bit.

set -euo pipefail

# ---------------------------------------------------------------------------
# This script lives in the repository it is updating, so a pull can rewrite
# it underneath a shell that is still reading it, which makes bash run
# nonsense.  Run from a copy instead, taken before anything is fetched.
# ---------------------------------------------------------------------------
if [ "${TREE_UPDATE_COPY:-}" != "1" ]; then
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    copy="$(mktemp "${TMPDIR:-/tmp}/tree-update.XXXXXX")"
    cat "${BASH_SOURCE[0]}" > "$copy"
    trap 'rm -f "$copy"' EXIT
    TREE_UPDATE_COPY=1 TREE_UPDATE_DIR="$here" bash "$copy" "$@"
    exit $?
fi

REPO="${TREE_UPDATE_DIR:?}"
BRANCH="main"
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1 ;;
        --branch) shift; BRANCH="${1:-main}" ;;
        -h|--help) sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die()  { printf '\n!! %s\n' "$*" >&2; exit 1; }

cd "$REPO"

# --------------------------------------------------------------- checks ---
command -v git >/dev/null 2>&1 || die "git is not installed, or not on the PATH."
git rev-parse --git-dir >/dev/null 2>&1 || die "$REPO is not a git repository."
[ -f code.py ] && [ -f boot.py ] || die "$REPO does not look like the tree's drive (no code.py)."

say "Tree drive: $REPO"

# --------------------------------------- let the operating system have its
# own files back.  macOS, Windows and Linux all leave bookkeeping on a
# removable volume, and some of it -- System Volume Information above all --
# cannot be deleted by a normal user on Windows.  While git still tracks
# them, every pull tries to, and fails part way through.  Untracking them
# leaves the files alone on disk and takes git out of the argument.  After
# the first run there is nothing left to do here.
ARTEFACTS=(
    "System Volume Information"
    ".fseventsd"
    ".Trashes"
    ".Trash-1000"
    ".metadata_never_index"
    ".Spotlight-V100"
    "boot_out.txt"
    "settings.toml"
    "sd"
)

tracked_artefacts=()
for path in "${ARTEFACTS[@]}"; do
    if git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
        tracked_artefacts+=("$path")
    fi
done

if [ ${#tracked_artefacts[@]} -gt 0 ]; then
    step "Taking the operating system's own files out of git"
    for path in "${tracked_artefacts[@]}"; do say "   $path"; done
    git rm -r --cached --quiet --ignore-unmatch -- "${tracked_artefacts[@]}"
    git -c user.name="tree update" -c user.email="tree@localhost" \
        commit -q -m "Stop tracking the drive's operating-system files" || true
    say "   done; the files themselves were not touched"
fi

# ------------------------------------------------------- local changes ---
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    if [ "$FORCE" -eq 1 ]; then
        step "Discarding local changes (--force)"
        git status --short --untracked-files=no | sed 's/^/   /'
        # Do it now rather than relying on the update below: when the drive
        # is already on the latest commit there is no update to do, and the
        # edits would otherwise survive a run that said it discarded them.
        # Only tracked files are touched -- config.json, boot_out.txt and
        # the operating system's own files are untracked and stay put.
        git reset --hard --quiet HEAD
    else
        say ""
        git status --short --untracked-files=no | sed 's/^/   /'
        die "There are local edits to tracked files on the drive (above).
   Save anything you want to keep, then run:  bash update.sh --force"
    fi
fi

# -------------------------------------------------------------- fetch ---
step "Fetching"
git fetch --quiet origin "$BRANCH" || die "Could not reach GitHub. Is this machine online?"

before="$(git rev-parse HEAD)"
target="$(git rev-parse "origin/$BRANCH")"

if [ "$before" = "$target" ]; then
    say "Already up to date at ${before:0:7} — nothing to do."
    exit 0
fi

step "Updating to $(git log -1 --format='%h %s' "origin/$BRANCH")"
git log --oneline "HEAD..origin/$BRANCH" 2>/dev/null | sed 's/^/   /' || true

# reset rather than merge: the drive is somewhere code is deployed to, not
# written on, so it should end up exactly matching the branch.  Files git
# does not track -- the operating system's, config.json, boot_out.txt -- are
# left alone.
git reset --hard --quiet "$target"

# --------------------------------------------------------------- check ---
step "Checking what landed"
missing=()
for path in code.py boot.py tree.py patterns.py buttons.py snake.py pwhash.py \
            lib/neopixel.mpy www/index.html.gz; do
    [ -e "$path" ] || missing+=("$path")
done
[ ${#missing[@]} -eq 0 ] || die "These should be on the drive but are not: ${missing[*]}"

# The board serves a prebuilt page; if the sources are newer than the
# bundle, someone committed a change without building it.
if command -v python3 >/dev/null 2>&1 && [ -f tools/build.py ]; then
    newest_src="$(ls -t src/* 2>/dev/null | head -1 || true)"
    if [ -n "$newest_src" ] && [ "$newest_src" -nt www/index.html.gz ]; then
        say "   note: src/ is newer than the built page; run python3 tools/build.py"
    fi
fi

say "   all present"
say ""
say "Updated ${before:0:7} -> ${target:0:7}"
say ""
say "The tree restarts itself as the files land, so it may flicker for a few"
say "seconds. When it settles, eject the drive and power-cycle the board."
