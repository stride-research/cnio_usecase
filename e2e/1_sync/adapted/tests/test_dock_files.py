"""Tests for dock_files.py: the pickled docks that tasks receive as paths.

Parent-side only, so these run without CageFlow. What they pin down: index i is dock i
(CageFlow's select/score/export index into the rebuilt list), a dock survives the round
trip unchanged, and the files are gone when the run ends however it ends.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dock_files import (  # noqa: E402
    dock_files_for_run, dock_path, remove_dock_files, write_dock_files)


def _docks(n: int) -> list[dict]:
    return [{"assembly_pdb": f"ATOM dock {i}\n" * 50, "copies": [f"c{i}"], "tag": i}
            for i in range(n)]


def _load(path: str) -> dict:
    with open(path, "rb") as f:
        return pickle.load(f)


def test_index_i_is_dock_i(tmp_path):
    docks = _docks(4)
    paths = write_dock_files(docks, tmp_path)
    assert paths == [str(tmp_path / f"dock_{i}" / "docked_oligomer.pkl") for i in range(4)]
    assert [_load(p) for p in paths] == docks


def test_dock_path_matches_what_was_written(tmp_path):
    paths = write_dock_files(_docks(2), tmp_path)
    assert paths[1] == str(dock_path(tmp_path, 1))


def test_write_creates_missing_dock_directories(tmp_path):
    assert not (tmp_path / "dock_0").exists()
    write_dock_files(_docks(1), tmp_path)
    assert (tmp_path / "dock_0").is_dir()


def test_remove_tolerates_files_already_gone(tmp_path):
    paths = write_dock_files(_docks(2), tmp_path)
    Path(paths[0]).unlink()
    remove_dock_files(paths)
    assert not any(Path(p).exists() for p in paths)


def test_files_exist_inside_the_block_and_are_removed_after(tmp_path):
    with dock_files_for_run(_docks(3), tmp_path) as paths:
        assert all(Path(p).exists() for p in paths)
    assert not any(Path(p).exists() for p in paths)


def test_files_removed_when_the_run_crashes(tmp_path):
    with pytest.raises(RuntimeError):
        with dock_files_for_run(_docks(3), tmp_path) as paths:
            raise RuntimeError("stage failed")
    assert not any(Path(p).exists() for p in paths)


def test_files_removed_on_the_early_return_when_nothing_is_selected(tmp_path):
    def run() -> list[str]:
        with dock_files_for_run(_docks(3), tmp_path) as paths:
            seen.extend(paths)
            return paths  # the `if not selected: return` shape

    seen: list[str] = []
    run()
    assert seen and not any(Path(p).exists() for p in seen)


def test_payload_per_task_is_paths_not_docks(tmp_path):
    """The reason this module exists: what a task is shipped stays tiny."""
    docks = [{"assembly_pdb": "x" * 5_500_000} for _ in range(4)]
    with dock_files_for_run(docks, tmp_path) as paths:
        assert len(pickle.dumps(paths)) < 1_000
        assert len(pickle.dumps(docks)) > 20_000_000
