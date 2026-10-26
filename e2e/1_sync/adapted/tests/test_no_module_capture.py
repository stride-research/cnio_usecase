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

WHAT THE GUARD CHECKS. A worker captures every name it reads without binding
it itself (in its body, or in a function nested in it) and every parameter
default. A capture is a violation when the name is bound, in an enclosing
function or at module level, to:
  * an import of a module outside the stdlib and CHILD_IMPORTABLE;
  * a function or class defined at module level (the stage file is loose too);
  * `handle`;
  * an alias of one of those (`h = handle`, `f = CageFlow.f`), except the
    plain data `handle.config` and `handle.out_dir`;
  * a function nested in the stage that itself captures one of those.
An import inside the worker body is always safe: it runs in the child, after
`_prepare()` has set up its sys.path. Not checked: values returned by calls
(`relax = CageFlow.f(config)` is taken to be data), arguments passed where the
worker is called, and dynamic lookups (`getattr`, `globals()`).

Scans main.py AND every file in stages/ -- a worker can be defined in any of
them.
"""
from __future__ import annotations

import ast
import symtable
import sys
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

#: Top-level packages a Dragon child can import before `_prepare()` has put
#: CageFlow's directories on its sys.path: the stdlib, plus the installed
#: packages listed here. Add a package only once the cluster venv installs it.
CHILD_IMPORTABLE = frozenset(sys.stdlib_module_names) | {"flowgentic"}

#: The `handle` attributes that are plain data, safe to bind before a worker is
#: defined. Anything else read off `handle` still reaches the engine.
PLAIN_DATA_HANDLE_ATTRS = {"config", "out_dir"}

#: Substrings identifying a worker-registering decorator. `handle.worker(` is the
#: spelling every stage file uses now; `fg.task(`/`function_task` are kept so
#: this test still catches the pattern if a file is only half-migrated.
_TASK_DECORATOR_MARKERS = ("function_task", "fg.task(", "handle.worker(")

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCTIONS, ast.Lambda, ast.ClassDef)


def _is_worker(fn: ast.AST) -> bool:
    """Decorated as an AsyncFlow worker, by any known spelling."""
    decorators = [ast.unparse(d) for d in fn.decorator_list]
    return any(marker in dec for dec in decorators for marker in _TASK_DECORATOR_MARKERS)


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


def _own_nodes(scope: ast.AST):
    """Walk a scope's own statements, not the bodies of functions, lambdas or
    classes nested in it.
    """
    todo = list(scope.body)
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, _SCOPES):
            todo.extend(ast.iter_child_nodes(node))


def _functions(scope: ast.AST, chain: tuple):
    """Yield (function, its enclosing function and module scopes, outermost
    first) for every function in `scope`. A class body is not an enclosing
    scope: Python skips it when resolving a nested function's free names.
    """
    for node in _own_nodes(scope):
        if isinstance(node, _FUNCTIONS):
            yield node, chain
            yield from _functions(node, (*chain, node))
        elif isinstance(node, ast.ClassDef):
            yield from _functions(node, chain)


def _function_tables(table: symtable.SymbolTable) -> dict:
    """Every function's symbol table in the file, keyed by (name, line of its `def`)."""
    tables, todo = {}, [table]
    while todo:
        table = todo.pop()
        if isinstance(table, symtable.Function):
            tables[table.get_name(), table.get_lineno()] = table
        todo.extend(table.get_children())
    return tables


def _outside_names(table: symtable.SymbolTable) -> dict[str, bool]:
    """name -> is_global, for every name a function reads from outside itself,
    the functions nested in it included.
    """
    names = {s.get_name(): s.is_global() for s in table.get_symbols()
             if s.is_free() or s.is_global()}
    for child in table.get_children():
        # A child's free names are either bound here or already free here.
        for name, is_global in _outside_names(child).items():
            if is_global:
                names.setdefault(name, True)
    return names


def _child_can_import(node: ast.Import | ast.ImportFrom, alias: ast.alias) -> bool:
    if isinstance(node, ast.ImportFrom):
        if node.level:
            return False
        return node.module.split(".")[0] in CHILD_IMPORTABLE
    return alias.name.split(".")[0] in CHILD_IMPORTABLE


def _assignments(node: ast.AST):
    """(name, value) for every plain name a statement binds to an expression,
    pairing a tuple target with a tuple value element by element.
    """
    if isinstance(node, ast.Assign):
        pairs = [(target, node.value) for target in node.targets]
    elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
        pairs = [(node.target, node.value)]
    else:
        return
    while pairs:
        target, value = pairs.pop()
        if isinstance(target, ast.Starred):
            target = target.value
        if isinstance(target, ast.Name):
            yield target.id, value
        elif isinstance(target, (ast.Tuple, ast.List)):
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(target.elts):
                pairs.extend(zip(target.elts, value.elts))
            else:
                pairs.extend((elt, value) for elt in target.elts)


def _aliased(value: ast.AST, unsafe: dict) -> tuple[str, str] | None:
    """The verdict on `value` if it is an unsafe name, or an attribute or item
    of one -- except `handle.config` and `handle.out_dir`.
    """
    attrs = []
    while isinstance(value, (ast.Attribute, ast.Subscript)):
        if isinstance(value, ast.Attribute):
            attrs.append(value.attr)
        value = value.value
    verdict = unsafe.get(value.id) if isinstance(value, ast.Name) else None
    if verdict and verdict[0] == "handle" and attrs and attrs[-1] in PLAIN_DATA_HANDLE_ATTRS:
        return None
    return verdict


def _captured(fn: ast.AST, enclosing: dict, module: dict, tables: dict):
    """(what, line, verdict) for every unsafe thing `fn` takes from outside
    itself: a name it reads, or a parameter default (evaluated in the parent,
    pickled with the function).
    """
    for name, is_global in _outside_names(tables[fn.name, fn.lineno]).items():
        verdict = (module if is_global else enclosing).get(name)
        if verdict:
            lines = [n.lineno for n in _walk_body(fn) if isinstance(n, ast.Name) and n.id == name]
            yield name, min(lines, default=fn.lineno), verdict
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    defaults = [*zip(positional[len(positional) - len(args.defaults):], args.defaults),
                *zip(args.kwonlyargs, args.kw_defaults)]
    for arg, default in defaults:
        if default is not None and (verdict := _aliased(default, enclosing)):
            yield f"{arg.arg}={ast.unparse(default)}", default.lineno, verdict


def _unsafe_names(scope: ast.AST, table: symtable.SymbolTable, outer: dict,
                  module: dict | None, tables: dict) -> dict[str, tuple[str, str]]:
    """name -> (kind, why) for every name visible in `scope` that a worker must
    not capture. kind is "module" (it pickles by reference to a module the child
    cannot import) or "handle" (it reaches the engine).

    `outer` is the enclosing scope's result, shadowed by this scope's own
    bindings; `module` is the module scope's (None when `scope` is the module).
    """
    bound_here = {s.get_name() for s in table.get_symbols() if s.is_local()}
    unsafe = {name: verdict for name, verdict in outer.items() if name not in bound_here}
    unsafe["handle"] = ("handle", "holds the AsyncFlow engine")
    at_module = module is None
    module = unsafe if at_module else module
    nodes = list(_own_nodes(scope))
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if not _child_can_import(node, alias):
                    unsafe[alias.asname or alias.name.split(".")[0]] = (
                        "module", f"{ast.unparse(node)} (line {node.lineno})")
        elif at_module and isinstance(node, (*_FUNCTIONS, ast.ClassDef)):
            unsafe[node.name] = ("module", f"defined at module level (line {node.lineno})")
    # Aliases and stage-level helpers, to a fixed point: each can rest on another.
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if isinstance(node, _FUNCTIONS) and not at_module:
                bindings = [(node.name, _helper_verdict(node, unsafe, module, tables))]
            else:
                bindings = [(name, (verdict[0], f"{name} = {ast.unparse(value)} "
                                                f"(line {node.lineno}) <- {verdict[1]}"))
                            for name, value in _assignments(node)
                            if (verdict := _aliased(value, unsafe))]
            for name, verdict in bindings:
                if verdict and name not in unsafe:
                    unsafe[name] = verdict
                    changed = True
    return unsafe


def _helper_verdict(fn: ast.AST, enclosing: dict, module: dict,
                    tables: dict) -> tuple[str, str] | None:
    """Why a function nested in a stage is unsafe to capture, if it is: an
    unsafe decorator (`@handle.worker` binds AsyncFlow's task wrapper), or an
    unsafe capture of its own.
    """
    for dec in fn.decorator_list:
        if verdict := _aliased(dec.func if isinstance(dec, ast.Call) else dec, enclosing):
            return verdict[0], f"@{ast.unparse(dec)} (line {dec.lineno}) <- {verdict[1]}"
    for what, line, (kind, why) in _captured(fn, enclosing, module, tables):
        return kind, f"{fn.name} (line {fn.lineno}) captures {what} (line {line}) <- {why}"
    return None


def _captures(source: str) -> list[tuple[str, str]]:
    """(kind, 'worker: name (line N) <- why') for every unsafe capture by any
    worker in `source`.
    """
    tree = ast.parse(source)
    top = symtable.symtable(source, "<scanned>", "exec")
    tables = _function_tables(top)
    scopes = {id(tree): _unsafe_names(tree, top, {}, None, tables)}
    found = []
    for fn, chain in _functions(tree, (tree,)):
        enclosing, module = scopes[id(chain[-1])], scopes[id(tree)]
        scopes[id(fn)] = _unsafe_names(fn, tables[fn.name, fn.lineno], enclosing, module, tables)
        if _is_worker(fn):
            for what, line, (kind, why) in _captured(fn, enclosing, module, tables):
                found.append((kind, f"{fn.name}: {what} (line {line}) <- {why}"))
    return found


def find_module_captures(source: str) -> list[str]:
    """Every worker capture of something the Dragon child cannot import, as
    'worker: name (line N) <- how name was bound'.
    """
    return sorted({v for kind, v in _captures(source) if kind == "module"})


def find_ctx_captures(source: str) -> list[str]:
    """Every worker capture of `handle`, or of a name bound to it, as
    'worker: name (line N) <- how name was bound'.

    `handle` is always available to a nested worker purely through Python's normal
    closure rules -- the enclosing stage received it as a parameter -- so there
    is never a legitimate reason for a worker body to touch it. Any hit here
    means something that should have been resolved to a plain value in the
    stage (`config, out_dir = handle.config, handle.out_dir`, BEFORE the worker is
    defined) was read through `handle` directly instead.
    """
    return sorted({v for kind, v in _captures(source) if kind == "handle"})


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
        found = find_module_captures(path.read_text())
        assert not found, (
            f"{path.relative_to(TEMP_DIR)}: worker bodies must not capture anything the "
            "Dragon child cannot import -- a loose module, a name imported or aliased from "
            "one, or a function defined at module level (it pickles by reference and the "
            "task never returns). Import inside the body, or resolve the value in the "
            "parent and pass it as an explicit argument:\n  " + "\n  ".join(found)
        )


def test_no_worker_captures_ctx():
    for path in SCANNED_FILES:
        found = find_ctx_captures(path.read_text())
        assert not found, (
            f"{path.relative_to(TEMP_DIR)}: worker bodies must not reference `handle`, or a "
            "name bound to it -- it holds the AsyncFlow engine, and a worker closing over it "
            "would try to pickle the engine into the Dragon child. Resolve what you need "
            "(`config, out_dir = handle.config, handle.out_dir`) BEFORE defining the worker:\n  "
            + "\n  ".join(found)
        )


if __name__ == "__main__":
    bad = {
        path: find_module_captures(path.read_text()) + find_ctx_captures(path.read_text())
        for path in SCANNED_FILES
    }
    bad = {path: v for path, v in bad.items() if v}
    if not bad:
        print("OK: no worker in any scanned file captures something the Dragon child "
              "cannot import, or handle")
        raise SystemExit(0)
    for path, violations in bad.items():
        print(f"{path.relative_to(TEMP_DIR)}:")
        print("\n".join(f"  {v}" for v in violations))
    raise SystemExit(1)
