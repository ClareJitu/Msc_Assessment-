"""Basin parameter resolution.

THE PROBLEM
    A precipitation file is a rain gauge. A hydrograph needs a catchment.
    Hardcoding one basin for 10,000 files does not produce 10,000 results —
    it produces one result printed 10,000 times.

THREE OPTIONS, descending defensibility:
  1. "table"     real catchments, basins.csv joined on station_id
  2. "scenarios" no catchments, so don't pretend: run each station against N
                 archetypes. Output grain becomes station x scenario, and the
                 honest reading is "what this rainfall does to a basin OF THIS
                 TYPE" — a regional screen, not a site-specific design number.
  3. "single"    one basin for everything. Correct for a single-station
                 assessment where the brief says "assume required parameters".

DERIVING WHAT YOU LACK
    tc from length + slope -> tc_kirpich(), defensible
    tc from area alone     -> tc_from_area(), WEAK, exponent is regional
    CN for wetness         -> cn_adjust_amc(), well established
    A wrong tc moves peak discharge more than a wrong CN does, so prefer
    scenarios over a fabricated tc.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import BasinConfig, BasinParams, Config
from ..errors import BasinError

AREA_LIMITS_KM2 = (0.05, 250.0)      # lumped SCS UH validity envelope
CN_LIMITS = (30.0, 98.0)


@lru_cache(maxsize=8)
def load_basin_table(path: Path, join_key: str = "station_id") -> pd.DataFrame:
    """Read basins.csv, deriving tc where possible. Cached per worker process."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    for col in (join_key, "area_km2", "cn"):
        if col not in df.columns:
            raise BasinError(f"{path}: missing required column {col!r}")
    if "tc_hr" not in df.columns:
        df["tc_hr"] = np.nan
    if {"length_m", "slope"} <= set(df.columns):
        need = df["tc_hr"].isna() & df["length_m"].notna() & df["slope"].notna()
        df.loc[need, "tc_hr"] = [
            tc_kirpich(l, s) for l, s in zip(df.loc[need, "length_m"], df.loc[need, "slope"])
        ]
    # rows still lacking tc are left NaN and rejected at resolve(), rather than
    # silently taking a default that would look like a real measurement
    return df.set_index(join_key)


def resolve(station_id: str, cfg: Config, table: pd.DataFrame | None = None) -> list[BasinParams]:
    """Basins to run this station against. Always a list, even of length 1."""
    b: BasinConfig = cfg.basin
    out: list[BasinParams] = []

    if b.source == "table":
        if table is None:
            table = load_basin_table(b.table_path, b.join_key)
        row = table.loc[station_id] if station_id in table.index else None
        if row is None and b.region_fallback and "region" in table.columns:
            raise BasinError(f"{station_id}: not in basin table and no region to fall back on")
        if row is None:
            raise BasinError(f"{station_id}: not in basin table {b.table_path}")
        if pd.isna(row["tc_hr"]):
            raise BasinError(f"{station_id}: no tc_hr and none derivable")
        out = [BasinParams(float(row["area_km2"]), float(row["cn"]), float(row["tc_hr"]),
                           str(row.get("label", station_id)))]
    elif b.source == "scenarios":
        out = list(b.scenarios)
    else:
        out = [b.single]

    resolved = []
    for bp in out:
        bp = BasinParams(bp.area_km2, cn_adjust_amc(bp.cn, b.amc), bp.tc_hr, bp.label)
        validate(bp, cfg.design.dt_hr)
        resolved.append(bp)
    return resolved


def tc_kirpich(length_m: float, slope: float) -> float:
    """Kirpich (1940) time of concentration, hours.

    Calibrated on small rural catchments; commonly applied with multipliers
    (x0.4 for overland flow on concrete/asphalt, x2 for natural grassed
    channels). Report which you used.
    """
    if length_m <= 0 or slope <= 0:
        raise BasinError(f"kirpich needs positive length/slope, got {length_m=} {slope=}")
    return 0.0195 * (length_m ** 0.77) * (slope ** -0.385) / 60.0


def tc_from_area(area_km2: float, a: float = 0.5, b: float = 0.38) -> float:
    """tc = a * A**b, hours. WEAK — coefficients are regional and these are
    placeholders, not a recommendation. Fit a and b against gauged catchments
    you actually have, or don't use this. It exists so the failure mode is
    explicit rather than hidden inside a default."""
    return a * (area_km2 ** b)


def cn_adjust_amc(cn_ii: float, amc: str) -> float:
    """CN(II) -> CN(I) dry or CN(III) wet.

    Matters: CN 75 -> 55 dry or 87 wet, which roughly doubles peak discharge
    across that range. A design storm on a saturated catchment is a different
    event, so state which AMC you assumed.
    """
    if amc == "II":
        return cn_ii
    if amc == "I":
        return 4.2 * cn_ii / (10.0 - 0.058 * cn_ii)
    if amc == "III":
        return 23.0 * cn_ii / (10.0 + 0.13 * cn_ii)
    raise BasinError(f"amc must be I, II or III; got {amc!r}")


def validate(bp: BasinParams, dt_hr: float) -> None:
    """Reject basins outside the method's validity envelope. Fail loudly: a
    tc of 0.2 h at dt of 1 h gives Tp = 0.62 h, which a 1-hour UH cannot
    resolve — the resulting peak is an artefact of the time step."""
    lo, hi = AREA_LIMITS_KM2
    if not lo <= bp.area_km2 <= hi:
        raise BasinError(f"{bp.label}: area {bp.area_km2} km2 outside {lo}-{hi}")
    lo, hi = CN_LIMITS
    if not lo <= bp.cn <= hi:
        raise BasinError(f"{bp.label}: CN {bp.cn:.1f} outside {lo}-{hi}")
    tp = dt_hr / 2.0 + 0.6 * bp.tc_hr
    if tp < 2.0 * dt_hr:
        raise BasinError(f"{bp.label}: Tp={tp:.2f} h under-resolved at dt={dt_hr} h")
