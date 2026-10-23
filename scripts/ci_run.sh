#!/usr/bin/env bash
# Runs one CI check: clone a ref, submit it, wait for the result. Called by
# .github/workflows/ci.yml and baseline.yml; Actions-specific bits (cancel
# handling, step summary) live in the workflow, not here.
#
# Usage: scripts/ci_run.sh <ref> <rundir> [config_name]
#   <ref>         branch, tag, or commit SHA
#   <rundir>      permanent output dir, e.g. .../ci_runs/pr-<n>-<sha>/
#   [config_name] committed config to run, default config.json
#
# Uses Javier's existing ~/.venvs and ~/software/ deployment unchanged -- every
# run executes as javidsegura regardless of whose PR triggered it, so there's
# nothing to share or rebuild at that layer.
set -euo pipefail

REF="${1:?usage: ci_run.sh <ref> <rundir> [config_name]}"
RUNDIR="${2:?usage: ci_run.sh <ref> <rundir> [config_name]}"
CONFIG_NAME="${3:-config.json}"
REPO_URL="https://github.com/stride-research/cnio_usecase.git"

mkdir -p "$RUNDIR"

# Scratch clone goes under $HOME, not /tmp: /tmp is node-local, but the Slurm
# job may land on a different node than this script runs on, so a node-local
# path is invisible to it. Plain $HOME/ci_scratch, not ~/.cache -- .cache
# carries an unrelated ACL that denies writes partway through.
mkdir -p "$HOME/ci_scratch"
SCRATCH=$(mktemp -d --tmpdir="$HOME/ci_scratch")
trap 'rm -rf "$SCRATCH"' EXIT

# fetch + checkout FETCH_HEAD, not `clone --branch`: CI passes a raw commit
# SHA, which --branch can't resolve but this handles either form.
git -C "$SCRATCH" init --quiet
git -C "$SCRATCH" remote add origin "$REPO_URL"
git -C "$SCRATCH" fetch --quiet --depth 1 origin "$REF"
git -C "$SCRATCH" checkout --quiet FETCH_HEAD
printf 'repo=%s\nref=%s\nsha=%s\n' "$REPO_URL" "$REF" \
    "$(git -C "$SCRATCH" rev-parse HEAD)" > "$RUNDIR/source.txt"

TRACK_DIR="$SCRATCH/e2e/1_sync/adapted"
cd "$TRACK_DIR"

# CNIO_CI_OUT is exported into the submitting shell, NOT passed via
# `sbatch --export=ALL,CNIO_CI_OUT=...`. Using the flag sets SLURM_EXPORT_ENV
# inside the job, which Dragon's child processes then inherit as a restriction
# -- they lose the env carrying Dragon's serialized channel/pool descriptors and
# die in connect_to_infrastructure() with "could not attach to memory pool",
# while the parent looks healthy and Slurm keeps reporting RUNNING. Confirmed by
# bisection: identical job, same node, differing only in this flag, fails within
# 60s with --export and completes in 3:33 without it. Plain sbatch already
# propagates the submitting environment and leaves SLURM_EXPORT_ENV unset.
export CNIO_CI_OUT="$RUNDIR/results"
JOBID=$(sbatch --parsable scripts/run_sbatch.sh "$TRACK_DIR/config/$CONFIG_NAME")
echo "$JOBID" > "$RUNDIR/.jobid"

# sbatch --wait only works at submission -- it can't attach to an existing job
# ID -- so poll sacct instead. Submit and wait are separate steps (not one
# `sbatch --wait` call) so $RUNDIR/.jobid exists immediately for the
# cancel-on-cancelled workflow step.
while true; do
    STATE=$(sacct -j "$JOBID" -X --noheader --format=State 2>/dev/null | tr -d ' ')
    case "$STATE" in
        COMPLETED) STATUS=0; break ;;
        FAILED|CANCELLED*|TIMEOUT|OUT_OF_MEMORY|NODE_FAIL|BOOT_FAIL|DEADLINE) STATUS=1; break ;;
        *) sleep 15 ;;
    esac
done

# sbatch's -o/-e are relative to cwd at submission, so they land in
# $TRACK_DIR -- inside $SCRATCH, which the trap deletes on exit. Copy them out
# first; results/ was already written straight to $RUNDIR.
cp -f "$TRACK_DIR/sbatch.out" "$RUNDIR/sbatch.out" 2>/dev/null || true
cp -f "$TRACK_DIR/sbatch.err" "$RUNDIR/sbatch.err" 2>/dev/null || true

exit "$STATUS"
