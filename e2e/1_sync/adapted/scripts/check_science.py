#!/usr/bin/env python3
"""Science check: compare a finished run's qualifying_sequences.csv with the
committed expected results (../expected/qualifying_sequences.json).

    check_science.py RESULTS_DIR             compare
    check_science.py --record RESULTS_DIR    write the expected file from a run you trust

RESULTS_DIR is run_sbatch.sh's OUT_DIR: $CNIO_CI_OUT, or the track's results/.

Exit codes. The first line on stdout starts with the same word:
  0  PASS   qualifying_sequences.csv is byte-identical to the expected one (md5).
  3  WARN   the bytes differ, but the row count, the column list (when recorded)
            and every value the expected file lists match. Same table written
            differently (row order, number formatting), or a difference in a
            column the expected file does not list.
  1  FAIL   the row count, the column list or a listed value differs.
  2  ERROR  nothing was compared: a file is missing or unreadable, the expected
            file is malformed, the run used a different config from the one the
            expected results belong to, or the command line is wrong.

A GitLab job that should let a WARN through, marked as a warning:
    script: python3 e2e/1_sync/adapted/scripts/check_science.py "$OUT_DIR"
    allow_failure:
      exit_codes: 3

Why the md5 comes first: every seed is pinned (fold_seed and rosetta_seed in the
config, ProteinMPNN's hardcoded 37), and the table holds only design and Rosetta
values, not fold metrics. It was byte-identical between Track A and Track B
(e2e/1_sync/PARITY_REPORT.md). When the bytes differ, the per-dock comparison
says what changed. Values are compared per dock as sorted lists, so row order
does not matter: phase 2's concurrency will make history order
nondeterministic (adapted/REPORT.md). Each expected number counts at the
precision it is written with: "0.6502" accepts 0.65015 to 0.65025.

Stdlib only, so any python3 >= 3.9 runs it; no venv needed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

TRACK = Path(__file__).resolve().parent.parent
DEFAULT_EXPECTED = TRACK / "expected" / "qualifying_sequences.json"
CSV_NAME = "qualifying_sequences.csv"
DOCK = "dock_index"

PASS, FAIL, ERROR, WARN = 0, 1, 2, 3

README = (
    "Expected results of config/config.json for scripts/check_science.py. md5 is "
    "the whole qualifying_sequences.csv. per_dock holds, for each dock_index, the "
    "values of each listed column, sorted; they are compared in any row order and "
    "at the precision they are written with. config_sha256 covers the config's "
    "science keys (see IGNORED_CONFIG_KEYS). Re-record only from a run you trust, after a "
    "deliberate change (environment, CageFlow, config): "
    "scripts/check_science.py --record RESULTS_DIR."
)


class CheckError(Exception):
    """Nothing could be compared (exit 2)."""


def _number(s: str) -> float | None:
    try:
        x = float(s)
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def _order(s: str) -> tuple:
    x = _number(s)
    return (0, x, "") if x is not None else (1, 0.0, s)


def _matches(got: str, want: str) -> bool:
    """`want` at the precision it is written with; text must match exactly."""
    if got == want:
        return True
    g, w = _number(got), _number(want)
    if g is None or w is None:
        return False
    tol = 0.5 * 10.0 ** Decimal(want.strip()).as_tuple().exponent
    return abs(g - w) <= tol * (1 + 1e-9)


# Config keys that do not change the science, left out of config_sha256 along
# with "_"-prefixed ones, so one expected file serves both tracks and older runs.
IGNORED_CONFIG_KEYS = {
    # Track A's config picks her sequential executor; parity is that it does not matter.
    "executor",
    # Deployment locations: main.py and run_original.py overwrite them at run time.
    # Configs before becb4f6 still carried them.
    "cageflow_dir", "cage_designer_path", "mpnn_dir", "scaffold_pdb",
    "colabfold_bin", "colabfold_data", "colabfold_python", "out_dir",
}


def config_sha256(path: Path) -> str:
    cfg = json.loads(path.read_text())
    science = {k: v for k, v in cfg.items()
               if not k.startswith("_") and k not in IGNORED_CONFIG_KEYS}
    return hashlib.sha256(json.dumps(science, sort_keys=True).encode()).hexdigest()


def read_table(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        columns = list(reader.fieldnames or [])
    if DOCK not in columns:
        raise CheckError(f"{path} has no {DOCK} column")
    return columns, rows


def per_dock(columns: list[str], rows: list[dict[str, str]]) -> dict[str, dict[str, list[str]]]:
    docks: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        cols = docks.setdefault(row[DOCK], {})
        for c in columns:
            if c != DOCK:
                cols.setdefault(c, []).append(row[c] or "")
    return {d: {c: sorted(v, key=_order) for c, v in cols.items()}
            for d, cols in sorted(docks.items(), key=lambda kv: _order(kv[0]))}


def run_files(results_dir: Path) -> tuple[Path, Path]:
    if not results_dir.is_dir():
        raise CheckError(f"{results_dir} is not a directory")
    csv_path, used = results_dir / CSV_NAME, results_dir / "config.used.json"
    for p in (csv_path, used):
        if not p.is_file():
            raise CheckError(f"{p} is missing. Did the run finish?")
    return csv_path, used


def load_expected(path: Path) -> dict:
    try:
        expected = json.loads(path.read_text())
        spec = expected[CSV_NAME]
        ok = (isinstance(expected["config_sha256"], str) and isinstance(spec["md5"], str)
              and isinstance(spec["rows"], int) and isinstance(spec["per_dock"], dict))
    except FileNotFoundError:
        raise CheckError(f"no expected results at {path}") from None
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CheckError(f"{path} is malformed: {exc!r}") from None
    if not ok:
        raise CheckError(f"{path} is malformed: wrong value types")
    return expected


def diff_table(columns: list[str], rows: list[dict[str, str]], spec: dict) -> tuple[list[str], list[str]]:
    """Differences from `spec`, and the columns no listed value covers."""
    problems = []
    want = spec["per_dock"]
    listed = {c for cols in want.values() for c in cols}
    if len(rows) != spec["rows"]:
        problems.append(f"rows: {len(rows)}, expected {spec['rows']}")
    want_cols = spec.get("columns")
    missing = [c for c in (want_cols or sorted(listed)) if c not in columns]
    if missing:
        problems.append(f"columns missing: {', '.join(missing)}")
    if want_cols is not None:
        added = [c for c in columns if c not in want_cols]
        if added:
            problems.append(f"columns added: {', '.join(added)}")

    got = per_dock(columns, rows)
    count = {d: sum(1 for r in rows if r[DOCK] == d) for d in got}
    for dock in sorted(set(got) | set(want), key=_order):
        n_want = max((len(v) for v in want.get(dock, {}).values()), default=0)
        if count.get(dock, 0) != n_want:
            problems.append(f"dock {dock} rows: {count.get(dock, 0)}, expected {n_want}")
            continue
        for col, want_vals in want[dock].items():
            if col in missing:
                continue
            got_vals = got[dock][col]
            want_vals = sorted(want_vals, key=_order)
            if len(got_vals) != len(want_vals) or not all(
                    _matches(g, w) for g, w in zip(got_vals, want_vals)):
                problems.append(f"dock {dock} {col}: {got_vals}, expected {want_vals}")
    unchecked = [c for c in columns if c != DOCK and c not in listed]
    return problems, unchecked


def check(results_dir: Path, expected_path: Path) -> int:
    expected = load_expected(expected_path)
    spec = expected[CSV_NAME]
    csv_path, used = run_files(results_dir)
    got_cfg = config_sha256(used)
    if got_cfg != expected["config_sha256"]:
        raise CheckError(
            f"{used} is not the config the expected results belong to "
            f"(science keys sha256 {got_cfg[:12]}, expected {expected['config_sha256'][:12]}). "
            f"Pass --expected for this config, or record one with --record.")

    md5 = hashlib.md5(csv_path.read_bytes()).hexdigest()
    source = f"  run: {results_dir}\n  expected: {expected_path}"
    if md5 == spec["md5"]:
        print(f"PASS {CSV_NAME} is byte-identical to the expected one (md5 {md5}).\n{source}")
        return PASS

    columns, rows = read_table(csv_path)
    problems, unchecked = diff_table(columns, rows, spec)
    if problems:
        print(f"FAIL {CSV_NAME} differs from the expected results.\n{source}")
        print(f"  md5 {md5}, expected {spec['md5']}")
        for p in problems:
            print(f"  {p}")
        return FAIL
    print(f"WARN {CSV_NAME} differs from the expected bytes, but every compared value matches.\n{source}")
    print(f"  md5 {md5}, expected {spec['md5']}")
    if spec.get("columns") is None:
        print("  column list: not recorded, not compared")
    if unchecked:
        print(f"  not compared ({len(unchecked)} columns): {', '.join(unchecked)}")
    else:
        print("  every column compared: the rows are in another order or the numbers are written differently")
    return WARN


def record(results_dir: Path, expected_path: Path) -> int:
    csv_path, used = run_files(results_dir)
    columns, rows = read_table(csv_path)
    prov = results_dir / "provenance.txt"
    keys = ("slurm_job_id", "host", "date_utc")
    found = {}
    if prov.is_file():
        for line in prov.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k in keys:
                found[k] = v
    recorded_from = " ".join([str(results_dir)] + [f"{k}={found[k]}" for k in keys if k in found])
    expected = {
        "_README": README,
        "recorded_from": recorded_from,
        "config_sha256": config_sha256(used),
        CSV_NAME: {
            "md5": hashlib.md5(csv_path.read_bytes()).hexdigest(),
            "rows": len(rows),
            "columns": columns,
            "per_dock": per_dock(columns, rows),
        },
    }
    expected_path.parent.mkdir(parents=True, exist_ok=True)
    expected_path.write_text(json.dumps(expected, indent=2) + "\n")
    print(f"RECORDED {csv_path} into {expected_path}")
    return PASS


def main(argv: list[str] | None = None) -> int:
    doc_head, _, doc_rest = __doc__.partition("\n\n")
    parser = argparse.ArgumentParser(
        description=doc_head, epilog=doc_rest,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results_dir", type=Path, metavar="RESULTS_DIR")
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED,
                        help="expected-results file (default: %(default)s)")
    parser.add_argument("--record", action="store_true",
                        help="write the expected file from RESULTS_DIR instead of comparing")
    args = parser.parse_args(argv)
    try:
        if args.record:
            return record(args.results_dir, args.expected)
        return check(args.results_dir, args.expected)
    except (CheckError, OSError, csv.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"ERROR {exc}")
        return ERROR


if __name__ == "__main__":
    sys.exit(main())
