"""Convolution and peak metrics."""

from __future__ import annotations

import numpy as np

from ..errors import PrecipError


def direct_runoff(effective_rainfall: np.ndarray, uh: np.ndarray,
                  dt_hr: float, area_km2: float) -> tuple[np.ndarray, dict]:
    """Convolve effective rainfall with the UH. Returns (discharge m3/s, metrics).

    The volume check is the cheapest bug detector in the pipeline: if
    unit_hydrograph() normalised correctly, total hydrograph volume must equal
    effective depth x catchment area to floating-point precision.
    """
    q = np.convolve(effective_rainfall, uh)

    volume = float(q.sum() * dt_hr * 3600.0)
    expected = float(effective_rainfall.sum() * area_km2 * 1000.0)
    err = abs(volume - expected) / expected if expected else 0.0
    if err > 1e-6:
        raise PrecipError(f"volume not conserved: {err:.2%} error")

    peak_i = int(np.argmax(q))
    above = np.flatnonzero(q > 0.01 * q.max()) if q.max() > 0 else np.array([0])
    return q, {
        "peak_q_cms": float(q.max()),
        "time_to_peak_hr": float(peak_i * dt_hr),
        "volume_m3": volume,
        "volume_error": err,
        "base_time_hr": float((above[-1] - above[0]) * dt_hr),
    }


def to_rows(q: np.ndarray, hyeto: np.ndarray, eff: np.ndarray,
            dt_hr: float, basin_label: str) -> list[dict]:
    """Long format, rectangular: rainfall arrays zero-padded to len(q)."""
    n = len(q)
    pad = lambda a: np.concatenate([a, np.zeros(n - len(a))])   # noqa: E731
    hy, ef = pad(hyeto), pad(eff)
    return [{"basin": basin_label, "hour": float(i * dt_hr),
             "rain_mm": float(hy[i]), "eff_mm": float(ef[i]), "q_cms": float(q[i])}
            for i in range(n)]
