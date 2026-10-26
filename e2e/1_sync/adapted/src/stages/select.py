"""Select stage: CageFlow's own select_top_docks(), called directly and unmodified --
it never took an Executor/Budget in the first place, so there is nothing to vendor here.
"""
from __future__ import annotations

import logging


async def select(handle, docked_oligomers, geometries):
    import CageFlow

    config, out_dir = handle.config, handle.out_dir

    # Called here in the parent, not in a worker: it is ~0.2 s of sorting, and a worker is
    # shipped its arguments by value -- every dock whole, assembly PDB text and copies,
    # about 5.5 MB per dock for 5dii. Her run_cage_flow() calls it in-process too.
    selected_indices = CageFlow.select_top_docks(
        docked_oligomers, geometries, config, str(out_dir))
    logging.info(f"[select] selected {selected_indices}")

    return selected_indices
