"""Entry point. Replaces adapted/src/main.py. JAVIER'S FILE.

Everything here is system work: engine setup, environment, logging, metrics,
notifications. There is no science in this file and no pipeline shape: the
shape is one import, from `pipeline.py` (sibling of this file). Compare with today's
`adapted/src/main.py`, where the eight `state = await <step>.run(...)` lines
are both the shape and the only place a new step could be added.

What stayed identical to today, because all of it was learned the hard way:
  * `import dragon` before mp.set_start_method("dragon")
  * sys.path gets cageflow_dir AND cage_designer_path in the PARENT, not just in
    worker bodies: some of her parent-side helpers (_n_to_materialize ->
    _prescore_rank_key) do `from src.cbeta_interface import ...` internally
    (job 24018 crashed without it)
  * attach_log_file() is called AGAIN after WorkflowEngine.create(), because
    Dragon's bring-up calls basicConfig(force=...) and drops handlers it did
    not install (job 24196)
  * CageFlow-owned defaults are resolved HERE and written into config, so no
    worker body ever references the CageFlow module for a constant (job 24049
    hung every run on exactly that)
  * FLOWGENTIC_ENV_FILE, so run_sbatch.sh can say where .env is rather than
    relying on a computed fallback

WHY THE FUNNEL IS BUILT FROM FILES, NOT FROM A RETURN VALUE: the pipeline
wiring (`pipeline.py`) returns nothing. A typed "run result" dataclass
was tried and dropped -- it is a contract between her wiring and this file,
and its "one value per stage" shape stops being true the moment a stage fans
out per candidate. Reading `compute_stats.json` and `history.jsonl` instead is
already the pattern for `scored` below, and it is exactly what
`metrics/collect.py` does without importing CageFlow at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing as mp
import os
import socket
import sys
import time
from pathlib import Path

import dragon  # noqa: F401  (must be imported before mp.set_start_method("dragon"))

from dotenv import load_dotenv
from radical.asyncflow import WorkflowEngine
from rhapsody.backends import DragonExecutionBackendV1

import flowgentic.discord as fg_discord
import flowgentic.metrics as fg_metrics
from flowgentic.handle import PipelineHandle

# main.py lives at src/main.py -- .parent.parent, not .parent, to land on the
# track root (temp/), matching adapted/'s own convention and run_sbatch.sh's
# $FG_DIR. Confirmed broken the hard way: job 24307 ran clean but wrote
# cageflow.log/history.jsonl/compute_stats.json to src/results/ while
# run_sbatch.sh's OUT_DIR pre-created and expected temp/results/ -- two
# independently-computed paths silently diverging, the exact failure mode
# already documented elsewhere in this project (three pieces, nothing checks
# they agree). Science was unaffected (parity confirmed against that run's
# output); only the output LOCATION was wrong.
HERE = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "config" / "config.json"

ENV_PATH = (Path(os.environ["FLOWGENTIC_ENV_FILE"])
            if os.environ.get("FLOWGENTIC_ENV_FILE")
            else HERE.parent.parent.parent / ".env")
load_dotenv(ENV_PATH)


def load_config() -> dict:
    """Science parameters from the file; deployment paths from $HOME at runtime.

    Same split as today: a committed config works under whoever's home it runs
    in, so the deployment-location keys are deliberately absent from the file
    and are overwritten here.
    """
    cfg = json.loads(CONFIG_PATH.read_text())
    home = Path.home()
    cageflow_dir = home / "software" / "CageFlow"
    colabfold = home / "software" / "localcolabfold_install" / "localcolabfold"
    cfg.update({
        "cageflow_dir": str(cageflow_dir),
        "cage_designer_path": str(home / "software" / "ProteinBuilder"),
        "mpnn_dir": str(home / "software" / "ProteinMPNN"),
        "scaffold_pdb": str(cageflow_dir / "scaffolds" / "5dii_trimer.pdb"),
        "colabfold_bin": str(colabfold / "colabfold-conda" / "bin" / "colabfold_batch"),
        "colabfold_data": str(colabfold / "colabfold"),
        "colabfold_python": str(colabfold / "colabfold-conda" / "bin" / "python"),
        "out_dir": str(HERE / "results"),
    })
    return cfg


def _funnel(out_dir: Path, stats: list[dict]) -> dict:
    """The Discord summary, read from her documented output files, not from a
    return value. Same field names/shape `metrics/collect.py` already reads
    out of `history.jsonl` (one record per (dock, stage-outcome), last status
    per dock-index wins).
    """
    history_path = out_dir / "history.jsonl"
    records = ([json.loads(line) for line in history_path.read_text().splitlines() if line.strip()]
               if history_path.exists() else [])

    last_status: dict[int, str] = {}
    n_folded_by_dock: dict[int, int] = {}
    for rec in records:
        idx = rec.get("dock_index")
        if idx is None:
            continue
        last_status[idx] = rec.get("status", last_status.get(idx))
        n_folded_by_dock[idx] = (rec.get("design") or {}).get("n_folded", n_folded_by_dock.get(idx, 0))

    selected_statuses = {"designed", "design_failed", "rosetta_scored", "folded"}
    rosetta_row = next((r for r in stats if r["stage"] == "score_with_rosetta"), None)

    return {
        "docks": len(last_status),
        "selected": sum(1 for s in last_status.values() if s in selected_statuses),
        "designed": sum(1 for s in last_status.values()
                        if s in {"designed", "rosetta_scored", "folded"}),
        "scored": rosetta_row["n_tasks"] if rosetta_row else 0,
        "folded": sum(n_folded_by_dock.values()),
        # "exported" is not in history.jsonl (export_af3_jobs writes no history
        # record of its own) -- main() fills it in from qualifying_sequences.csv
        # after this function returns.
    }


async def main():
    mp.set_start_method("dragon")

    config = load_config()
    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    log_path = out_dir / "cageflow.log"
    logging.basicConfig(level=logging.INFO, format=fg_metrics.LOG_FORMAT,
                        handlers=[logging.StreamHandler()])
    fg_metrics.attach_log_file(log_path)

    sys.path.insert(0, config["cageflow_dir"])
    sys.path.insert(0, config["cage_designer_path"])
    import CageFlow

    CageFlow.reset_history(out_dir)

    # Resolved here, in the parent, and written into config. A worker body that
    # reaches for CageFlow.DEFAULT_CROSS_CONTACT_DIST instead pickles the module
    # by reference, and the Dragon child cannot import it back: the task
    # submits, the child launches, and the future never resolves (job 24049).
    config.setdefault("cross_contact_dist", CageFlow.DEFAULT_CROSS_CONTACT_DIST)
    config.setdefault("cross_clash_dist", 3.0)

    metrics_dir = out_dir / "metrics"
    metrics_dir.mkdir(parents=True, exist_ok=True)
    for stale in metrics_dir.glob("*.jsonl"):
        stale.unlink()

    job_id = os.environ.get("SLURM_JOB_ID", "local")
    t0 = time.monotonic()
    fg_discord.run_started(job_id=job_id, node=socket.gethostname(), config=config)

    backend = await DragonExecutionBackendV1()
    flow = await WorkflowEngine.create(backend=backend)
    # Again: Dragon's bring-up above calls basicConfig(force=...) and drops
    # handlers it did not install, so without this cageflow.log holds only the
    # few lines written before this point (job 24196).
    fg_metrics.attach_log_file(log_path)

    handle = PipelineHandle(
        flow, config, out_dir,
        metrics_dir=metrics_dir,
        sys_paths=(config["cageflow_dir"], config["cage_designer_path"]),
        log_path=log_path,
    )

    # The pipeline shape lives in pipeline.py, a sibling of this file -- not
    # inside stages/ (that package is science only) and not here (this file
    # names it once and never expresses the order itself).
    from pipeline import cageflow

    await cageflow(handle)

    # ------------------------------------------------------------- wrap up
    table = CageFlow.build_sequence_table(out_dir)
    table.to_csv(out_dir / "qualifying_sequences.csv", index=False)
    CageFlow.style_sequence_table(table).to_html(out_dir / "qualifying_sequences.html")

    stats = fg_metrics.compute_stats(metrics_dir)
    (out_dir / "compute_stats.json").write_text(json.dumps(stats, indent=2))

    n_qualifying = max(0, len(table.index))
    funnel = _funnel(out_dir, stats)
    funnel["exported"] = n_qualifying

    fg_discord.run_finished(
        job_id=job_id, wall_s=time.monotonic() - t0, funnel=funnel, out_dir=out_dir)

    await flow.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
