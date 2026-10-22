"""THE WIRING. LAURA'S FILE (Javier reviews the PR).

This is the whole pipeline: one line per stage, naming what it is given.
AsyncFlow does not need to be told the order -- every stage is a registered
block, and `handle.run` returns its future immediately. Here, PHASE 1, every
future is awaited on the spot, so execution is serial and in the same order
`adapted/src/main.py` runs today. That is deliberate: it is what makes the
parity check against Track A exact.

ADD A STAGE:     write stages/my_stage.py, import it, add one line below.
REMOVE A STAGE:  delete its line. Anything still using its variable is a
                 NameError, immediately, before the cluster is touched.
REORDER:         move the arguments. The order follows the data.

Nothing outside this directory changes for any of those.

PHASE 2, FOR ORIENTATION ONLY -- not built here: delete the word `await` from
each line below. Futures then flow between stages instead of values, real
dependency edges appear, and the engine overlaps whatever is independent (the
first thing that surfaces: `fold` depends on the designs but not the Rosetta
scores, so it would overlap `score_designs` and `rosetta`). No stage file
changes for that -- only this function does.
"""

from __future__ import annotations

from .design import design
from .dock import dock
from .export import export
from .fold import fold
from .geometry import geometry
from .rosetta import rosetta
from .score_designs import score_designs
from .select import select


async def cageflow(handle) -> None:
    """Dock, filter, design, score, fold, export. Same order as today."""
    docked   = await handle.run(dock)
    geoms    = await handle.run(geometry, docked)
    selected = await handle.run(select, docked, geoms)
    designed = await handle.run(design, docked, geoms, selected)
    scored   = await handle.run(score_designs, docked, geoms, designed)
    scores   = await handle.run(rosetta, docked, geoms, designed, scored)
    await handle.run(fold, docked, geoms, designed)
    await handle.run(export, docked, designed, scores)
