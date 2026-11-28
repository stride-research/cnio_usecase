"""Export stage: writes the AF3 job manifest (export_af3_jobs()), called directly.

A dispatched task receiving dock file paths (see dock_files.py), for the same reason as
select.py.
"""
from __future__ import annotations

import logging


async def export(handle, dock_files, designed_oligomers, rosetta_scores):
    config, out_dir = handle.config, handle.out_dir

    @handle.worker(name="export_af3_jobs", description="Write the AF3 job manifest")
    async def task_export_af3(dock_files: list[str]):
        import pickle
        import CageFlow as _cf

        docked_oligomers = []
        for path in dock_files:
            with open(path, "rb") as f:
                docked_oligomers.append(pickle.load(f))
        return _cf.export_af3_jobs(
            designed_oligomers, docked_oligomers, rosetta_scores, config, str(out_dir))

    manifest = await task_export_af3(dock_files)
    logging.info(f"[export_af3] {len(manifest)} job(s) exported")

    return manifest
