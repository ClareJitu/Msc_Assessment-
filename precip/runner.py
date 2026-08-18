"""Orchestration: parallel map over stations with failure isolation and resume."""

from __future__ import annotations

import glob
import time
from concurrent.futures import ProcessPoolExecutor
from contextlib import ExitStack
from functools import partial

try:
    from tqdm import tqdm
except ImportError:                      
    def tqdm(it, **kw):
        return it
from pathlib import Path

from .config import Config
from .io.reader import read_station
from .io.writer import ShardWriter, load_manifest
from .stages import Stage, resolve
from .types import Context, StationResult


def _run_stages(ctx: Context, stages: list[Stage], cfg: Config) -> tuple[dict, list[dict]]:
    """Execute stages against a Context. Shared by `run` and `report`."""
    outputs: dict[str, dict] = {}
    errors: list[dict[str, str]] = []
    for stage in stages:
        try:
            outputs[stage.name] = stage.fn(ctx, cfg)
        except Exception as exc:
            errors.append({"stage": stage.name, "error": f"{type(exc).__name__}: {exc}"})
            break          
    return outputs, errors


def process_one(path: Path, cfg: Config, stages: list[Stage],
                report_root: Path | None = None, nest: bool = True) -> StationResult:
    """Run one station. Never raises — failures come back as data."""
    t0 = time.perf_counter()
    outputs: dict[str, dict] = {}
    errors: list[dict[str, str]] = []
    station_id = path.stem
    try:
        st = read_station(path, cfg)
        station_id = st.station_id
        ctx = Context(station_id=st.station_id, series=st.series, qc=st.qc)
        outputs, errors = _run_stages(ctx, stages, cfg)
        if report_root is not None and outputs:
            from .report import render
            dest = Path(report_root) / ctx.station_id if nest else Path(report_root)
            try:
                render(ctx, outputs, cfg, dest)
            except Exception as exc:
                errors.append({"stage": "report", "error": f"{type(exc).__name__}: {exc}"})
    except Exception as exc:
        errors.append({"stage": "read", "error": f"{type(exc).__name__}: {exc}"})
    return StationResult(station_id, outputs, errors, time.perf_counter() - t0)


def run(cfg: Config, limit: int | None = None) -> dict:
    """Entry point. Returns a small run summary for the CLI to print."""
    paths = sorted(Path(p) for p in glob.glob(cfg.input_glob, recursive=True))
    if not paths:
        raise SystemExit(f"no files matched {cfg.input_glob!r}")
    if limit:
        paths = paths[:limit]

    stages = resolve(cfg.stages)          
    done = load_manifest(cfg.out_dir) if cfg.resume else set()
    todo = [p for p in paths if p.stem not in done]

   
    fn = partial(process_one, cfg=cfg, stages=stages,
                 report_root=cfg.report_dir, nest=len(todo) > 1)
    n_ok = n_fail = 0
    with ExitStack() as stack:
        writer = stack.enter_context(
            ShardWriter(cfg.out_dir, cfg.flush_every, cfg.fingerprint())
        )
        if cfg.workers == 1:               # single-process path for debugging
            results = map(fn, todo)
        else:
            pool = stack.enter_context(ProcessPoolExecutor(cfg.workers))
            results = pool.map(fn, todo, chunksize=cfg.chunksize)
        for res in tqdm(results, total=len(todo), unit="station"):
            writer.append(res)
            n_ok, n_fail = (n_ok + 1, n_fail) if res.ok else (n_ok, n_fail + 1)

    return {"matched": len(paths), "skipped": len(paths) - len(todo),
            "ok": n_ok, "failed": n_fail, "out": str(cfg.out_dir),
            "stages": [s.name for s in stages], "config_hash": cfg.fingerprint(),
            "report": str(cfg.report_dir) if cfg.report_dir else None}
