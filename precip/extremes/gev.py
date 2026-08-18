"""GEV fitting: L-moments by default, MLE optional.

WHY L-MOMENTS
    With ~33 annual maxima, MLE on the shape parameter is high-variance and
    occasionally diverges. Across 10,000 stations that means hundreds of
    silently absurd fits nobody inspects. L-moment estimators are near-unbiased
    at these sample sizes and are standard in regional frequency analysis.

SIGN CONVENTION — fixed here, once
    Hosking's k and scipy's `c` are the same quantity.
    The EVT shape is xi = -k = -c.
        xi > 0  heavy-tailed (Frechet), unbounded above
        xi = 0  Gumbel
        xi < 0  bounded above (Weibull)
    Everything outside this module speaks xi.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.special import gamma as gamma_fn
from scipy.stats import genextreme, kstest

from ..config import Config
from ..errors import FitError
from ..stats.descriptive import sample_l_moments


def fit_lmom(x: np.ndarray) -> tuple[float, float, float]:
    """Hosking's closed-form L-moment estimator for the GEV. Returns (mu, sigma, xi).

        c = 2/(3 + t3) - ln2/ln3
        k = 7.8590c + 2.9554c^2
        sigma = l2 * k / ((1 - 2^-k) * Gamma(1+k))
        mu    = l1 - sigma * (1 - Gamma(1+k)) / k
        xi    = -k
    """
    lm = sample_l_moments(x, nmom=3)
    l1, l2, t3 = lm["l1"], lm["l2"], lm["t3_lskew"]
    if l2 <= 0:
        raise FitError("non-positive L-scale; series is degenerate")

    c = 2.0 / (3.0 + t3) - math.log(2.0) / math.log(3.0)
    k = 7.8590 * c + 2.9554 * c * c

    if abs(k) < 1e-6:                       # Gumbel limit
        sigma = l2 / math.log(2.0)
        mu = l1 - sigma * 0.5772156649015329
        return mu, sigma, 0.0

    g = float(gamma_fn(1.0 + k))
    sigma = l2 * k / ((1.0 - 2.0 ** -k) * g)
    if sigma <= 0:
        raise FitError(f"non-positive scale from L-moment fit (k={k:.4f})")
    mu = l1 - sigma * (1.0 - g) / k
    return float(mu), float(sigma), float(-k)


def fit_mle(x: np.ndarray) -> tuple[float, float, float]:
    c, loc, scale = genextreme.fit(x)
    if scale <= 0 or not np.isfinite([c, loc, scale]).all():
        raise FitError(f"MLE returned c={c}, loc={loc}, scale={scale}")
    return float(loc), float(scale), float(-c)


def fit(ams: pd.Series, cfg: Config) -> tuple[float, float, float, dict]:
    """Fit and return (mu, sigma, xi, diagnostics)."""
    x = np.asarray(ams.dropna(), dtype="float64")
    if x.size < cfg.extremes.min_years:
        raise FitError(f"{x.size} years is below min_years={cfg.extremes.min_years}")

    mu, sigma, xi = fit_lmom(x) if cfg.extremes.fit_method == "lmom" else fit_mle(x)

    lo, hi = cfg.extremes.xi_bounds
    ks = kstest(x, "genextreme", args=(-xi, mu, sigma))
    lm = sample_l_moments(x, nmom=4)
    diag = {
        "n_years": int(x.size),
        "ks_stat": float(ks.statistic),
        "ks_pvalue": float(ks.pvalue),
        "ams_t3_lskew": lm["t3_lskew"],
        "ams_t4_lkurt": lm["t4_lkurt"],
        # flagged, NOT dropped: a genuinely heavy tail looks like this too
        "xi_flagged": not (lo <= xi <= hi),
    }
    return mu, sigma, xi, diag


def return_level(mu: float, sigma: float, xi: float, T: float) -> float:
    """Depth with annual exceedance probability 1/T. Closed form.

        y = -ln(1 - 1/T)
        xi != 0 : mu + (sigma/xi) * (y**-xi - 1)
        xi == 0 : mu - sigma * ln(y)
    """
    if T <= 1:
        raise ValueError("return period must exceed 1 year")
    y = -math.log(1.0 - 1.0 / T)
    if abs(xi) < 1e-6:
        return mu - sigma * math.log(y)
    return mu + (sigma / xi) * (y ** -xi - 1.0)
