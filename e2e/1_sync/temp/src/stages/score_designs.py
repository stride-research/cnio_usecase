"""Score-designs stage: CageFlow's own per-dock design summary/ranking, called directly."""
from __future__ import annotations


async def score_designs(handle, docked_oligomers, geometries, designed_oligomers):
    config, out_dir = handle.config, handle.out_dir

    @handle.worker(name="score_designs", description="Per-dock design summary/ranking")
    async def task_score_designs(docked_oligomers: list[dict], geometries: list[dict]):
        import CageFlow as _cf
        return _cf.score_designs(
            designed_oligomers, docked_oligomers, geometries, config, str(out_dir))

    scored_designs = await task_score_designs(docked_oligomers, geometries)

    return scored_designs
