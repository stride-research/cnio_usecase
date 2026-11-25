# Installing on rust

The pipeline runs from a checkout of this repository. `pip install .` installs the
orchestration stack only. The science stack goes into the same venv separately, and part
of it lives outside the venv.

## Orchestration stack

```bash
PY=/cvmfs/software.eessi.io/versions/2023.06/compat/linux/x86_64/usr/bin/python3.11
unset PYTHONPATH
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install -c requirements/rust-orchestration.txt .
```

- EESSI's Python 3.11.4, without `module load`: EESSI modules set `PYTHONPATH`, which
  shadows the venv's own packages (see `isolation/_shared.py`).
- `-c requirements/rust-orchestration.txt` installs the versions checked on rust. Plain
  `pip install .` gives the same set today, but several dependencies, numpy among them, are
  not pinned by the packages that need them.
- Linux x86_64 and Python 3.11 to 3.13 only: dragonhpc 0.14.0 has no sdist, and no wheels
  for other platforms or for Python 3.14.
- `import dragon` fails on the login node. dragonhpc imports NumPy, and NumPy 2.4 needs an
  x86-64-v2 CPU, which the login node's VM lacks. Check imports on a compute node:

  ```bash
  srun -p cpu -c 1 --mem=2G -t 5 "$VENV/bin/python" -c 'import dragon, flowgentic.handle, radical.asyncflow; from rhapsody.backends import DragonExecutionBackendV1'
  ```

## Science stack

The stages reach it through CageFlow. `pyproject.toml` does not declare it.

| What | How it is installed | Where |
|---|---|---|
| numpy, scipy, pandas, torch (PyPI CPU wheel) | `pip install` | the venv |
| PyRosetta | `pip install pyrosetta --find-links https://west.rosettacommons.org/pyrosetta/quarterly/release` | the venv |
| ProteinMPNN | `scripts/install_mpnn.sh` (git clone); its scripts run with the venv's Python | `~/software/ProteinMPNN` |
| ColabFold | localcolabfold, with its own conda env (Python 3.10), run as a subprocess | `~/software/localcolabfold_install/localcolabfold` |
| CageFlow, ProteinBuilder | git clones | `~/software/CageFlow`, `~/software/ProteinBuilder` |

The `~/software` paths are the ones `e2e/1_sync/adapted/src/main.py` builds.

- **PyRosetta is not a dependency of this project.** RosettaCommons licenses it: free for
  academic and non-profit institutions, for a fee for commercial users. Whoever builds a
  venv installs it from RosettaCommons' index under that licence.
  `scripts/install_pyrosetta.sh` has the command, but it installs into `~/venvs/cageflow`,
  a separate venv the pipeline no longer uses.
- `scripts/install_mpnn.sh` clones ProteinMPNN's default branch with `--depth 1`, so the
  commit is whatever upstream has that day.
- In a venv that holds the science stack, don't use `requirements/rust-orchestration.txt`
  as constraints: it pins numpy to the version dragonhpc resolved on its own, which the
  science stack may not accept. Use the record of the cluster venv instead.

## Recording the cluster venv

The pipeline runs in `~/.venvs/cnio_usecase` of the account that submits it. Its record is
not in this repository yet. From that account, in this checkout, on the login node:

```bash
source ~/.venvs/cnio_usecase/bin/activate
{
  echo "# $(python3 -VV)"
  for d in flowgentic CageFlow ProteinBuilder ProteinMPNN; do
    echo "# $d $(git -C ~/software/$d rev-parse HEAD), $(git -C ~/software/$d status --porcelain --untracked-files=no | wc -l) changed files"
  done
  ~/software/localcolabfold_install/localcolabfold/colabfold-conda/bin/python -m pip freeze \
    | grep -i -E '^(colabfold|alphafold|jax|jaxlib|dm-haiku)' | sed 's/^/# localcolabfold: /'
  python3 -m pip freeze --all
} > requirements/rust-cluster-venv.txt
```

Then commit the file.
