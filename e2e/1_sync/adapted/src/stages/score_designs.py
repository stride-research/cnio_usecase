"""Score-designs stage: CageFlow's own per-dock design summary/ranking, called directly."""
from __future__ import annotations


async def score_designs(handle, docked_oligomers, geometries, designed_oligomers):
    import CageFlow

    config, out_dir = handle.config, handle.out_dir

    # In the parent, not in a worker, for the same reason as select.py: ~0.15 s of
    # bookkeeping, against every dock shipped whole to a worker.
    scored_designs = CageFlow.score_designs(
        designed_oligomers, docked_oligomers, geometries, config, str(out_dir))

    return scored_designs
