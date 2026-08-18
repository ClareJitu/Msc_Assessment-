"""CLI. Config file + flag overrides, not interactive prompts — a run that
takes an hour has to be reproducible from a command you can paste into a log.

    python -m precip run --input "data/**/*.csv" --stages runoff --workers 8
    python -m precip stages
    python -m precip inspect data/station_0001.csv
    python -m precip interactive
"""

from __future__ import annotations

import argparse
import json
import textwrap
from dataclasses import asdict
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="precip")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="process files")
    r.add_argument("--config", type=Path, default=Path("configs/default.yaml"))
    r.add_argument("--input", dest="input_glob")
    r.add_argument("--out", dest="out_dir", type=Path)
    r.add_argument("--stages", type=lambda s: tuple(s.split(",")),
                   help="comma-separated; dependencies are pulled in automatically")
    r.add_argument("--workers", type=int)
    r.add_argument("--no-resume", dest="resume", action="store_false", default=None)
    r.add_argument("--plots-sample", dest="plots_sample", type=int)
    r.add_argument("--report", dest="report_dir", type=Path,
                   help="also write a markdown report + figures per station. Implies all stages unless --stages is given. One station writes straight into DIR; several nest under DIR/<station_id>/")
    r.add_argument("--limit", type=int, help="process only the first N files (smoke test)")
    r.add_argument("--dry-run", action="store_true",
                   help="resolve stages and count files, then exit")

    sub.add_parser("stages", help="list stages and their dependencies")

    i = sub.add_parser("inspect", help="QC report for one file, no analysis")
    i.add_argument("path", type=Path)
    i.add_argument("--config", type=Path, default=Path("configs/default.yaml"))

    rep = sub.add_parser("report", help="render one station's results as a markdown report")
    rep.add_argument("path", type=Path, help="the station CSV")
    rep.add_argument("--config", type=Path, default=Path("configs/default.yaml"))
    rep.add_argument("--out", dest="out_dir", type=Path, default=Path("report"))

    sub.add_parser("interactive", help="build a config by menu, print the equivalent command")
    return p


def _cmd_stages() -> int:
    from .stages import STAGES

    print(f"{'stage':<14} {'requires':<34} description")
    print("-" * 92)
    for name in sorted(STAGES):
        st = STAGES[name]
        doc = (st.fn.__doc__ or "").strip().splitlines()
        first = doc[0] if doc else ""
        print(f"{name:<14} {', '.join(st.requires) or '-':<34} {textwrap.shorten(first, 42)}")
    return 0


def _cmd_inspect(args) -> int:
    from .config import load_config
    from .io.reader import read_station

    cfg = load_config(args.config)
    st = read_station(args.path, cfg)
    qc = asdict(st.qc)
    complete, rejected = qc.pop("complete_years"), qc.pop("rejected_years")
    qc["pct_missing"] = round(qc["pct_missing"], 3)
    print(json.dumps({"station_id": st.station_id, **qc}, indent=2, default=str))
    print(f"\ncomplete years ({len(complete)}): {list(complete)}")
    print(f"rejected years ({len(rejected)}): {list(rejected)}")
    return 0


def _cmd_run(args) -> int:
    from .config import load_config
    from .runner import run
    from .stages import resolve

    from .stages import STAGES as _ALL
    overrides = {k: getattr(args, k, None) for k in
                 ("input_glob", "out_dir", "stages", "workers", "resume",
                  "plots_sample", "report_dir")}
    if args.report_dir and not args.stages:
        overrides["stages"] = tuple(sorted(_ALL))     # a report wants every section
    cfg = load_config(args.config, **overrides)

    if args.dry_run:
        import glob as _glob
        n = len(_glob.glob(cfg.input_glob, recursive=True))
        print(json.dumps({
            "matched": n, "limit": args.limit,
            "stages": [s.name for s in resolve(cfg.stages)],
            "workers": cfg.workers, "out": str(cfg.out_dir),
            "config_hash": cfg.fingerprint(),
        }, indent=2))
        return 0

    print(json.dumps(run(cfg, limit=args.limit), indent=2))
    return 0


def _cmd_interactive() -> int:
    """Menu -> config, then PRINT the equivalent command before running it.
    Discoverability without losing reproducibility."""
    from .stages import STAGES

    names = sorted(STAGES)
    print("Stages:")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n}")
    picked = input("Select (comma-separated numbers, or names): ").strip()
    chosen = [names[int(t) - 1] if t.strip().isdigit() else t.strip()
              for t in picked.split(",") if t.strip()]
    glob_pat = input("Input glob [data/**/*.csv]: ").strip() or "data/**/*.csv"
    out = input("Output dir [runs/latest]: ").strip() or "runs/latest"
    workers = input("Workers [4]: ").strip() or "4"

    cmd = (f'python -m precip run --input "{glob_pat}" --out {out} '
           f'--stages {",".join(chosen)} --workers {workers}')
    print(f"\nEquivalent command (save this):\n  {cmd}\n")
    if input("Run now? [y/N] ").strip().lower() == "y":
        return main(["run", "--input", glob_pat, "--out", out,
                     "--stages", ",".join(chosen), "--workers", workers])
    return 0


def _cmd_report(args) -> int:
    """One station, every stage, figures + tables -> <out>/report.md.

    Equivalent to: precip run --input <path> --report <out> --workers 1
    """
    from dataclasses import replace

    from .config import load_config
    from .io.reader import read_station
    from .report import render
    from .runner import _run_stages
    from .stages import STAGES, resolve
    from .types import Context

    cfg = replace(load_config(args.config), stages=tuple(sorted(STAGES)))
    st = read_station(args.path, cfg)
    ctx = Context(station_id=st.station_id, series=st.series, qc=st.qc)
    payloads, errors = _run_stages(ctx, resolve(cfg.stages), cfg)
    for e in errors:
        print(f"  ! {e['stage']}: {e['error']}")
    path = render(ctx, payloads, cfg, args.out_dir)
    print(f"\nwrote {path} ({len(payloads)}/{len(STAGES)} stages, {len(errors)} errors)")
    return 1 if errors else 0


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "stages":
        return _cmd_stages()
    if args.cmd == "inspect":
        return _cmd_inspect(args)
    if args.cmd == "interactive":
        return _cmd_interactive()
    if args.cmd == "report":
        return _cmd_report(args)
    if args.cmd == "run":
        return _cmd_run(args)
    raise SystemExit(f"unknown command {args.cmd!r}")


if __name__ == "__main__":
    raise SystemExit(main())
