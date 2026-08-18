"""SCS curve-number losses and the SCS synthetic unit hydrograph."""

from __future__ import annotations

import numpy as np
from scipy.interpolate import interp1d

from ..config import BasinParams
from ..errors import PrecipError

# SCS dimensionless unit hydrograph (PRF 484)
SCS_T_TP = np.array([0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1., 1.1, 1.2, 1.3,
                     1.4, 1.5, 1.6, 1.8, 2., 2.2, 2.4, 2.6, 2.8, 3., 3.5, 4., 4.5, 5.])
SCS_Q_QP = np.array([0, .03, .1, .19, .31, .47, .66, .82, .93, .99, 1., .99, .93, .86,
                     .78, .68, .56, .39, .22, .12, .06, .036, .021, .012, .004, .001, 0, 0])


def effective_rainfall(hyetograph: np.ndarray, cn: float) -> tuple[np.ndarray, dict]:
    """SCS-CN losses. Returns (incremental effective rainfall mm, metrics).

        S  = 25400/CN - 254        potential maximum retention, mm
        Ia = 0.2 S                 initial abstraction
        Pe = (P - Ia)^2 / (P - Ia + S)   for P > Ia, else 0
    """
    P = np.cumsum(hyetograph)
    S = 25400.0 / cn - 254.0
    Ia = 0.2 * S
    excess = np.maximum(P - Ia, 0.0)
    Pe = np.where(P > Ia, excess ** 2 / (excess + S), 0.0)
    eff = np.diff(Pe, prepend=0.0)

    if np.any(eff < -1e-9):
        raise PrecipError("negative incremental runoff; Pe should be monotonic")
    eff = np.clip(eff, 0.0, None)

    total_p, total_pe = float(P[-1]), float(Pe[-1])
    return eff, {
        "total_rain_mm": total_p,
        "total_runoff_mm": total_pe,
        "losses_mm": total_p - total_pe,
        "runoff_coefficient": total_pe / total_p if total_p else 0.0,
        "S_mm": float(S),
        "Ia_mm": float(Ia),
    }


def unit_hydrograph(bp: BasinParams, dt_hr: float) -> tuple[np.ndarray, dict]:
    """SCS synthetic UH at dt intervals, m3/s per mm of effective rainfall.

        Tp = dt/2 + 0.6 tc        time to peak, hours
        Qp = 0.208 A / Tp         m3/s per mm, A in km2

    NORMALISED so the UH carries exactly 1 mm over the catchment:
        sum(uh) * dt * 3600 == A * 1e6 * 1e-3   m3
    Coarse sampling of a peaked curve breaks this by several percent, and the
    error goes straight into peak discharge. This is the step the original
    script was missing.
    """
    Tp = dt_hr / 2.0 + 0.6 * bp.tc_hr
    Qp = 0.208 * bp.area_km2 / Tp

    t, q = SCS_T_TP * Tp, SCS_Q_QP * Qp
    grid = np.arange(0.0, t.max() + dt_hr, dt_hr)
    uh = interp1d(t, q, kind="linear", bounds_error=False, fill_value=0.0)(grid)

    target = bp.area_km2 * 1000.0 / (dt_hr * 3600.0)     # m3/s-units summing to 1 mm
    raw = uh.sum()
    if raw <= 0:
        raise PrecipError(f"degenerate unit hydrograph for {bp.label}")
    factor = target / raw
    uh = uh * factor

    return uh, {
        "Tp_hr": float(Tp),
        "Qp_unit_cms_per_mm": float(uh.max()),
        "uh_volume_correction": float(factor),   # far from 1.0 => dt too coarse for Tp
        "uh_ordinates": int(uh.size),
    }
