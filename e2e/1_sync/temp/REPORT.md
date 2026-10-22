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

`flowgentic.PipelineHandle` (`context.py`), four members:

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
# stages/pipeline.py -- Laura's file, Javier reviews the PR
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
  main.py                JAVIER   engine, env, logging, metrics, Discord
  stages/pipeline.py     LAURA    the wiring (Javier reviews the PR)
  stages/*.py            LAURA    one file per stage, zero flowgentic imports
  config/config.json     LAURA
  tests/test_no_module_capture.py   checked by both, runs with no cluster
flowgentic/src/flowgentic/
  context.py             JAVIER   PipelineHandle
  metrics.py             JAVIER   worker-side primitives (_prepare/_snapshot/_write)
```

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

## Next: Phase B

Move to `e2e/1_sync/temp/`, install `context.py` into
`flowgentic/src/flowgentic/`, wire `run_sbatch.sh`, submit alongside Track A,
and accept only on byte-identical `qualifying_sequences.csv` + field-identical
`history.jsonl` + `group_ddG` matching the current baseline
(`-56.293125` / `1132.880384`).
