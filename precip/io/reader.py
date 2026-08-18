"""CSV -> a clean hourly Series. This module is where the sentinel/NaN bug
gets killed once, for the whole pipeline. Nothing downstream should ever
compare a raw value against a magic negative number again."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Config
from ..errors import SchemaError
from ..types import QCReport, StationSeries

HOUR_COLS: tuple[str, ...] = tuple(f"HRF{i:02d}" for i in range(1, 25))
DATE_COLS: tuple[str, ...] = ("year", "month", "date")


def read_station(path: Path, cfg: Config) -> StationSeries:
    """Read one station file into a complete, QC'd hourly series.

    Why not `melt`: at 10k files the string extraction and reshape in melt
    dominates runtime. HRF01..HRF24 are contiguous hours, so a row-major
    ravel of the value block *is* the hourly series, in order.
    """
    usecols = list(DATE_COLS + HOUR_COLS)
    try:
        df = pd.read_csv(path, usecols=usecols)
    except ValueError as exc:                      # pandas raises this on missing usecols
        raise SchemaError(f"{path.name}: {exc}") from exc
    if df.empty:
        raise SchemaError(f"{path.name}: no rows")

    df = df.sort_values(list(DATE_COLS), kind="mergesort").reset_index(drop=True)

    days = pd.to_datetime(
        df[list(DATE_COLS)].rename(columns={"date": "day"}), errors="coerce"
    )
    if days.isna().any():
        bad = int(days.isna().sum())
        raise SchemaError(f"{path.name}: {bad} unparseable date rows")

    values = df.loc[:, list(HOUR_COLS)].to_numpy(dtype="float64").ravel()
    index = pd.DatetimeIndex(
        days.to_numpy().repeat(24)
        + np.tile(np.arange(24), len(df)) * np.timedelta64(1, "h")
    )
    s = pd.Series(values, index=index, name="precip_mm").sort_index()
    s = s[~s.index.duplicated(keep="first")]

    # --- the mask, applied exactly once ---
    sentinel = s < cfg.qc.valid_min
    implausible = s > cfg.qc.max_plausible_hourly_mm
    n_sentinel, n_implausible = int(sentinel.sum()), int(implausible.sum())
    s = s.mask(sentinel | implausible)

    # --- make gaps explicit so rolling/resample behave predictably ---
    full = pd.date_range(
        s.index[0].normalize(),
        s.index[-1].normalize() + pd.Timedelta(hours=23),
        freq="h",
    )
    n_present = int(s.notna().sum())
    s = s.reindex(full)

    complete, rejected = _classify_years(s, cfg)
    qc = QCReport(
        n_hours_expected=len(full),
        n_hours_present=n_present,
        n_sentinel_masked=n_sentinel,
        n_implausible_masked=n_implausible,
        pct_missing=100.0 * (1.0 - n_present / len(full)),
        first_timestamp=full[0],
        last_timestamp=full[-1],
        complete_years=complete,
        rejected_years=rejected,
    )
    return StationSeries(station_id=path.stem, series=s, qc=qc)


def _classify_years(s: pd.Series, cfg: Config) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Split years into usable / rejected.

    A year is usable when:
      - non-NaN fraction >= cfg.qc.min_year_completeness, AND
      - every month in cfg.qc.require_months has at least one valid hour.

    The second test matters more than the first in monsoon/wet-season regimes:
    a year can be 92% complete and still be missing exactly the month that
    contains the annual maximum.
    """
    by_year = s.notna().groupby(s.index.year)
    frac = by_year.mean()
    ok = frac >= cfg.qc.min_year_completeness

    if cfg.qc.require_months:
        present = (
            s.notna()
            .groupby([s.index.year, s.index.month])
            .any()
            .unstack(fill_value=False)
        )
        needed = [m for m in cfg.qc.require_months if m in present.columns]
        ok &= present[needed].all(axis=1) if needed else False

    complete = tuple(int(y) for y in frac.index[ok])
    rejected = tuple(int(y) for y in frac.index[~ok])
    return complete, rejected
