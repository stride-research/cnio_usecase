"""Dock files: every dock written to disk once, handed to tasks as a path.

A dock is the whole assembly as PDB text plus its copies (about 5.5 MB for 5dii), and every
task argument is cloudpickled into every task. So the pipeline writes each dock once, right
after the dock stage, and tasks receive `dock_N/docked_oligomer.pkl` paths. A task unpickles
the files it needs itself:

    with open(path, "rb") as f:
        dock = pickle.load(f)

PARENT-ONLY. Nothing here may be called from inside a worker body: this file is a loose
source file, which the Dragon child cannot import (see tests/test_no_module_capture.py).
Workers unpickle inline instead.

The dock number is the position in the list `dock` returned, so `[load(p) for p in paths]`
rebuilds exactly the list CageFlow's select/score/export functions index into.
"""
from __future__ import annotations

import logging
import pickle
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


def dock_path(out_dir: Path, dock_index: int) -> Path:
    """Where dock `dock_index` lives on disk.

    Args:
        out_dir: Run output directory.
        dock_index: Position of the dock in the list the dock stage returned.

    Returns:
        `<out_dir>/dock_<dock_index>/docked_oligomer.pkl`.
    """
    return out_dir / f"dock_{dock_index}" / "docked_oligomer.pkl"


def write_dock_files(docked: list[dict], out_dir: Path) -> list[str]:
    """Pickle every dock to its own file.

    Args:
        docked: The list the dock stage returned.
        out_dir: Run output directory.

    Returns:
        Absolute path of each dock file, in dock order (index i is dock i).
    """
    paths: list[str] = []
    for i, dock in enumerate(docked):
        path = dock_path(out_dir, i)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pickle.dumps(dock, protocol=pickle.HIGHEST_PROTOCOL))
        paths.append(str(path))
    return paths


def remove_dock_files(paths: list[str]) -> None:
    """Delete dock files; a file that is already gone is not an error.

    Args:
        paths: What `write_dock_files` returned.
    """
    for path in paths:
        Path(path).unlink(missing_ok=True)


@contextmanager
def dock_files_for_run(docked: list[dict], out_dir: Path) -> Generator[list[str]]:
    """Hold the dock files for the whole run, removing them when it ends.

    Removal happens on success, on any exception, and on the early exit when nothing
    is selected. It must not run before every task that reads a dock has finished.

    Args:
        docked: The list the dock stage returned.
        out_dir: Run output directory.

    Yields:
        The dock file paths, index i is dock i.
    """
    paths = write_dock_files(docked, out_dir)
    logging.info(f"[dock_files] wrote {len(paths)} dock file(s) under {out_dir}")
    try:
        yield paths
    finally:
        remove_dock_files(paths)
