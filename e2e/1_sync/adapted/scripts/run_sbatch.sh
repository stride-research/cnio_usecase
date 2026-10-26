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
# One task: `dragon -s` runs every stage inside this one allocation, and Slurm binds
# the job to the CPUs it books (task/affinity), so -c is what the stages get. 32 CPUs
# are 16 cores on fortran (2 threads per core). The widest stage is design: two
# ProteinMPNN runs of mpnn_threads=6. Runs of this config used about 5 CPUs on
# average. dock.py splits prescore into -c chunks.
#SBATCH --ntasks=1
#SBATCH -c 32
# Runs of this config peaked at 57.1-57.4 GiB MaxRSS (sacct, summed over processes),
# mostly four concurrent ColabFold folds of about 15 GB each, plus about 5 GB of Dragon
# /dev/shm pools. Each extra concurrent fold needs about 15 GB more. rust does not
# enforce --mem (ConstrainRAMSpace=no); it tells the scheduler what this job holds.
#SBATCH --mem=80G
#SBATCH -t 00:30:00
# The time limit sends no SIGTERM on rust, so ask for one 2-3 min ahead: the run
# then stops and cleans up /dev/shm (see the end of this script) before the kill.
#SBATCH --signal=B:TERM@120
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

# Install locations. The defaults are Javier's deployment, which every run uses
# today, CI-triggered or his own. A shared read-only install can be passed in
# instead through CNIO_VENV and CNIO_SOFTWARE_DIR. main.py builds the CageFlow,
# ProteinBuilder, ProteinMPNN and localcolabfold paths from CNIO_SOFTWARE_DIR,
# so it and provenance.txt (below) always look at the same checkouts.
VENV="${CNIO_VENV:-$HOME/.venvs/cnio_usecase}"
SOFTWARE_DIR="${CNIO_SOFTWARE_DIR:-$HOME/software}"
export CNIO_SOFTWARE_DIR="$SOFTWARE_DIR"
OUT_DIR="${CNIO_CI_OUT:-$FG_DIR/results}"

# Self-contained venv: no `module load`, so PYTHONPATH is never set. See
# context/current-implementation.md "UPDATE 2026-10-20" for why that matters.
# shellcheck source=/dev/null
source "$VENV/bin/activate"
export PYTHONUNBUFFERED=1

# .env lives at the repo root -- pointed at explicitly rather than guessed, since this
# file is gitignored and never deployed by a plain git clone on its own. Missing file =
# no-op (Discord notifs just stay off).
#
# A CI run (CNIO_CI_OUT set) posts to Discord only through a file the caller names
# in CNIO_CI_ENV_FILE. It reads no other .env (the deployment's holds the webhook),
# and it drops a DISCORD_WEBHOOK_URL inherited from the submitting shell, which
# flowgentic would otherwise use. /dev/null rather than empty, because main.py
# falls back to the checkout's own .env when FLOWGENTIC_ENV_FILE is empty.
if [ -n "${CNIO_CI_OUT:-}" ]; then
    export FLOWGENTIC_ENV_FILE="${CNIO_CI_ENV_FILE:-/dev/null}"
    unset DISCORD_WEBHOOK_URL
else
    export FLOWGENTIC_ENV_FILE="$HOME/software/cnio_usecase/.env"
fi

# Dragon children must `import flowgentic` to deserialize their tasks (cloudpickle ships by
# reference), so it has to be on site-packages, not just sitting next to this script.
# Checked, not installed: two jobs installing into one venv at once race each other,
# and the editable build can fetch hatchling from PyPI on the compute node. -I so
# that neither PYTHONPATH nor the cwd can satisfy the import.
if ! import_err=$(python3 -I -c 'import flowgentic.handle, dotenv' 2>&1); then
    import_err=${import_err##*$'\n'}  # the traceback's last line
    cat >&2 <<EOF
run_sbatch.sh: flowgentic.handle and dotenv must import from $VENV
  $import_err
Install them once, from a login node, before submitting:
  source "$VENV/bin/activate"
  python3 -m pip install --no-deps -e "$SOFTWARE_DIR/flowgentic" python-dotenv
EOF
    exit 1
fi

# provenance.txt: what this run ran against, written before main.py starts so a
# crashed run has one too. One line per checkout: SHA, clean or dirty (tracked
# files only: sbatch.out/.err are untracked), repository root.
# safe.directory='*': a shared install belongs to another account, and git refuses
# to read such repositories otherwise. The run imports their code anyway.
# --no-optional-locks: git status writes nothing into the checkout.
checkout() {
    local name="$1" dir="$2" top sha state=clean
    local git=(git -c "safe.directory=*" --no-optional-locks -C "$dir")
    if [ ! -d "$dir" ]; then
        echo "checkout.$name=none missing $dir"
    elif ! top=$("${git[@]}" rev-parse --show-toplevel 2>/dev/null); then
        echo "checkout.$name=none not-a-git-checkout $dir"
    else
        sha=$("${git[@]}" rev-parse HEAD 2>/dev/null) || sha=none
        if [ -n "$("${git[@]}" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
            state=dirty
        fi
        echo "checkout.$name=$sha $state $top"
    fi
}

write_provenance() {
    local fg_src
    # The flowgentic the venv imports, wherever its editable install points.
    fg_src=$(python3 -I -c 'import importlib.util as u; print(u.find_spec("flowgentic").submodule_search_locations[0])' 2>/dev/null) \
        || fg_src="$SOFTWARE_DIR/flowgentic"
    echo "# Written by run_sbatch.sh before main.py starts."
    echo "date_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "host=$(hostname)"
    echo "user=$(id -un)"
    echo "slurm_job_id=${SLURM_JOB_ID:-none}"
    echo "slurm_cpus_per_task=${SLURM_CPUS_PER_TASK:-none}"
    echo "slurm_mem_per_node=${SLURM_MEM_PER_NODE:-none}"
    echo "cuda_visible_devices=${CUDA_VISIBLE_DEVICES:-none}"
    echo "config=$CONFIG"
    echo "env_file=$FLOWGENTIC_ENV_FILE"
    echo "venv=$VENV"
    echo "python=$(python3 -c 'import platform, sys; print(platform.python_version(), sys.executable)')"
    echo "dragonhpc=$(python3 -c 'import importlib.metadata as m; print(m.version("dragonhpc"))' 2>/dev/null || echo none) $(command -v dragon || echo none)"
    checkout cnio_usecase "$FG_DIR"
    checkout flowgentic "$fg_src"
    checkout CageFlow "$SOFTWARE_DIR/CageFlow"
    checkout ProteinBuilder "$SOFTWARE_DIR/ProteinBuilder"
    checkout ProteinMPNN "$SOFTWARE_DIR/ProteinMPNN"
    checkout localcolabfold "$SOFTWARE_DIR/localcolabfold_install/localcolabfold"
    echo
    echo "[pip freeze --all]"
    python3 -m pip freeze --all || echo "pip freeze failed, see sbatch.err"
}

mkdir -p "$OUT_DIR"
cp "$CONFIG" "$OUT_DIR/config.used.json"
write_provenance > "$OUT_DIR/provenance.txt"

# main.py reads this instead of computing its own out_dir, so this script is the
# single source of truth for where output lands. Without it, main.py's own
# HERE/"results" wins and the two silently diverge.
export CAGEFLOW_OUT_DIR="$OUT_DIR"

# Dragon's /dev/shm pools (6-8 files per runtime, several GB) outlive a run that
# fails, is cancelled or times out. Remove this run's: owned by this user, written after START, and
# mapped or opened by no live process, so another runtime of yours on the node
# keeps its pools. Not dragon-cleanup: it kills every Dragon process you have on
# the node, other jobs' included, and its -s restarts the CUDA MPS daemon.
cleanup_shm() {
    local f
    if ! command -v fuser > /dev/null; then
        echo "run_sbatch.sh: no fuser on $(hostname), /dev/shm left as it is" >&2
        return 0
    fi
    find /dev/shm -maxdepth 1 -type f -user "$(id -un)" -newermt "@$START" -print0 2>/dev/null |
        while IFS= read -r -d '' f; do
            if ! fuser -s "$f" 2>/dev/null; then
                rm -f "$f" && echo "run_sbatch.sh: removed $f"
            fi
        done || true
}

# Slurm on rust signals this script only, never dragon: scancel sends it SIGTERM
# and kills everything KillWait (30 s) later, and --signal above does the same
# before the time limit. So dragon runs in the background: `wait` returns at once
# on SIGTERM (a foreground dragon would hold the trap back until the SIGKILL), and
# after a failure, a cancel or a timeout the EXIT trap stops whatever is left of
# dragon's runtime, then cleans up. The launcher puts itself at the head of a new
# process group as it starts (pgid = its pid), which is what the kills target;
# under setsid it cannot, and hangs at start-up.
dragon_alive() {
    kill -0 -- "-$DRAGON_PID" 2>/dev/null || kill -0 "$DRAGON_PID" 2>/dev/null
}

gone_within() {
    local deadline=$(( SECONDS + $1 ))
    while dragon_alive; do
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 1
    done
}

# SIGINT, as for Ctrl-C: the launcher tears its runtime down. Then SIGKILL for
# whatever is left, including the group of a launcher that already died.
stop_dragon() {
    dragon_alive || return 0
    echo "run_sbatch.sh: stopping dragon (process group $DRAGON_PID)" >&2
    kill -INT "$DRAGON_PID" 2>/dev/null || :
    gone_within 10 && return 0
    kill -KILL -- "-$DRAGON_PID" 2>/dev/null || kill -KILL "$DRAGON_PID" 2>/dev/null || :
    gone_within 5 || :
}

# -s (single-node services only), not -w slurm -t tcp: the latter stands up Dragon's full
# multi-node overlay even for a one-node job, and that overlay has hung here before. Nothing
# in this pipeline is multi-node.
#
# main.py lives under $FG_DIR/src -- stages/ and pipeline.py (the wiring, a
# sibling of main.py, not inside stages/) are its own siblings, so Python's own
# sys.path[0] (the script's own directory) is enough for `import stages` and
# `from pipeline import cageflow` to resolve.
#
# bash starts a background command with SIGINT and SIGQUIT ignored; env puts them
# back to the defaults a foreground dragon gets, so the launcher's SIGINT teardown
# and its children behave as before.
START=$(date +%s)
trap 'exit 143' TERM
env --default-signal=INT,QUIT dragon -s "$FG_DIR/src/main.py" "$CONFIG" &
DRAGON_PID=$!
trap '[ "$?" -eq 0 ] || { stop_dragon; cleanup_shm; }' EXIT
wait "$DRAGON_PID"
WALL=$(( $(date +%s) - START ))
echo "wall_s=$WALL"
echo "$WALL" > "$OUT_DIR/wall_s.txt"
