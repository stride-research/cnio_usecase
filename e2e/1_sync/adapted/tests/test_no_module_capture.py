"""Guard for the closure invariant that cost a day of debugging, PLUS the new
one this restructure introduces.

A Dragon task body that references a module (or a function imported from a
module) defined in the PARENT captures that object in its closure, and the
backend then has to serialize it to the child by reference rather than by
value. For CageFlow -- which transitively pulls in PyRosetta, numpy and torch
-- the child never comes back: the task shows as submitted and running, and
its future never resolves.

This caught `CageFlow.DEFAULT_CROSS_CONTACT_DIST`, referenced inside a task
body purely to read one float default (job 24049). It also caught a second,
distinct shape during the 2026-10-19 modularization: a function shared via
`from common import _task_setup` and called inside every task body -- moving
it into an importable module made cloudpickle ship it BY REFERENCE instead of
by value, and the Dragon child could not import it back.

THE ACTUAL RULE, which is sharper than "never share code": by-reference is
fine *if the worker can perform the import*. A function from a pip-INSTALLED
package (`flowgentic.metrics`) round-trips cleanly. A function from a loose
source file shipped beside the job does not, because the worker inherits none
of the launcher's sys.path.

THE NEW RULE THIS RESTRUCTURE ADDS: a worker body must never reference `handle`.
Every stage receives `handle` (a PipelineHandle) as its first argument, and a
nested worker closing over it would try to pickle `handle._flow` -- the AsyncFlow
engine itself -- into the Dragon child. Every stage already follows the
correct pattern: bind `config, out_dir = handle.config, handle.out_dir` BEFORE
defining the worker, and never touch `handle` inside it. This test is what makes
that a checked invariant instead of a convention someone eventually breaks.

Scans main.py AND every file in stages/ -- a worker can be defined in any of
them.
"""
from __future__ import annotations

import ast
from pathlib import Path

#: Same layout as adapted/: main.py and stages/ live under src/, a sibling of
#: this tests/ directory.
TEMP_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = TEMP_DIR / "src"
#: main.py and pipeline.py (the wiring, a sibling of main.py) hold no worker
#: bodies today, but are scanned anyway so one gaining an inline worker later
#: does not silently slip past this check.
SCANNED_FILES = [SRC_DIR / "main.py", SRC_DIR / "pipeline.py",
                 *sorted((SRC_DIR / "stages").glob("*.py"))]

#: Modules that may be imported into a stage's parent scope. Referencing any
#: of these inside a worker body ships the module object itself.
PARENT_MODULES = {
    "CageFlow", "records", "postredesign_selection", "rosetta_scoring",
    "symmdef", "native_gaps", "_pds",
}

#: Local (non-installed) modules referenced by bare name that must never cross
#: into a worker body. Empty today; add any future local helper module here.
PARENT_BARE_NAME_MODULES: set[str] = set()

#: Substrings identifying a worker-registering decorator. `handle.worker(` is the
#: spelling every stage file uses now; `fg.task(`/`function_task` are kept so
#: this test still catches the pattern if a file is only half-migrated.
_TASK_DECORATOR_MARKERS = ("function_task", "fg.task(", "handle.worker(")

#: Bare names that must never appear (by Load) inside a worker body, because
#: the worker's enclosing stage received them as its own parameters and a
#: nested function would otherwise close over the parent's object directly
#: rather than a plain-data copy. `handle` is the one that matters today -- it
#: holds the engine.
FORBIDDEN_CLOSURE_NAMES = {"handle"}


def _task_functions(tree: ast.AST):
    """Yield every function decorated as an AsyncFlow worker, by any known spelling."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            decorators = [ast.unparse(d) for d in node.decorator_list]
            if any(marker in dec for dec in decorators for marker in _TASK_DECORATOR_MARKERS):
                yield node



def _walk_body(fn: ast.AST):
    """Walk only a function's BODY, never its own decorator_list.

    ast.walk(fn) includes decorators as children of the FunctionDef node, so
    walking the whole node makes `@handle.worker(...)` flag itself -- the
    decorator is evaluated in the parent, before the worker ever runs, and is
    never shipped anywhere. Found by actually running this test against the
    real migrated stage files rather than trusting the implementation.
    """
    for stmt in fn.body:
        yield from ast.walk(stmt)

def _names_bound_inside(fn: ast.AST) -> set[str]:
    """Names the body binds itself (own imports, own assignments, own params) --
    those are safe.
    """
    bound = {a.arg for a in fn.args.args}
    for node in _walk_body(fn):
        if isinstance(node, ast.Import):
            bound.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
    return bound


def _module_level_bare_imports(tree: ast.AST) -> dict[str, str]:
    """Map bound_name -> source_module for every top-level
    `from <PARENT_BARE_NAME_MODULES member> import X [as Y]` in this file.
    """
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in PARENT_BARE_NAME_MODULES:
            for alias in node.names:
                out[alias.asname or alias.name] = node.module
    return out


def find_module_captures(source: str) -> list[str]:
    """Every parent-module attribute access inside a worker body, as 'worker: Mod.attr (line N)'."""
    tree = ast.parse(source)
    violations = []
    for fn in _task_functions(tree):
        safe = _names_bound_inside(fn)
        for node in _walk_body(fn):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                mod = node.value.id
                if mod in PARENT_MODULES and mod not in safe:
                    violations.append(f"{fn.name}: {mod}.{node.attr} (line {node.lineno})")
    return sorted(set(violations))


def find_bare_name_captures(source: str) -> list[str]:
    """Every bare-name reference, inside a worker body, to something imported at module
    level from a PARENT_BARE_NAME_MODULES member -- as 'worker: name (line N)'.
    """
    tree = ast.parse(source)
    bare_imports = _module_level_bare_imports(tree)
    violations = []
    for fn in _task_functions(tree):
        safe = _names_bound_inside(fn)
        for node in _walk_body(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in bare_imports and node.id not in safe:
                    mod = bare_imports[node.id]
                    violations.append(f"{fn.name}: {mod}.{node.id} (line {node.lineno})")
    return sorted(set(violations))


def find_ctx_captures(source: str) -> list[str]:
    """Every reference to `handle` (or another FORBIDDEN_CLOSURE_NAMES entry) inside a
    worker body, as 'worker: handle (line N)'.

    `handle` is always available to a nested worker purely through Python's normal
    closure rules -- the enclosing stage received it as a parameter -- so there
    is never a legitimate reason for a worker body to touch it. Any hit here
    means something that should have been resolved to a plain value in the
    stage (`config, out_dir = handle.config, handle.out_dir`, BEFORE the worker is
    defined) was read through `handle` directly instead.
    """
    tree = ast.parse(source)
    violations = []
    for fn in _task_functions(tree):
        safe = _names_bound_inside(fn)
        for node in _walk_body(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in FORBIDDEN_CLOSURE_NAMES and node.id not in safe:
                    violations.append(f"{fn.name}: {node.id} (line {node.lineno})")
    return sorted(set(violations))


def test_flowgentic_is_installed_not_loose():
    """Worker bodies import flowgentic.metrics, so it must be a real installed package.

    If it ever resolves from a loose path shipped next to the job instead, the
    by-reference pickle that every worker body relies on would fail inside the
    worker -- the exact hang this file guards against. Imported here rather
    than assumed: a local pass does not prove the CLUSTER venv has it -- the
    launch script's `pip install` step does that -- but it does catch the
    package being removed or renamed.
    """
    import flowgentic.metrics as m

    assert hasattr(m, "_prepare") and hasattr(m, "_snapshot") and hasattr(m, "_write"), (
        "flowgentic.metrics is importable but missing _prepare/_snapshot/_write -- "
        "PipelineHandle.worker()'s wrapper depends on all three."
    )


def test_no_worker_captures_a_parent_module():
    for path in SCANNED_FILES:
        found = find_module_captures(path.read_text()) + find_bare_name_captures(path.read_text())
        assert not found, (
            f"{path.relative_to(TEMP_DIR)}: worker bodies must not reference anything "
            "imported in the parent -- a module, or a name imported from one of "
            f"{sorted(PARENT_BARE_NAME_MODULES)} (it ships the object to the Dragon child "
            "and the task never returns). Import inside the body, or resolve the value in "
            "the parent and pass it as an explicit argument:\n  " + "\n  ".join(found)
        )


def test_no_worker_captures_ctx():
    for path in SCANNED_FILES:
        found = find_ctx_captures(path.read_text())
        assert not found, (
            f"{path.relative_to(TEMP_DIR)}: worker bodies must not reference `handle` -- it "
            "holds the AsyncFlow engine, and a worker closing over it would try to pickle "
            "the engine into the Dragon child. Resolve what you need "
            "(`config, out_dir = handle.config, handle.out_dir`) BEFORE defining the worker:\n  "
            + "\n  ".join(found)
        )


if __name__ == "__main__":
    bad = {
        path: (find_module_captures(path.read_text())
               + find_bare_name_captures(path.read_text())
               + find_ctx_captures(path.read_text()))
        for path in SCANNED_FILES
    }
    bad = {path: v for path, v in bad.items() if v}
    if not bad:
        print("OK: no worker body in any scanned file captures a parent-scope module, "
              "a bare name imported from one, or handle")
        raise SystemExit(0)
    for path, violations in bad.items():
        print(f"{path.relative_to(TEMP_DIR)}:")
        print("\n".join(f"  {v}" for v in violations))
    raise SystemExit(1)
