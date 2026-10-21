#!/usr/bin/env bash
#SBATCH -A biostructure
#SBATCH -p biostructure-fortran
#SBATCH --qos=biostructure-priority-qos
#SBATCH --gres=gpu:rtx6000bb:1
#SBATCH -c 8
#SBATCH --mem=32G
#SBATCH -t 01:00:00
#SBATCH -J cf_original
# Relative, not absolute: SBATCH directives aren't shell-expanded, so $HOME/~ don't
# resolve here. Relies on sbatch being submitted from this directory (cwd).
#SBATCH -o sbatch.out
#SBATCH -e sbatch.err

set -euo pipefail

REF_DIR="$HOME/software/cnio_usecase/e2e/1_sync/original"
CONFIG="$REF_DIR/config.json"

# Self-contained venv: no `module load`, so PYTHONPATH is never set. See
# context/current-implementation.md "UPDATE 2026-10-20" for why that matters.
source ~/.venvs/cnio_usecase/bin/activate

# out_dir is no longer a config.json key (run_original.py computes it itself, same
# formula, at runtime) -- computed here directly instead of reading a key that's
# deliberately no longer there.
OUT_DIR="$REF_DIR/_scratch/cageflow_out"
mkdir -p "$OUT_DIR"
cp "$CONFIG" "$OUT_DIR/config.used.json"

START=$(date +%s)
python3 "$REF_DIR/run_original.py" "$CONFIG"
WALL=$(( $(date +%s) - START ))
echo "wall_s=$WALL"
echo "$WALL" > "$OUT_DIR/wall_s.txt"
