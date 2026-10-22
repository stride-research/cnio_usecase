"""THE WIRING. Sibling of main.py, not a stage itself -- this is the seam
between Laura's science (stages/) and Javier's orchestration (main.py), so it
gets its own file rather than living inside either directory.

This is the whole pipeline: one line per stage, naming what it is given.
AsyncFlow does not need to be told the order -- every stage is a registered
block, and `handle.run` returns its future immediately. Here, PHASE 1, every
future is awaited on the spot, so execution is serial and in the same order
`adapted/src/main.py` runs today. That is deliberate: it is what makes the
parity check against Track A exact (confirmed: job 24307's group_ddG matches
the established baseline, -56.293125 / 1132.880384, at full float precision).

ADD A STAGE:     write stages/my_stage.py, import it, add one line below.
REMOVE A STAGE:  delete its line. Anything still using its variable is a
                 NameError, immediately, before the cluster is touched.
REORDER:         move the arguments. The order follows the data.

Nothing in stages/ changes for any of those. Laura is free to edit this file
for exactly those three reasons; Javier reviews the PR, since the shape is
also where phase-2 fan-out and phase-3 decision gates will be inserted.

PER-STAGE DISCORD NOTIFICATIONS live here, not in main.py or in any stage, and
not generically inside PipelineHandle either -- deliberately. This is today's
adapted/src/main.py's eight hand-written `fg_discord.notify(...)` calls,
unchanged in wording, moved to the one place that now actually knows the stage
order. A new stage announces itself by being given a line here; nothing can
forget to wire it up because there is nowhere else the order is expressed.

PHASE 2, FOR ORIENTATION ONLY -- not built here: delete the word `await` from
each `handle.run` line below. Futures then flow between stages instead of
values, real dependency edges appear, and the engine overlaps whatever is
independent (the first thing that surfaces: `fold` depends on the designs but
not the Rosetta scores, so it would overlap `score_designs` and `rosetta`). No
stage file changes for that -- only this function does.
"""

from __future__ import annotations

import time

import flowgentic.discord as fg_discord

from stages.design import design
from stages.dock import dock
from stages.export import export
from stages.fold import fold
from stages.geometry import geometry
from stages.rosetta import rosetta
from stages.score_designs import score_designs
from stages.select import select


async def cageflow(handle) -> None:
    """Dock, filter, design, score, fold, export. Same order as today.

    Returns nothing: main.py builds its post-run summary from the output
    files (history.jsonl, compute_stats.json), not from a return value here --
    see main.py's module docstring for why a typed "run result" was tried and
    dropped.
    """
    t0 = time.monotonic()

    def elapsed() -> str:
        """mm:ss since this pipeline started -- the same clock every stage
        ping reads from, so the sequence of Discord messages stays internally
        consistent with itself.
        """
        m, s = divmod(int(time.monotonic() - t0), 60)
        return f"{m:02d}:{s:02d}"

    fg_discord.notify(f"▶️ dock — starting (t+`{elapsed()}`)")
    docked = await handle.run(dock)

    fg_discord.notify(
        f"▶️ geometry — starting (t+`{elapsed()}`), {len(docked)} dock(s) docked")
    geoms = await handle.run(geometry, docked)

    fg_discord.notify(f"▶️ select — starting (t+`{elapsed()}`)")
    selected = await handle.run(select, docked, geoms)

    if not selected:
        # Same early exit as today's main.py: nothing downstream to run. Just
        # stop here -- main.py's wrap-up reads history.jsonl/compute_stats.json
        # regardless of how far the pipeline got, so the funnel it reports
        # (docks docked, 0 selected, 0 of everything after) is already correct
        # with no special-casing needed on its side.
        print("Nothing selected -- nothing downstream to run.")
        return

    fg_discord.notify(
        f"▶️ design — starting (t+`{elapsed()}`), {len(selected)} dock(s) selected")
    designed = await handle.run(design, docked, geoms, selected)

    fg_discord.notify(f"▶️ score_designs — starting (t+`{elapsed()}`)")
    scored = await handle.run(score_designs, docked, geoms, designed)

    fg_discord.notify(f"▶️ rosetta — starting (t+`{elapsed()}`)")
    scores = await handle.run(rosetta, docked, geoms, designed, scored)

    fg_discord.notify(f"▶️ fold — starting (t+`{elapsed()}`)")
    await handle.run(fold, docked, geoms, designed)

    fg_discord.notify(f"▶️ export_af3 — starting (t+`{elapsed()}`)")
    await handle.run(export, docked, designed, scores)
