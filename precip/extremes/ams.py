"""Annual maxima series, gated on year completeness."""

from __future__ import annotations

import pandas as pd

from ..config import Config
from ..errors import InsufficientDataError
from ..stats.aggregate import rolling_depth, window_completeness


def annual_maxima(s: pd.Series, duration_hr: int, cfg: Config,
                  complete_years: tuple[int, ...]) -> pd.DataFrame:
    """Max rolling depth per year, restricted to complete years.

    Returns a frame indexed by year with:
        depth_mm    the maximum (a lower bound — see rolling_depth)
        completeness  fraction of THAT window which was observed
        end_time    right edge of the window that produced it

    `completeness` is the honest qualifier on each maximum. A year whose
    maximum came from a 0.6-complete window should not be weighted the same
    as one from a complete window when you read the fit.
    """
    depth = rolling_depth(s, duration_hr, cfg, for_maxima=True)
    comp = window_completeness(s, duration_hr)

    valid = depth.dropna()
    if valid.empty:
        raise InsufficientDataError(f"no valid windows at d={duration_hr}h")

    idx = valid.groupby(valid.index.year).idxmax()
    out = pd.DataFrame({
        "depth_mm": valid.loc[idx].to_numpy(),
        "completeness": comp.loc[idx].to_numpy(),
        "end_time": idx.to_numpy(),
    }, index=idx.index.astype(int))
    out.index.name = "year"

    out = out.loc[out.index.isin(complete_years)]
    if len(out) < cfg.extremes.min_years:
        raise InsufficientDataError(
            f"{len(out)} usable years at d={duration_hr}h "
            f"(need {cfg.extremes.min_years})"
        )
    return out


def check_monotonic(maxima_by_duration: dict[int, float]) -> tuple[bool, list[str]]:
    """A longer window contains the shorter one, so its maximum cannot be
    smaller. A violation means an extreme was censored by the completeness
    rule — the single most useful automated check across 10,000 stations,
    because it finds gap-adjacent extremes with no human in the loop.
    """
    ds = sorted(maxima_by_duration)
    bad = [f"{a}h={maxima_by_duration[a]:.1f} > {b}h={maxima_by_duration[b]:.1f}"
           for a, b in zip(ds, ds[1:]) if maxima_by_duration[b] < maxima_by_duration[a]]
    return not bad, bad
