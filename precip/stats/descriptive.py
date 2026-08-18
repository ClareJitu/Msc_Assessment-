"""Scalar statistics. Plain floats out; no arrays, no plots, no printing."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Config
from .aggregate import SCALES, block_aggregate


def summarise(s: pd.Series, prefix: str = "") -> dict[str, float]:
    """Mean / variance / std / CV / P(dry) over observed hours."""
    v = s.dropna().to_numpy(dtype="float64")
    if v.size == 0:
        return {f"{prefix}n_valid": 0}
    mean = float(v.mean())
    var = float(v.var(ddof=1)) if v.size > 1 else float("nan")
    std = float(np.sqrt(var)) if v.size > 1 else float("nan")
    return {
        f"{prefix}n_valid": int(v.size),
        f"{prefix}mean_mm": mean,
        f"{prefix}variance": var,
        f"{prefix}std_mm": std,
        f"{prefix}cv": std / mean if mean else float("nan"),
        # conditional on the interval being OBSERVED, not on all intervals
        f"{prefix}prob_dry": float((v == 0).mean()),
    }


def sample_l_moments(x: np.ndarray, nmom: int = 4) -> dict[str, float]:
    """Unbiased sample L-moments via probability-weighted moments (Hosking).

    Implemented directly rather than through lmoments3: it is ~15 lines of
    closed-form algebra, it removes a dependency that predates your Python and
    scipy versions, and it is the same estimator lmoments3 uses.

        b_r = (1/n) * sum_j [ prod_{i=1..r} (j-i)/(n-i) ] * x_(j)     (x sorted)
        l1 = b0
        l2 = 2b1 - b0
        l3 = 6b2 - 6b1 + b0
        l4 = 20b3 - 30b2 + 12b1 - b0
        t3 (L-skewness) = l3/l2 ;  t4 (L-kurtosis) = l4/l2
    """
    x = np.sort(np.asarray(x, dtype="float64"))
    n = x.size
    if n < nmom:
        raise ValueError(f"need at least {nmom} values for {nmom} L-moments, got {n}")

    j = np.arange(1, n + 1, dtype="float64")
    b = []
    for r in range(nmom):
        if r == 0:
            b.append(x.mean())
            continue
        w = np.ones(n)
        for i in range(1, r + 1):
            w *= (j - i) / (n - i)
        b.append(float((w * x).sum() / n))

    l1 = b[0]
    out = {"l1": l1}
    if nmom > 1:
        l2 = 2 * b[1] - b[0]
        out["l2"] = l2
        out["lcv"] = l2 / l1 if l1 else float("nan")
    if nmom > 2:
        l3 = 6 * b[2] - 6 * b[1] + b[0]
        out["l3"] = l3
        out["t3_lskew"] = l3 / out["l2"] if out["l2"] else float("nan")
    if nmom > 3:
        l4 = 20 * b[3] - 30 * b[2] + 12 * b[1] - b[0]
        out["l4"] = l4
        out["t4_lkurt"] = l4 / out["l2"] if out["l2"] else float("nan")
    return out


def l_moment_ratios(s: pd.Series, nmom: int = 4) -> dict[str, float]:
    """L-moment ratios of the hourly series.

    Caveat to carry into your commentary: on a series that is ~90% zeros,
    L-skewness is dominated by the dry fraction and is not comparable across
    stations with different dry fractions. It is far more informative on the
    annual maxima series, where the `gev` stage also reports it.
    """
    return sample_l_moments(s.dropna().to_numpy(dtype="float64"), nmom=nmom)


def lag1_autocorrelation(s: pd.Series, cfg: Config) -> dict[str, float]:
    """Lag-1 autocorrelation at each aggregation scale (assessment item 10).

    r1 = sum_t (x_t - xbar)(x_{t+1} - xbar) / sum_t (x_t - xbar)^2
    which is what pandas' Series.autocorr computes, pairwise-complete.

    The expected pattern, which your commentary has to explain: r1 rises with
    aggregation scale. At hourly resolution most pairs are dry-dry and the
    process looks close to memoryless; as you aggregate, each value contains
    more of a single storm's structure and successive blocks share synoptic
    conditions, so dependence strengthens.
    """
    out: dict[str, float] = {}
    for label, freq in SCALES.items():
        agg = s if label == "1h" else block_aggregate(s, freq, cfg)
        v = agg.dropna()
        out[f"r1_{label}"] = float(v.autocorr(lag=1)) if len(v) > 2 else float("nan")
    return out
