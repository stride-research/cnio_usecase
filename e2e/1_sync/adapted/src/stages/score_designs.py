"""Score-designs stage: CageFlow's own per-dock design summary/ranking, called directly.

A dispatched task receiving dock file paths (see dock_files.py), for the same reason as
select.py.
"""
from __future__ import annotations


async def score_designs(handle, dock_files, geometries, designed_oligomers):
    config, out_dir = handle.config, handle.out_dir

    @handle.worker(name="score_designs", description="Per-dock design summary/ranking")
    async def task_score_designs(dock_files: list[str], geometries: list[dict]):
        import pickle
        import CageFlow as _cf

        docked_oligomers = []
        for path in dock_files:
            with open(path, "rb") as f:
                docked_oligomers.append(pickle.load(f))
        return _cf.score_designs(
            designed_oligomers, docked_oligomers, geometries, config, str(out_dir))

    scored_designs = await task_score_designs(dock_files, geometries)

    return scored_designs
