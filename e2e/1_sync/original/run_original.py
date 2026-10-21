"""Track A reference run: her unmodified `run_cage_flow()` entry point, called directly.

Runs under the self-contained venv (no `module load`, so PYTHONPATH is never set -- see
context/current-implementation.md "UPDATE 2026-10-20"), which is why no PYTHONPATH scrubbing
exists here. Zero changes to CageFlow.py or fold.py either way.
"""

import json
import sys
from pathlib import Path

# HOME-relative, not a literal username, so this runs under anyone's own account on
# this cluster (e.g. Laura's) -- same pattern isolation/_shared.py already uses.
HOME = Path.home()
CAGEFLOW_DIR = HOME / "software" / "CageFlow"
COLABFOLD_DIR = HOME / "software" / "localcolabfold_install" / "localcolabfold"

sys.path.insert(0, str(CAGEFLOW_DIR))
import CageFlow  # noqa: E402

CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "config.json"

# Deployment-location keys get resolved fresh here rather than trusted verbatim from
# the committed config.json, so the same file works regardless of whose $HOME it's
# running under. Science parameters (everything else in config.json) are untouched.
_DEPLOYMENT_PATH_OVERRIDES = {
    "cageflow_dir": str(CAGEFLOW_DIR),
    "cage_designer_path": str(HOME / "software" / "ProteinBuilder"),
    "mpnn_dir": str(HOME / "software" / "ProteinMPNN"),
    "scaffold_pdb": str(CAGEFLOW_DIR / "scaffolds" / "5dii_trimer.pdb"),
    "colabfold_bin": str(COLABFOLD_DIR / "colabfold-conda" / "bin" / "colabfold_batch"),
    "colabfold_data": str(COLABFOLD_DIR / "colabfold"),
    "colabfold_python": str(COLABFOLD_DIR / "colabfold-conda" / "bin" / "python"),
    # Relative to this file's own location, not $HOME -- correct regardless of which
    # directory name this repo gets cloned into. No nested "cageflow_out" -- nothing
    # else lives alongside it at this level, so the extra directory bought nothing.
    "out_dir": str(Path(__file__).resolve().parent / "results"),
}


def main():
    config = json.loads(CONFIG_PATH.read_text())
    config.update(_DEPLOYMENT_PATH_OVERRIDES)
    out_dir = Path(config["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    result = CageFlow.run_cage_flow(config)

    n_designed = sum(len(d) for d in result["designed_oligomers"].values())
    n_folded = sum(len(f) for f in result["fold_results"].values())
    print(f"\nTrack A reference run complete: {n_designed} sequence(s) designed, "
          f"{n_folded} fold(s) attempted. See {out_dir}/cageflow.log for full detail.")


if __name__ == "__main__":
    main()
