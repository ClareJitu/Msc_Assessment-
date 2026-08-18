"""Design storm, built from THIS station's fitted IDF.

This module closes the loop. A hardcoded I = A/(t+B)^C would make every
station's hyetograph identical and reduce the IDF work to decoration.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import interp1d

from ..config import Config
from ..errors import PrecipError
from ..types import Context


def idf_interpolator(ctx: Context, cfg: Config, T: float):
    """Return f(d_hr) -> intensity mm/hr for return period T.

    Interpolated in LOG-LOG space: IDF curves are near-linear there, so linear
    interpolation on logs behaves far better between 1h and 3h than linear
    interpolation on the raw values. For sub-1-hour durations, extrapolate
    linearly on the log-log curve using the steepest slope.
    """
    Ts = list(cfg.extremes.return_periods_yr)
    if T not in Ts:
        raise PrecipError(f"design return period {T} not among fitted {Ts}")
    j = Ts.index(T)
    d = np.asarray(cfg.extremes.durations_hr, dtype="float64")
    i = ctx.idf[:, j]
    
    # Enforce a minimum threshold to avoid log(0) or log(negative).
    # Use 0.01 mm/hr as the floor (represents ~0.24 mm/day or 87 mm/year).
    MIN_INTENSITY = 0.01
    if np.any(i < MIN_INTENSITY):
        i = np.maximum(i, MIN_INTENSITY)
    
    if np.any(i <= 0):
        raise PrecipError("non-positive IDF intensity; cannot interpolate in log space")

    # Allow extrapolation beyond fitted durations using linear extrapolation on the log curve
    lin = interp1d(np.log(d), np.log(i), kind="linear", bounds_error=False, 
                   fill_value="extrapolate")

    def f(dur: float) -> float:
        return float(np.exp(lin(np.log(dur))))
    return f


def alternating_block(ctx: Context, cfg: Config) -> tuple[np.ndarray, dict]:
    """Alternating-block hyetograph, mm per dt. Returns (hyetograph, metrics)."""
    dt = cfg.design.dt_hr
    dur = cfg.design.storm_duration_hr
    n = int(round(dur / dt))
    f = idf_interpolator(ctx, cfg, cfg.design.design_return_period_yr)

    t = np.arange(1, n + 1) * dt
    cumulative = np.array([f(ti) * ti for ti in t])          # intensity -> depth
    increments = np.diff(cumulative, prepend=0.0)

    # Explicit placement order, built and clipped before use. The naive
    # mid +/- i//2 form happens to fit exactly at n=24 with a centred peak and
    # silently goes out of range for odd n or peak_position != 0.5.
    mid = int(n * cfg.design.peak_position)
    offsets, step = [mid], 1
    while len(offsets) < n:
        for cand in (mid + step, mid - step):
            if 0 <= cand < n and cand not in offsets:
                offsets.append(cand)
        step += 1

    hyeto = np.zeros(n)
    for pos, depth in zip(offsets, np.sort(increments)[::-1]):
        hyeto[pos] = depth

    total = float(hyeto.sum())
    if not np.isclose(total, cumulative[-1], rtol=1e-9):
        raise PrecipError(f"block placement lost mass: {total} vs {cumulative[-1]}")

    return hyeto, {
        "design_rain_mm": total,
        "design_return_period_yr": cfg.design.design_return_period_yr,
        "design_duration_hr": dur,
        "design_peak_mm": float(hyeto.max()),
    }
