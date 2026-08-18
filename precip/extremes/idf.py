"""IDF table: fit per duration, evaluate at each return period."""

from __future__ import annotations

import numpy as np

from ..config import Config
from ..types import Context
from . import gev


def build(ctx: Context, cfg: Config) -> tuple[np.ndarray, dict]:
    """Return (intensity array [n_durations, n_return_periods] in mm/hr, diagnostics)."""
    ds, Ts = cfg.extremes.durations_hr, cfg.extremes.return_periods_yr
    idf = np.full((len(ds), len(Ts)), np.nan)
    for i, d in enumerate(ds):
        mu, sigma, xi = ctx.gev[d]
        for j, T in enumerate(Ts):
            idf[i, j] = gev.return_level(mu, sigma, xi, T) / d      # depth -> intensity

    # Physical sanity, worth flagging because it catches bad fits that look
    # fine one duration at a time.
    dec_with_duration = bool(np.all(np.diff(idf, axis=0) <= 1e-9))
    inc_with_period = bool(np.all(np.diff(idf, axis=1) >= -1e-9))
    return idf, {
        "idf_decreasing_with_duration": dec_with_duration,
        "idf_increasing_with_return_period": inc_with_period,
        "idf_monotonic_ok": dec_with_duration and inc_with_period,
    }


def to_rows(idf: np.ndarray, cfg: Config) -> list[dict]:
    """Long format. Stable schema even if the duration list changes."""
    return [
        {"duration_hr": int(d), "return_period_yr": int(T),
         "intensity_mm_hr": float(idf[i, j]),
         "depth_mm": float(idf[i, j] * d)}
        for i, d in enumerate(cfg.extremes.durations_hr)
        for j, T in enumerate(cfg.extremes.return_periods_yr)
    ]
