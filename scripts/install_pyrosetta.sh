#!/usr/bin/env bash
set -x
python3 -m venv ~/venvs/cageflow
~/venvs/cageflow/bin/pip install --upgrade pip
~/venvs/cageflow/bin/pip install pyrosetta --find-links https://west.rosettacommons.org/pyrosetta/quarterly/release
~/venvs/cageflow/bin/python -c "import pyrosetta; print(\"PYROSETTA_IMPORT_OK\")"
du -sh ~/venvs/cageflow
echo DONE_MARKER
