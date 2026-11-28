"""Select stage: CageFlow's own select_top_docks(), called directly and unmodified --
it never took an Executor/Budget in the first place, so there is nothing to vendor here.

A dispatched task, not a parent call: its cost grows with the number of docks, and a
blocking call in the parent freezes the event loop once stages overlap. It receives dock
file paths (see dock_files.py), not the docks themselves.
"""
from __future__ import annotations

import logging


async def select(handle, dock_files, geometries):
    config, out_dir = handle.config, handle.out_dir

    @handle.worker(name="select_top_docks",
                   description="Rank and filter docks; pure sort/filter over the dock files")
    async def task_select(dock_files: list[str], geometries: list[dict]):
        import pickle
        import CageFlow as _cf

        docked_oligomers = []
        for path in dock_files:
            with open(path, "rb") as f:
                docked_oligomers.append(pickle.load(f))
        return _cf.select_top_docks(docked_oligomers, geometries, config, str(out_dir))

    selected_indices = await task_select(dock_files, geometries)
    logging.info(f"[select] selected {selected_indices}")

    return selected_indices
