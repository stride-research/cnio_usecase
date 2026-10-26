"""Export stage: writes the AF3 job manifest (export_af3_jobs()), called directly."""
from __future__ import annotations

import logging


async def export(handle, docked_oligomers, designed_oligomers, rosetta_scores):
    import CageFlow

    config, out_dir = handle.config, handle.out_dir

    # In the parent, not in a worker, for the same reason as select.py: her docstring calls
    # it cheap, and a worker would be shipped every dock whole to do it.
    manifest = CageFlow.export_af3_jobs(
        designed_oligomers, docked_oligomers, rosetta_scores, config, str(out_dir))
    logging.info(f"[export_af3] {len(manifest)} job(s) exported")

    return manifest
