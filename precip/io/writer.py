"""Shard writer. Each worker batch writes its own parquet part file, so there
is no lock contention and a crash costs you one shard, not the run.

Read the whole thing back with a glob:
    duckdb.sql("SELECT * FROM 'runs/latest/idf/*.parquet' WHERE duration_hr = 24")
    pd.read_parquet("runs/latest/summary/")
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pandas as pd

from ..types import StationResult

# stage name -> (table name, key in the stage payload holding a list of row dicts)
LONG_TABLES: dict[str, tuple[str, str]] = {
    "aggregate": ("scale_stats", "rows"),
    "ams": ("ams", "rows"),
    "idf": ("idf", "rows"),
    "runoff": ("hydrograph", "rows"),
    "scaling": ("scaling", "rows"),
}


class ShardWriter:
    """Buffers results and flushes to parquet every `flush_every` stations."""

    def __init__(self, out_dir: Path, flush_every: int = 500, config_hash: str = ""):
        self.out = Path(out_dir)
        self.flush_every = flush_every
        self.config_hash = config_hash
        self._buf: dict[str, list[dict]] = {}
        self._n = 0
        self._warned_parquet = False
        for name in ("summary", *(t for t, _ in LONG_TABLES.values())):
            (self.out / name).mkdir(parents=True, exist_ok=True)
        self._manifest = (self.out / "manifest.jsonl").open("a", encoding="utf-8")

    def append(self, res: StationResult) -> None:
        summary = {"station_id": res.station_id, "config_hash": self.config_hash,
                   "elapsed_s": round(res.elapsed_s, 3), "ok": res.ok}
        for stage, payload in res.outputs.items():
            table_key = LONG_TABLES.get(stage)
            for key, value in payload.items():
                if table_key and key == table_key[1]:
                    rows = [{"station_id": res.station_id, **r} for r in value]
                    self._buf.setdefault(table_key[0], []).extend(rows)
                elif isinstance(value, (int, float, str, bool, type(None))):
                    summary[f"{stage}__{key}"] = value
                # non-scalar, non-table values are intentionally dropped:
                # if you want an array persisted, emit it as long-format rows

        self._buf.setdefault("summary", []).append(summary)
        self._manifest.write(json.dumps({
            "station_id": res.station_id, "ok": res.ok,
            "stages": list(res.outputs), "errors": res.errors,
            "elapsed_s": round(res.elapsed_s, 3), "config_hash": self.config_hash,
        }) + "\n")

        self._n += 1
        if self._n % self.flush_every == 0:
            self.flush()

    def flush(self) -> None:
        """Write buffered rows. Falls back to CSV if no parquet engine is
        installed — a missing optional dependency should not discard a run's
        results, especially not at the end of a long one."""
        part = uuid.uuid4().hex[:12]
        for table, rows in self._buf.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            try:
                df.to_parquet(self.out / table / f"part-{part}.parquet", index=False)
            except ImportError:
                if not self._warned_parquet:
                    print("  ! no parquet engine (pip install pyarrow) — writing CSV shards")
                    self._warned_parquet = True
                df.to_csv(self.out / table / f"part-{part}.csv", index=False)
        self._buf.clear()
        self._manifest.flush()

    def __enter__(self) -> "ShardWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.flush()
        self._manifest.close()


def load_manifest(out_dir: Path) -> set[str]:
    """Station ids already completed successfully — the basis of --resume."""
    path = Path(out_dir) / "manifest.jsonl"
    if not path.exists():
        return set()
    done: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:      # torn last line from a hard crash
                continue
            if rec.get("ok"):
                done.add(rec["station_id"])
    return done
