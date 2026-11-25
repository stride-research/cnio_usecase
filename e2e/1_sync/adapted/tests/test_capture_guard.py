"""Tests for the capture guard itself, on synthetic sources.

test_no_module_capture.py runs the guard over the real stage files, which are
all clean, so on its own it cannot show that the guard catches anything. Each
test here feeds the guard a small stage that does one thing and checks the
verdict: the unsafe shapes are reported, the pattern the stage files use is not.
"""
from __future__ import annotations

import textwrap

import pytest

import test_no_module_capture as guard


def _module(source: str) -> list[str]:
    return guard.find_module_captures(textwrap.dedent(source))


def _ctx(source: str) -> list[str]:
    return guard.find_ctx_captures(textwrap.dedent(source))


def _names(violations: list[str]) -> set[str]:
    """The captured name in each 'worker: name (line N) ...' violation."""
    return {v.split(": ", 1)[1].split(" ", 1)[0].split(".", 1)[0] for v in violations}


# ------------------------------------------------------------ loose modules

def test_job_24049_module_imported_in_stage_read_in_worker():
    found = _module("""
        async def geometry(handle):
            import CageFlow
            config = handle.config

            @handle.worker(name="x")
            async def task_x():
                return CageFlow.DEFAULT_CROSS_CONTACT_DIST
    """)
    assert _names(found) == {"CageFlow"}
    assert found[0].startswith("task_x: CageFlow") and "(line 8)" in found[0]


def test_from_import_in_stage_body():
    found = _module("""
        async def dock(handle):
            from CageFlow import _materialize_dock

            @handle.worker(name="x")
            async def task_x(sol):
                return _materialize_dock(sol)
    """)
    assert _names(found) == {"_materialize_dock"}


def test_from_import_at_module_top():
    found = _module("""
        from CageFlow import _materialize_dock

        async def dock(handle):
            @handle.worker(name="x")
            async def task_x(sol):
                return _materialize_dock(sol)
    """)
    assert _names(found) == {"_materialize_dock"}


@pytest.mark.parametrize("where", ["module", "stage"])
def test_aliased_module_import(where):
    imp = "import CageFlow as cf"
    found = _module(f"""
        {imp if where == "module" else ""}
        async def dock(handle):
            {imp if where == "stage" else ""}

            @handle.worker(name="x")
            async def task_x():
                return cf.MATERIALIZE_FLOOR
    """)
    assert _names(found) == {"cf"}


def test_function_from_a_loose_helper_module():
    """The second incident: a helper shared from a source file beside the job."""
    found = _module("""
        from common import _task_setup

        async def fold(handle):
            @handle.worker(name="x")
            async def task_x():
                _task_setup()
    """)
    assert _names(found) == {"_task_setup"}


@pytest.mark.parametrize("imp, name", [
    ("import src.docking", "src"),
    ("from src.docking import scan_docking", "scan_docking"),
    ("from . import records", "records"),
    ("from .records import is_two_component", "is_two_component"),
])
def test_submodule_and_relative_imports(imp, name):
    found = _module(f"""
        {imp}

        async def dock(handle):
            @handle.worker(name="x")
            async def task_x():
                return {name}
    """)
    assert _names(found) == {name}


def test_alias_of_a_module_member():
    found = _module("""
        async def dock(handle):
            import CageFlow
            materialize = CageFlow._materialize_dock

            @handle.worker(name="x")
            async def task_x(sol):
                return materialize(sol)
    """)
    assert _names(found) == {"materialize"}


def test_function_defined_at_module_level():
    """It pickles by reference to the stage module, which the child cannot import."""
    found = _module("""
        def _radius_window(dr):
            return dr["radius"]

        async def dock(handle):
            config = handle.config

            @handle.worker(name="x")
            async def task_x():
                return _radius_window(config["dock_ranges"])
    """)
    assert _names(found) == {"_radius_window"}


def test_helper_in_stage_that_captures_a_module():
    found = _module("""
        async def dock(handle):
            import CageFlow
            config = handle.config

            def spin_period(pdb):
                return CageFlow._spin_period(pdb, config)

            @handle.worker(name="x")
            async def task_x(pdb):
                return spin_period(pdb)
    """)
    assert _names(found) == {"spin_period"}
    assert "CageFlow" in found[0]


def test_default_argument_value():
    found = _module("""
        async def dock(handle):
            from CageFlow import _materialize_dock

            @handle.worker(name="x")
            async def task_x(sol, materialize=_materialize_dock):
                return materialize(sol)
    """)
    assert len(found) == 1 and "_materialize_dock" in found[0]


def test_binding_in_a_nested_scope_does_not_cover_the_worker():
    found = _module("""
        async def dock(handle):
            import CageFlow

            @handle.worker(name="x")
            async def task_x():
                def inner():
                    import CageFlow
                    return CageFlow
                return CageFlow.MATERIALIZE_FLOOR, inner
    """)
    assert _names(found) == {"CageFlow"}


def test_capture_from_a_nested_scope_inside_the_worker():
    found = _module("""
        import CageFlow

        async def dock(handle):
            @handle.worker(name="x")
            async def task_x(sols):
                def score(s):
                    return CageFlow.score(s)
                return [CageFlow.rank(score(s)) for s in sols]
    """)
    assert _names(found) == {"CageFlow"}


def test_only_stdlib_and_allowlisted_packages_are_importable_by_the_child():
    found = _module("""
        import json
        import flowgentic.metrics as fg_metrics
        import numpy as np

        async def score(handle):
            @handle.worker(name="x")
            async def task_x():
                return json.dumps, fg_metrics.LOG_FORMAT, np.mean
    """)
    assert _names(found) == {"np"}


# ------------------------------------------------------------------- handle

def test_handle_read_inside_worker():
    found = _ctx("""
        async def select(handle):
            @handle.worker(name="x")
            async def task_x():
                return handle.config["top_n"]
    """)
    assert _names(found) == {"handle"}


@pytest.mark.parametrize("binding", [
    "h = handle",
    "h = handle._flow",
    "h = handle.run",
    "cfg, h = handle.config, handle",
    "h: object = handle",
])
def test_alias_of_handle(binding):
    found = _ctx(f"""
        async def select(handle):
            {binding}

            @handle.worker(name="x")
            async def task_x():
                return h
    """)
    assert _names(found) == {"h"}


def test_alias_of_an_alias_of_handle():
    found = _ctx("""
        async def select(handle):
            h = handle
            flow = h._flow

            @handle.worker(name="x")
            async def task_x():
                return flow
    """)
    assert _names(found) == {"flow"}


def test_handle_as_default_argument_value():
    found = _ctx("""
        async def select(handle):
            @handle.worker(name="x")
            async def task_x(h=handle, cfg=handle.config):
                return h, cfg
    """)
    assert len(found) == 1 and "handle" in found[0] and "cfg" not in found[0]


def test_worker_capturing_another_worker():
    """A registered worker is AsyncFlow's task wrapper, which holds the engine."""
    found = _ctx("""
        async def dock(handle):
            @handle.worker(name="a")
            async def task_a():
                return 1

            @handle.worker(name="b")
            async def task_b():
                return await task_a()
    """)
    assert _names(found) == {"task_a"}


# ---------------------------------------------------- what must stay allowed

#: The shape every stage file uses: plain data off `handle` before the worker,
#: CageFlow imported in the stage for parent-side use and again inside the
#: worker for the child, stdlib and flowgentic used freely.
CORRECT_STAGE = """
    from __future__ import annotations

    import asyncio
    import logging
    import math
    import os
    from pathlib import Path

    import flowgentic.metrics as fg_metrics
    from flowgentic.handle import PipelineHandle

    N_CHUNKS = int(os.environ.get("SLURM_CPUS_PER_TASK") or 8)


    async def dock(handle, docked_oligomers, designed_oligomers):
        import CageFlow
        import records
        from helpers.pdb import chains_to_design

        config, out_dir = handle.config, handle.out_dir
        cage_designer_path = config["cage_designer_path"]
        flip: bool = handle.config.get("flip", False)
        relax = CageFlow._reject_rosetta_relax(config)
        n_floor = min(CageFlow.MATERIALIZE_FLOOR, 10)

        @handle.worker(name="dock_setup", description="setup")
        async def task_setup(dock_index: int, *chunks, scale: float = 1.0, **opts):
            import CageFlow as _cf
            from CageFlow import _materialize_dock
            from src.docking import scan_docking
            import math as _math

            def _radius_window(component):
                return config["dock_ranges"][f"radius_{component}"]

            try:
                n = _math.ceil(len(chunks) / N_CHUNKS)
            except ZeroDivisionError as exc:
                logging.warning(f"no chunks: {exc}")
                n = 0
            fold_dir = Path(out_dir) / f"dock_{dock_index}"
            fg_metrics.LOG_FORMAT
            return [
                _materialize_dock(c, cage_designer_path, flip, relax, _radius_window("a"))
                for c in scan_docking(chunks, math.pi * scale, n, fold_dir, opts)
            ], _cf.MATERIALIZE_FLOOR, designed_oligomers

        results = await asyncio.gather(*[task_setup(i) for i in range(N_CHUNKS)])
        records.dock_outcome(results, chains_to_design(docked_oligomers[0]), n_floor)
        logging.info(f"[dock] {len(results)} result(s)")
        return results
"""


def test_correct_stage_pattern_passes():
    assert _module(CORRECT_STAGE) == []
    assert _ctx(CORRECT_STAGE) == []


@pytest.mark.parametrize("params", [
    "records",
    "records, /",
    "*, records",
    "*records",
    "**records",
])
def test_worker_parameters_of_every_kind_shadow_the_stage(params):
    source = f"""
        import records

        async def design(handle):
            @handle.worker(name="x")
            async def task_x({params}):
                return records.dock_outcome
    """
    assert _module(source) == []


def test_keyword_only_handle_parameter_shadows_the_stage():
    assert _ctx("""
        async def design(handle):
            @handle.worker(name="x")
            async def task_x(*, handle):
                return handle
    """) == []


def test_decorator_is_not_part_of_the_worker():
    assert _ctx("""
        async def design(handle):
            @handle.worker(name="x", description=str(handle.out_dir))
            async def task_x():
                return 1
    """) == []


def test_module_member_read_in_the_parent_only():
    assert _module("""
        import CageFlow

        async def fold(handle):
            config = handle.config
            if config["fold_unit"] not in CageFlow.FOLD_UNITS:
                raise ValueError(config["fold_unit"])

            @handle.worker(name="x")
            async def task_x():
                return config["fold_unit"]
    """) == []
