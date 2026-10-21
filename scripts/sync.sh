#!/usr/bin/env bash
# Sync cnio_usecase and flowgentic to their tracked branch's current tip on this
# cluster. Run this before submitting anything from cnio_usecase -- see the
# sync-convention note in /context (Thesis/context/current-implementation.md).
#
# CageFlow is deliberately NOT handled here. It's Laura's repo, not ours to
# silently advance -- syncing it stays a manual `git pull` in
# ~/software/CageFlow, done only when you've deliberately decided to pick up
# her latest commit.
set -euo pipefail

CNIO_USECASE_DIR="$HOME/software/cnio_usecase"
FLOWGENTIC_DIR="$HOME/software/flowgentic"
VENV="$HOME/.venvs/cnio_usecase"

sync_repo() {
    local dir="$1" label="$2" branch old new
    cd "$dir"
    branch="$(git rev-parse --abbrev-ref HEAD)"
    old="$(git rev-parse --short HEAD)"
    git fetch --quiet origin "$branch"
    new="$(git rev-parse --short "origin/$branch")"
    if [ "$old" = "$new" ]; then
        echo "[$label] up to date ($old, branch $branch)"
    else
        git reset --hard --quiet "origin/$branch"
        echo "[$label] updated $old -> $new (branch $branch)"
    fi
}

sync_repo "$CNIO_USECASE_DIR" "cnio_usecase"
sync_repo "$FLOWGENTIC_DIR" "flowgentic"

# Always re-run, even if flowgentic's commit didn't move -- cheap (a few seconds),
# and it's the only way to guarantee pip's installed metadata (entry points,
# pyproject.toml changes) never goes stale after a pull. A plain `git pull` alone
# would NOT catch that: editable installs reflect source-code edits immediately,
# but not changes to the package's own metadata.
source "$VENV/bin/activate"
python3 -m pip install --no-deps --quiet -e "$FLOWGENTIC_DIR"
echo "[flowgentic] editable install refreshed"

# .env is gitignored on purpose (it's a secret -- DISCORD_WEBHOOK_URL), so it never
# comes along on a fresh clone and this sync can't restore it even if it wanted to.
# Local and cluster deliberately keep independent .env files (see main.py's own
# comment on FLOWGENTIC_ENV_FILE) -- this is just a heads-up so a missing file doesn't
# show up later as "why did Discord notifications silently stop," which is exactly
# what a missing .env looks like (load_dotenv() no-ops on a missing path).
if [ ! -f "$CNIO_USECASE_DIR/.env" ]; then
    echo "[cnio_usecase] WARNING: .env not found at $CNIO_USECASE_DIR/.env -- Discord notifications will stay silently disabled until you create one from .env.example"
fi
