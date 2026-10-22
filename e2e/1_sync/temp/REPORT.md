# Decoupling the pipeline from flowgentic: the shape

Phase A of the plan in `/Users/javierdominguezsegura/.claude/plans/misty-sleeping-stroustrup.md`.
Nothing here has run. The next step (Phase B) moves this to `e2e/1_sync/temp/`,
submits it, and parity-checks against Track A.

## The problem

Laura changes the pipeline often, including adding and removing stages. Today
that is four edits and three land in Javier's files: the stage itself (hers),
a field in `schema/state.py`, a line in `main.py`'s call sequence, and a
Discord ping in `main.py`. Every scientific change routes through him.

The deeper problem is dependency direction: all eight of her stage files do
`import flowgentic.metrics as fg`, so flowgentic cannot change without
breaking her code either. Both directions are blocked.

## The contract

`flowgentic.PipelineHandle` (`handle.py`), four members:

| Member | What |
|---|---|
| `handle.config` | her science parameters + deployment paths resolved at runtime |
| `handle.out_dir` | where this run writes |
| `handle.run(stage, *deps)` | register a stage as a block, return its future |
| `handle.worker(name=, resource=, description=, **asyncflow_kwargs)` | decorator; registers an instrumented function_task |

Her stage files import nothing from flowgentic:

```python
# stages/design.py -- no flowgentic import anywhere
async def design(handle, docked_oligomers, geometries, selected_indices):
    config, out_dir = handle.config, handle.out_dir
    resource = "cpu" if config.get("mpnn_force_cpu") else "gpu"

    @handle.worker(name="design_interface", resource=resource,
                description="ProteinMPNN sequence design for one dock's interface")
    async def task_design(dock_index, docked_oligomer, geometry):
        from CageFlow import _design_one_interface
        return dock_index, _design_one_interface(...)

    results = await asyncio.gather(*(task_design(i, ...) for i in selected_indices))
    ...aggregate, write history...
    return designed_oligomers
```

Four mechanical edits against today's `adapted/src/stages/design.py`, verified
by diff on every file: delete `import flowgentic.metrics as fg`; the signature
`run(flow, config, cage_designer_path, out_dir, state, ...)` becomes
`design(handle, *its named dependencies)`; bind
`config, out_dir = handle.config, handle.out_dir` at the top; `@fg.task(flow,
name=..., resource=..., description=...)` becomes `@handle.worker(name=...,
resource=..., description=...)`. Nothing between the decorator and the return
moved — confirmed by diffing every migrated file against a sed-mechanized
version of the original.

## The wiring — phase 1, strictly serial

```python
# src/pipeline.py -- sibling of main.py, Laura's file, Javier reviews the PR
async def cageflow(handle) -> None:
    docked   = await handle.run(dock)
    geoms    = await handle.run(geometry, docked)
    selected = await handle.run(select, docked, geoms)
    designed = await handle.run(design, docked, geoms, selected)
    scored   = await handle.run(score_designs, docked, geoms, designed)
    scores   = await handle.run(rosetta, docked, geoms, designed, scored)
    await handle.run(fold, docked, geoms, designed)
    await handle.run(export, docked, designed, scores)
```

Same stage order as today's `adapted/src/main.py`. Add a stage: new file,
import it, add a line. Remove one: delete the line — anything still using its
variable is a `NameError` before the cluster is touched. Reorder: move the
arguments.

**Every stage is a block even though the wiring awaits each one immediately.**
Two edges, easy to conflate:

| Edge | Mechanism | Needs a block? |
|---|---|---|
| worker → worker, inside one stage | pass the future as an argument | no |
| stage → stage | the stage must itself be a registered component | **yes** |

A block is what lets a stage hand back a receipt instead of forcing a wait.
Phase 1 awaits that receipt on the spot, so execution is serial — but the
stage is a real engine component (attributable, cancellable via
`_block_members`), and **phase 2 is deleting the word `await`**, not a
restructure.

Dependencies are passed **positionally**. `_register_component` detects them
from `comp_desc["args"]` only (`workflow_manager.py:756`) — there is exactly
one call site, and nothing scans `kwargs`. The Best Practices page says
"positional or keyword"; the installed code disagrees. `handle.run`'s signature
(`def run(self, stage, *deps)`) makes a keyword future a `TypeError` at the
call site, so the trap is unreachable here. Worth telling Aymen.

## No `Results` dataclass

Tried, then dropped. Two reasons: it is a return-value contract between her
wiring and `main.py`, and its "one value per stage" shape stops being true in
phase 2 when stages fan out per candidate. `main.py` instead builds its
Discord funnel from the run's **output files** — `compute_stats.json` for
task counts and `history.jsonl` for per-dock status — the same pattern
`metrics/collect.py` already uses without importing CageFlow.

## Ownership after this

```
e2e/1_sync/temp/
  src/main.py             JAVIER   engine, env, logging, metrics, Discord
  src/pipeline.py         LAURA    the wiring (Javier reviews the PR) --
                                   sibling of main.py, NOT inside stages/:
                                   it is the seam between the two, not a
                                   stage and not pure orchestration either
  src/stages/*.py         LAURA    one file per stage, zero flowgentic imports
  config/config.json      LAURA
  tests/test_no_module_capture.py   checked by both, runs with no cluster
flowgentic/src/flowgentic/
  handle.py               JAVIER   PipelineHandle
  metrics.py              JAVIER   worker-side primitives (_prepare/_snapshot/_write)
```

Mirrors `adapted/`'s own `src/` layout exactly, which is also what let the
`HERE` path bug below get caught quickly: `main.py`'s own path arithmetic has
to agree with `run_sbatch.sh`'s, and nothing checks that automatically.

## The capture test, extended and verified

`tests/test_no_module_capture.py` is the existing AST guard, moved and given
one new rule: a worker body must never reference `handle` (it holds the engine;
closing over it would try to pickle the engine into the Dragon child).

Ran it against the real migrated files — it initially FAILED on all eight,
flagging the `@handle.worker(...)` decorator as capturing `handle`. Real bug: `ast.walk(fn)`
walks a `FunctionDef`'s `decorator_list` along with its body, so the decorator
(evaluated in the parent, before any worker runs) was being scanned as if it
were inside the worker. Fixed by walking only `fn.body`. Re-verified three ways
after the fix:

```
$ python3 tests/test_no_module_capture.py
OK: no worker body in any scanned file captures a parent-scope module,
a bare name imported from one, or handle
```

- the real job-24049 shape (module imported in the parent, referenced in the
  worker with no local import) is caught: `['task_x: CageFlow.DEFAULT_CROSS_CONTACT_DIST (line 9)']`
- a worker referencing `handle` directly is caught: `['task_x: handle (line 5)']`
- the correct pattern (config/out_dir bound before the worker is defined, local
  imports inside it) produces no violations

## Risks and follow-ons (unchanged from the plan)

| Item | Note |
|---|---|
| `metrics.configure()` / `metrics.task()` unused | Keep as deprecated. Only `adapted/src/*` and the old test called them; Track A and `isolation/` do not (verified by grep). Remove once `adapted/` is retired |
| Per-stage timing | Not built. AsyncFlow already tracks component times via `backend.register_callback`; hook that rather than hand-timing |
| Futures nested in containers | Not reachable in phase 1 (stages receive resolved values). Worth an AST lint before the phase-2 wiring fans out per candidate |

## What phase 2 adds (orientation only, not built)

1. Delete the `await`s from the wiring — futures flow, edges appear, `fold`
   overlaps `score_designs`/`rosetta` since it never depended on the scores.
2. Fan out per candidate — stages split into `_one` variants, one block per
   stage per dock.
3. Enforce resource counts through the `**asyncflow_kwargs` path `handle.worker`
   already forwards to `task_description`.

Expect when (1) lands: `history.jsonl` append order stops being deterministic
(sort by dock index before diffing), and PyRosetta/ColabFold may oversubscribe
the 8-CPU allocation. Neither is a phase-1 concern.

## Two non-blocking questions for Laura

1. Does `score_designs` compare docks against each other, or just receive them
   all? `rosetta` depends on its output but reads only its own dock's entry
   (`scored_designs.get(i)`, one history field).
2. Should `export` wait for `fold_results`? It does not read them today.

## Phase B: run on the cluster, parity result

Done. Job **24307** (`cf_handle`, `fortran`, `wall_s=208`) ran this tree
end to end. No fresh Track A was submitted for comparison -- there is
already a recently re-verified baseline (jobs 24292/24293, 2026-10-21,
`PARITY_REPORT.md`), and re-running Track A just to diff against it would
have burned shared GPU time on the `biostructure` node for no reason. (One
was submitted by mistake mid-session as job 24308 and cancelled ~1 minute
in, once caught.)

**Science: exact match at full float precision.**

```
dock 1  group_ddG = -56.29312481189004   (baseline: -56.293125)
dock 2  group_ddG = 1132.880383650814    (baseline: 1132.880384)
```

Both `qualifying_sequences.csv` rows and `history.jsonl`'s `group_ddg` fields
read directly off the cluster. The restructure changes nothing about the
science.

**Two real bugs found by actually running it, both fixed, neither visible
from reading the code:**

1. **`main.py`'s `out_dir` silently diverged from `run_sbatch.sh`'s `OUT_DIR`.**
   `HERE = Path(__file__).resolve().parent` was correct when `main.py` lived
   flat under `temp/`, and stayed wrong after it moved to `src/main.py`: `HERE`
   became `temp/src/`, so `out_dir` computed to `temp/src/results` while the
   launch script pre-creates and expects `temp/results`. The job still
   completed -- `cageflow.log`, `history.jsonl`, `compute_stats.json`, every
   per-dock subdirectory, all landed at `src/results/` instead, nothing was
   lost, but two independently-computed paths had silently disagreed, which is
   the exact failure mode this project has already been bitten by once
   (CLAUDE.md, the three-pieces-nothing-checks-they-agree note). Fixed:
   `HERE = Path(__file__).resolve().parent.parent`, matching `adapted/`'s own
   convention exactly.
2. **The per-stage Discord notifications were dropped during the restructure,
   not deliberately removed.** `main.py` lost its hardcoded stage sequence
   (the point of this whole design), and the eight `fg_discord.notify(...)`
   calls that lived alongside that sequence went with it, with nothing put in
   their place. Restored in `pipeline.py` -- the one place that still
   expresses the stage order -- with the exact original wording, plus the
   `if not selected: return` early exit `adapted/src/main.py` has, which
   `pipeline.py` also silently lacked until now.

**One structural change made in the same pass, not a bug but requested
directly:** `pipeline.py` moved out of `stages/` to sit beside `main.py`.
`stages/` is pure science with zero flowgentic imports; `pipeline.py` is the
seam between that and the orchestration, which is neither, so it gets its own
file. Mirrors `adapted/`'s own `src/` layout exactly now.

Not yet re-run on the cluster since these fixes landed -- the confirmed
parity numbers above are from before the `HERE` fix (output just had to be
read from the unintended `src/results/` location). Worth one more cheap run
to confirm the path fix lands results in the expected `temp/results/` and
that the restored Discord messages actually post, before calling Phase 1
fully done.
