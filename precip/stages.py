"""Stage registry and dependency resolution.

This is what makes `--stages runoff` safe: asking for runoff pulls in
descriptive -> ams -> gev -> idf -> design_storm ahead of it, in order.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import scaling as _scaling
from .config import Config
from .design import convolve, scs, storm
from .extremes import ams as _ams
from .extremes import gev as _gev
from .extremes import idf as _idf
from .io import basins as _basins
from .stats import descriptive as _desc
from .stats.aggregate import SCALES, block_aggregate
from .types import Context

StageFn = Callable[[Context, Config], dict[str, Any]]
STAGES: dict[str, "Stage"] = {}


@dataclass(frozen=True)
class Stage:
    name: str
    requires: tuple[str, ...]
    fn: StageFn


def stage(name: str, requires: tuple[str, ...] = ()):
    def deco(fn: StageFn) -> StageFn:
        STAGES[name] = Stage(name, requires, fn)
        return fn
    return deco


def resolve(selected: Iterable[str]) -> list[Stage]:
    
    selected = list(selected)
    order: list[Stage] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str, path: tuple[str, ...] = ()) -> None:
        if name in done:
            return
        if name in visiting:
            raise ValueError(f"circular dependency: {' -> '.join((*path, name))}")
        if name not in STAGES:
            raise KeyError(f"unknown stage {name!r}; available: {sorted(STAGES)}")
        visiting.add(name)
        for dep in STAGES[name].requires:
            visit(dep, (*path, name))
        visiting.discard(name)
        done.add(name)
        order.append(STAGES[name])

    for name in selected:
        visit(name)
    return order


# --------------------------------------------------------------------------
# Stage implementations. 
# No printing, no plotting, no file IO in here.
# --------------------------------------------------------------------------

@stage("descriptive")
def descriptive(ctx: Context, cfg: Config) -> dict:
    """Items 1 and 10: basic statistics, L-moments, P(dry), autocorrelation."""
    q = ctx.qc
    return {
        "pct_missing": q.pct_missing,
        "n_hours_expected": q.n_hours_expected,
        "n_hours_present": q.n_hours_present,
        "n_sentinel_masked": q.n_sentinel_masked,
        "n_complete_years": len(q.complete_years),
        "n_rejected_years": len(q.rejected_years),
        "rejected_years": ",".join(str(y) for y in q.rejected_years) or None,
        "first_year": int(q.first_timestamp.year),
        "last_year": int(q.last_timestamp.year),
        **_desc.summarise(ctx.series),
        **_desc.l_moment_ratios(ctx.series),
        **_desc.lag1_autocorrelation(ctx.series, cfg),
    }


@stage("aggregate", requires=("descriptive",))
def aggregate(ctx: Context, cfg: Config) -> dict:
    """Item 3: statistics at 3h / 6h / 12h / 1D / 7D / 15D.

    Strict windows here (not min_periods=1): these are means and dry
    probabilities, not maxima, so a partial sum genuinely mislabels the scale.
    """
    rows = []
    for label, freq in SCALES.items():
        agg = ctx.series if label == "1h" else block_aggregate(ctx.series, freq, cfg)
        stats = _desc.summarise(agg)
        if not stats.get("n_valid"):
            continue
        rows.append({"scale": label, **{k: v for k, v in stats.items()}})
    return {"rows": rows, "n_scales": len(rows)}


@stage("ams", requires=("descriptive",))
def ams(ctx: Context, cfg: Config) -> dict:
    """Item 2 (d=1) and the input to every extreme-value stage."""
    rows, peaks = [], {}
    for d in cfg.extremes.durations_hr:
        frame = _ams.annual_maxima(ctx.series, d, cfg, ctx.qc.complete_years)
        ctx.ams[d] = frame
        peaks[d] = float(frame["depth_mm"].max())
        rows += [{"duration_hr": int(d), "year": int(y),
                  "depth_mm": float(r.depth_mm),
                  "window_completeness": float(r.completeness),
                  "end_time": str(r.end_time)}
                 for y, r in frame.iterrows()]

    ok, violations = _ams.check_monotonic(peaks)
    return {
        "rows": rows,
        "n_years": int(len(ctx.ams[cfg.extremes.durations_hr[0]])),
        "ams_monotonic": ok,
        "ams_violations": "; ".join(violations) or None,
        "min_window_completeness_used": float(
            min(f["completeness"].min() for f in ctx.ams.values())),
        **{f"ams_max_{d}h_mm": v for d, v in peaks.items()},
    }


@stage("gev", requires=("ams",))
def gev(ctx: Context, cfg: Config) -> dict:
    """Item 4: fit a distribution to each duration's annual maxima."""
    out: dict = {"fit_method": cfg.extremes.fit_method}
    for d in cfg.extremes.durations_hr:
        mu, sigma, xi, diag = _gev.fit(ctx.ams[d]["depth_mm"], cfg)
        ctx.gev[d] = (mu, sigma, xi)
        out[f"gev_mu_{d}h"] = mu
        out[f"gev_sigma_{d}h"] = sigma
        out[f"gev_xi_{d}h"] = xi
        out[f"gev_ks_p_{d}h"] = diag["ks_pvalue"]
        out[f"gev_xi_flagged_{d}h"] = diag["xi_flagged"]
    out["n_years"] = int(len(ctx.ams[cfg.extremes.durations_hr[0]]))
    return out


@stage("idf", requires=("gev",))
def idf(ctx: Context, cfg: Config) -> dict:
    """Item 6: intensity-duration-frequency relationships."""
    ctx.idf, diag = _idf.build(ctx, cfg)
    return {"rows": _idf.to_rows(ctx.idf, cfg), **diag}


@stage("design_storm", requires=("idf",))
def design_storm(ctx: Context, cfg: Config) -> dict:
    """Item 7a: alternating-block hyetograph from this station's own IDF."""
    ctx.hyetograph, metrics = storm.alternating_block(ctx, cfg)
    return metrics


@stage("runoff", requires=("design_storm",))
def runoff(ctx: Context, cfg: Config) -> dict:
    """Item 7b: SCS losses -> unit hydrograph -> convolution -> peak discharge.

    Can return MULTIPLE results per station (one per basin scenario). Scalars
    are prefixed by basin label; the full hydrographs go to the long table.
    """
    dt = cfg.design.dt_hr
    rows: list[dict] = []
    out: dict = {}
    for bp in _basins.resolve(ctx.station_id, cfg):
        eff, loss = scs.effective_rainfall(ctx.hyetograph, bp.cn)
        uh, uh_m = scs.unit_hydrograph(bp, dt)
        q, flow = convolve.direct_runoff(eff, uh, dt, bp.area_km2)
        ctx.effective_rainfall = eff
        rows += convolve.to_rows(q, ctx.hyetograph, eff, dt, bp.label)
        for k, v in {**loss, **uh_m, **flow}.items():
            out[f"{bp.label}__{k}"] = v
        out[f"{bp.label}__area_km2"] = bp.area_km2
        out[f"{bp.label}__cn"] = bp.cn
        out[f"{bp.label}__tc_hr"] = bp.tc_hr
    out["rows"] = rows
    out["n_basins"] = len({r["basin"] for r in rows})
    return out


@stage("scaling", requires=("ams",))
def scaling(ctx: Context, cfg: Config) -> dict:
    """Item 11: simple vs multiscaling from moment scaling exponents."""
    return _scaling.analyse(ctx, cfg)
