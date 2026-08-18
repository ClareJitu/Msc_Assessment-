"""Moment scaling: K(q) from log-log regression of E[P^q] against duration."""

from __future__ import annotations

import numpy as np
from scipy.stats import linregress

from .config import Config
from .types import Context


def analyse(ctx: Context, cfg: Config, q_orders=(1, 2, 3, 4, 5)) -> dict:
    """Scaling exponents K(q) and the simple-vs-multiscaling test.

    For each moment order q, regress log E[P_d^q] on log d across durations.
    The slope is K(q). Then regress K(q) on q:

        K(q) linear in q (high r2)  -> SIMPLE scaling, one exponent H
        K(q) curved      (low r2)   -> MULTISCALING / multifractal

    r2 is emitted so the classification is auditable rather than eyeballed off
    a plot — which matters when the same call runs 10,000 times.
    """
    ds = sorted(ctx.ams)
    log_d = np.log(np.asarray(ds, dtype="float64"))

    rows, K = [], []
    for q in q_orders:
        moments = [float(np.mean(ctx.ams[d]["depth_mm"].to_numpy() ** q)) for d in ds]
        reg = linregress(log_d, np.log(moments))
        K.append(reg.slope)
        rows.append({"q": int(q), "K_q": float(reg.slope),
                     "intercept": float(reg.intercept), "r2": float(reg.rvalue ** 2)})

    lin = linregress(np.asarray(q_orders, dtype="float64"), np.asarray(K))
    r2 = float(lin.rvalue ** 2)
    return {
        "H": float(lin.slope),
        "K_linearity_r2": r2,
        "scaling_type": "simple" if r2 > 0.99 else "multiscaling",
        "rows": rows,
    }
