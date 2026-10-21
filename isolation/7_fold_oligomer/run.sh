#!/usr/bin/env bash
# Step 7: fold_oligomer, in isolation (real ColabFold, needs a GPU). See SUCCESS.md.
set -euo pipefail
cd "$(dirname "$0")"

source ~/.venvs/cnio_usecase/bin/activate

# Chains the WHOLE pipeline (dock -> ... -> rosetta -> fold) for real, so
# generous wall time: Rosetta scoring alone is ~134s/sequence.
# biostructure-fortran, not the general gpu partition: same account/partition e2e's
# own run_sbatch.sh already uses successfully, 2 cards, far less contended than the
# shared general gpu queue. Card model is rtx6000bb here, not rtx6000ada.
srun -A biostructure -p biostructure-fortran --qos=biostructure-priority-qos --gres=gpu:rtx6000bb:1 -c 4 --mem=16G -t 00:30:00 python3 main.py
