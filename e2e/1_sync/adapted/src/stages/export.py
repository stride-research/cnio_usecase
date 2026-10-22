"""Export stage: writes the AF3 job manifest (export_af3_jobs()), called directly."""
from __future__ import annotations

import logging


async def export(handle, docked_oligomers, designed_oligomers, rosetta_scores):
    config, out_dir = handle.config, handle.out_dir

    @handle.worker(name="export_af3_jobs", description="Write the AF3 job manifest")
    async def task_export_af3(docked_oligomers: list[dict]):
        import CageFlow as _cf
        return _cf.export_af3_jobs(
            designed_oligomers, docked_oligomers, rosetta_scores, config, str(out_dir))

    manifest = await task_export_af3(docked_oligomers)
    logging.info(f"[export_af3] {len(manifest)} job(s) exported")

    return manifest
