#!/usr/bin/env bash
# Track B launch script. Sibling of ../original/run_sbatch.sh (Track A) -- same
# partition, same config content, so the two runs are the actual comparison.
# This is the handle-based restructure (see REPORT.md): the old PipelineState/
# fg.task-import version that used to live at this path is gone, replaced
# wholesale after parity was re-confirmed against it (job 24307/24309).
#SBATCH -A biostructure
#SBATCH -p biostructure-fortran
#SBATCH --qos=biostructure-priority-qos
#SBATCH --gres=gpu:rtx6000bb:1
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=8
#SBATCH -c 8
#SBATCH -t 00:30:00
#SBATCH -J cf_adapted
# Relative, not absolute: SBATCH directives aren't shell-expanded, so $HOME/~ don't
# resolve here. Relies on sbatch being submitted from this directory (cwd).
#SBATCH -o sbatch.out
#SBATCH -e sbatch.err

set -euo pipefail

# Computed from cwd, not a hardcoded $HOME path: sbatch is always submitted from
# this directory (-o/-e below are relative to cwd at submission time, not shell-
# expanded), so this resolves correctly whether that cwd is Javier's permanent
# deployment clone (~/software/cnio_usecase/...) or a throwaway CI scratch clone
# (scripts/ci_run.sh's mktemp checkout) -- same script, no path rewriting needed.
FG_DIR="$(pwd)"
CONFIG="${1:-$FG_DIR/config/config.json}"

# No shared/CI-specific venv: every run -- CI-triggered or Javier's own -- executes
# as javidsegura (the self-hosted runner's own identity, regardless of whose PR
# triggered it), so his existing personal deployment paths just work unchanged.
# Only the output directory varies per run (CNIO_CI_OUT, below).
VENV="$HOME/.venvs/cnio_usecase"
FLOWGENTIC_PKG="$HOME/software/flowgentic"
OUT_DIR="${CNIO_CI_OUT:-$FG_DIR/results}"

# Self-contained venv: no `module load`, so PYTHONPATH is never set. See
# context/current-implementation.md "UPDATE 2026-10-20" for why that matters.
source "$VENV/bin/activate"
export PYTHONUNBUFFERED=1

# .env lives at the repo root -- pointed at explicitly rather than guessed, since this
# file is gitignored and never deployed by a plain git clone on its own. Missing file =
# no-op (Discord notifs just stay off).
export FLOWGENTIC_ENV_FILE="$HOME/software/cnio_usecase/.env"

# Dragon children must `import flowgentic` to deserialize their tasks (cloudpickle ships by
# reference), so it has to be on site-packages, not just sitting next to this script. This
# is also where `flowgentic.handle.PipelineHandle` comes from -- confirm the flowgentic
# checkout on the cluster is at the commit that added handle.py before relying on this.
# --no-deps: flowgentic is stdlib-only; don't let pip's resolver touch anything else pinned.
python3 -m pip install --no-deps --quiet -e "$FLOWGENTIC_PKG" python-dotenv

mkdir -p "$OUT_DIR"
cp "$CONFIG" "$OUT_DIR/config.used.json"

# -s (single-node services only), not -w slurm -t tcp: the latter stands up Dragon's full
# multi-node overlay even for a one-node job, and that overlay has hung here before. Nothing
# in this pipeline is multi-node. No --mem: Dragon's /dev/shm pools are ~5GB and the node has
# 756GB to itself; capping it just gives the pools a ceiling to hit for no benefit.
#
# main.py lives under $FG_DIR/src -- stages/ and pipeline.py (the wiring, a
# sibling of main.py, not inside stages/) are its own siblings, so Python's own
# sys.path[0] (the script's own directory) is enough for `import stages` and
# `from pipeline import cageflow` to resolve.

START=$(date +%s)
dragon -s "$FG_DIR/src/main.py" "$CONFIG"
WALL=$(( $(date +%s) - START ))
echo "wall_s=$WALL"
echo "$WALL" > "$OUT_DIR/wall_s.txt"
