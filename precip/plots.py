"""Figures. Off by default in `run` (10,000 x 7 figures is 70,000 files nobody
opens); always produced by `report` for a single station."""

from __future__ import annotations

import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")          # MUST precede the pyplot import, in every worker

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from scipy.stats import genextreme  # noqa: E402

from .config import Config  # noqa: E402
from .stats.aggregate import SCALES, block_aggregate  # noqa: E402
from .types import Context  # noqa: E402


def sample_station_ids(all_ids, n: int, seed: int) -> list[str]:
    """Deterministic subset, so successive runs plot the SAME stations and a
    fix can be compared against the previous run."""
    ids = sorted(all_ids)
    return random.Random(seed).sample(ids, min(n, len(ids)))


def _save(fig, out: Path, name: str) -> str:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.png"
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)             # always close: a leaked figure per station exhausts memory
    return path.name


def save_timeseries(ctx: Context, out: Path) -> str:
    daily = ctx.series.resample("1D").sum(min_count=1)
    fig, ax = plt.subplots(figsize=(13, 4.5))
    ax.plot(daily.index, daily.to_numpy(), color="steelblue", lw=0.7)
    ax.set(xlabel="Year", ylabel="Precipitation (mm)",
           title=f"Daily total precipitation — {ctx.station_id}")
    ax.grid(ls="--", alpha=0.4)
    return _save(fig, out, "timeseries")


def save_ams(ctx: Context, cfg: Config, out: Path, duration_hr: int = 1) -> str:
    f = ctx.ams[duration_hr]
    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.bar(f.index, f["depth_mm"], color="teal", edgecolor="black")
    # shade maxima drawn from incomplete windows — the qualifier made visible
    for b, c in zip(bars, f["completeness"]):
        if c < 1.0:
            b.set_color("indianred")
    ax.set(xlabel="Year", ylabel=f"Max {duration_hr}-hour depth (mm)",
           title=f"Annual maxima, {duration_hr}h — red = partial window")
    ax.grid(axis="y", ls="--", alpha=0.5)
    return _save(fig, out, f"ams_{duration_hr}h")


def save_gev(ctx: Context, cfg: Config, out: Path, duration_hr: int = 1) -> str:
    x = ctx.ams[duration_hr]["depth_mm"].to_numpy()
    mu, sigma, xi = ctx.gev[duration_hr]
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.hist(x, bins=12, density=True, alpha=0.6, color="teal",
            edgecolor="black", label="Annual maxima")
    grid = np.linspace(x.min() * 0.7, x.max() * 1.4, 300)
    ax.plot(grid, genextreme.pdf(grid, -xi, loc=mu, scale=sigma), "r-", lw=2.5,
            label=f"GEV ({cfg.extremes.fit_method}): ξ={xi:.3f}")
    ax.set(xlabel=f"Max {duration_hr}-hour depth (mm)", ylabel="Density",
           title=f"GEV fit to {duration_hr}h annual maxima")
    ax.legend(); ax.grid(ls="--", alpha=0.4)
    return _save(fig, out, f"gev_{duration_hr}h")


def save_idf(ctx: Context, cfg: Config, out: Path) -> str:
    ds, Ts = cfg.extremes.durations_hr, cfg.extremes.return_periods_yr
    fig, ax = plt.subplots(figsize=(11, 6))
    for j, T in enumerate(Ts):
        ax.plot(ds, ctx.idf[:, j], marker="o", lw=2.2, label=f"{T}-year")
    ax.set_xscale("log")
    ax.set_xticks(ds); ax.set_xticklabels([f"{d}h" for d in ds])
    ax.set(xlabel="Duration (log scale)", ylabel="Intensity (mm/hr)",
           title=f"IDF curves — {ctx.station_id}")
    ax.grid(which="both", ls="--", alpha=0.5)
    ax.legend(title="Return period", bbox_to_anchor=(1.02, 1), loc="upper left")
    return _save(fig, out, "idf")


def save_hydrograph(ctx: Context, cfg: Config, out: Path, label: str,
                    q: np.ndarray, eff: np.ndarray) -> str:
    dt = cfg.design.dt_hr
    t = np.arange(len(q)) * dt
    hy = ctx.hyetograph
    fig, ax1 = plt.subplots(figsize=(12, 6.5))
    ax1.plot(t, q, color="darkblue", lw=2.4, label="Direct runoff")
    ax1.fill_between(t, q, color="blue", alpha=0.12)
    ax1.set(xlabel="Time (hours)", ylabel="Discharge (m³/s)")
    ax1.set_ylim(0, q.max() * 1.3)
    ax2 = ax1.twinx()
    th = np.arange(len(hy)) * dt
    ax2.bar(th, hy, width=dt, color="lightblue", edgecolor="white",
            align="edge", alpha=0.75, label="Total rainfall")
    ax2.bar(th, eff, width=dt, color="darkblue", edgecolor="white",
            align="edge", alpha=0.9, label="Effective rainfall")
    ax2.set_ylabel("Precipitation (mm)")
    ax2.set_ylim(hy.max() * 3, 0)          # rain "falls" from the top
    ax1.set_title(f"{cfg.design.storm_duration_hr}h design storm & runoff — {label}")
    return _save(fig, out, f"hydrograph_{label}")


def save_autocorr(ctx: Context, cfg: Config, out: Path) -> str:
    labels, vals = [], []
    for label, freq in SCALES.items():
        agg = ctx.series if label == "1h" else block_aggregate(ctx.series, freq, cfg)
        v = agg.dropna()
        labels.append(label); vals.append(v.autocorr(lag=1) if len(v) > 2 else np.nan)
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(labels, vals, marker="s", color="darkred", lw=2.4, markersize=8)
    ax.set(xlabel="Aggregation scale", ylabel="Lag-1 autocorrelation $r_1$",
           title="Autocorrelation structure across time scales")
    ax.grid(ls="--", alpha=0.5)
    return _save(fig, out, "autocorrelation")


def save_scaling(ctx: Context, cfg: Config, out: Path, result: dict) -> str:
    ds = sorted(ctx.ams)
    log_d = np.log(ds)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 5.5))
    for row in result["rows"]:
        q = row["q"]
        m = [np.mean(ctx.ams[d]["depth_mm"].to_numpy() ** q) for d in ds]
        a1.plot(log_d, np.log(m), marker="o", lw=2, label=f"q={q}")
        a1.plot(log_d, row["intercept"] + row["K_q"] * log_d, ls="--", color="gray", alpha=0.6)
    a1.set(xlabel=r"$\log(d)$", ylabel=r"$\log(E[P^q])$", title="Moments vs duration")
    a1.legend(); a1.grid(ls="--", alpha=0.5)

    qs = [r["q"] for r in result["rows"]]; Ks = [r["K_q"] for r in result["rows"]]
    a2.plot(qs, Ks, marker="s", color="darkred", lw=2.4, markersize=8, label="K(q)")
    a2.plot(qs, np.polyval(np.polyfit(qs, Ks, 1), qs), ls="--", color="black",
            label=f"linear fit (H={result['H']:.3f}, r²={result['K_linearity_r2']:.4f})")
    a2.set(xlabel="Moment order q", ylabel="K(q)",
           title=f"Scaling type: {result['scaling_type']}")
    a2.legend(); a2.grid(ls="--", alpha=0.5)
    fig.tight_layout()
    return _save(fig, out, "scaling")
