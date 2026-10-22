"""Select stage: CageFlow's own select_top_docks(), called directly and unmodified --
it never took an Executor/Budget in the first place, so there is nothing to vendor here.
"""
from __future__ import annotations

import logging


async def select(handle, docked_oligomers, geometries):
    config, out_dir = handle.config, handle.out_dir

    @handle.worker(name="select_top_docks",
                description="Rank and filter docks; pure sort/filter over in-memory data")
    async def task_select(docked_oligomers: list[dict], geometries: list[dict]):
        import CageFlow as _cf
        return _cf.select_top_docks(docked_oligomers, geometries, config, str(out_dir))

    selected_indices = await task_select(docked_oligomers, geometries)
    logging.info(f"[select] selected {selected_indices}")

    return selected_indices
