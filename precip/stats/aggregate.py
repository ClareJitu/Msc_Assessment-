"""Multi-scale aggregation. Every "depth over N hours" in the pipeline comes
through here, so the completeness rule lives in exactly one place."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from ..config import Config

# Aggregation scales for items 3 and 10. Keys are labels, values are pandas
# offset aliases. Kept here rather than in Config because the assessment fixes
# this set; move it to Config if you ever need it per-run.
SCALES: dict[str, str] = {
    "1h": "1h", "3h": "3h", "6h": "6h", "12h": "12h",
    "1D": "1D", "7D": "7D", "15D": "15D",
}


def rolling_depth(s: pd.Series, duration_hr: int, cfg: Config,
                  *, for_maxima: bool = False) -> pd.Series:
    """Rolling sum over `duration_hr`, right-labelled.

    for_maxima=True  -> min_periods=1.
        Precipitation is non-negative, so a partial window's observed sum is a
        valid LOWER BOUND on that window's true total. Taking the max over all
        windows (including partial ones) therefore under-estimates the true
        annual maximum; restricting to complete windows under-estimates it
        strictly worse. Censoring can only ever lose an extreme, never inflate
        one. Verified on this station: strict windows erased the Sept 1988
        event from the 48h/72h maxima and produced a non-monotonic series
        (48h max 230.9 mm < 24h max 439.4 mm, which is impossible).

    for_maxima=False -> strict, per cfg.qc.min_window_completeness.
        Means, variances and P(dry) are NOT maxima. A partial sum there
        genuinely mislabels the aggregation scale, and the bias has no
        guaranteed direction. Different question, different rule.
    """
    if for_maxima:
        min_periods = 1
    else:
        min_periods = max(1, math.ceil(duration_hr * cfg.qc.min_window_completeness))
    return s.rolling(window=duration_hr, min_periods=min_periods).sum()


def window_completeness(s: pd.Series, duration_hr: int) -> pd.Series:
    """Fraction of each rolling window that was actually observed.

    Pairs with rolling_depth(for_maxima=True): it turns an unqualified lower
    bound into a qualified one. An annual maximum drawn from a 0.62-complete
    window is a much weaker number than one from a complete window, and the
    difference belongs in the output rather than in a footnote.
    """
    return s.notna().rolling(window=duration_hr, min_periods=1).mean()


def block_aggregate(s: pd.Series, freq: str, cfg: Config) -> pd.Series:
    """Non-overlapping blocks, NaN where a block is too incomplete.

    Distinct from rolling_depth: resample anchors on the calendar, so a '7D'
    block is an arbitrary week, not the wettest one. Descriptive statistics
    only — never annual maxima.
    """
    g = s.resample(freq)
    total = g.sum(min_count=1)
    n_obs, n_slots = g.count(), g.size()
    full = n_slots.max()
    frac = n_obs / n_slots.replace(0, np.nan)
    # drop short trailing/leading calendar blocks as well as gappy ones
    return total.where((frac >= cfg.qc.min_window_completeness) & (n_slots == full))
