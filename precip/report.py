"""Render one station's stage payloads into a readable markdown report.

Shared by `precip report` (one station, explicit) and `precip run --report`
(batch). `run` writes parquet you query; this writes something a human reads.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import plots
from .config import Config
from .design import convolve, scs
from .io import basins as _basins
from .types import Context


def render(ctx: Context, payloads: dict, cfg: Config, out_dir: Path) -> Path:
    """Write report.md, figs/*.png and one CSV per long-format stage."""
    out = Path(out_dir)
    figs = out / "figs"
    out.mkdir(parents=True, exist_ok=True)
    st_qc = ctx.qc

    def tbl(rows, cols=None):
        df = pd.DataFrame(rows)
        if cols:
            df = df[[c for c in cols if c in df.columns]]
        return df.to_markdown(index=False, floatfmt=".4g")

    L = [f"# Precipitation analysis — {ctx.station_id}", "",
         f"Record {st_qc.first_timestamp:%Y-%m-%d} to {st_qc.last_timestamp:%Y-%m-%d} · "
         f"config `{cfg.fingerprint()}` · fit `{cfg.extremes.fit_method}`", ""]

    if "descriptive" in payloads:
        d = payloads["descriptive"]
        L += ["## 1. Data quality, basic statistics, P(dry), time series", "",
              tbl([{"metric": k, "value": v} for k, v in d.items()
                   if not k.startswith("r1_")]), "",
              f"![timeseries](figs/{plots.save_timeseries(ctx, figs)})", ""]

    if "ams" in payloads:
        a = payloads["ams"]
        L += ["## 2. Annual maxima series (hourly)", "",
              f"Monotonic across durations: **{a['ams_monotonic']}**"
              + (f" — violations: {a['ams_violations']}" if a["ams_violations"] else ""), "",
              tbl([r for r in a["rows"] if r["duration_hr"] == 1],
                  ["year", "depth_mm", "window_completeness", "end_time"]), "",
              f"![ams](figs/{plots.save_ams(ctx, cfg, figs, 1)})", ""]

    if "aggregate" in payloads:
        L += ["## 3. Statistics by aggregation scale", "",
              tbl(payloads["aggregate"]["rows"],
                  ["scale", "n_valid", "mean_mm", "variance", "std_mm", "cv", "prob_dry"]), "",
              "> COMMENTARY: describe how mean, CV and P(dry) change with scale, and why.", ""]

    if "gev" in payloads:
        g = payloads["gev"]
        rows = [{"duration_hr": d, "mu": g[f"gev_mu_{d}h"], "sigma": g[f"gev_sigma_{d}h"],
                 "xi": g[f"gev_xi_{d}h"], "KS p": g[f"gev_ks_p_{d}h"]}
                for d in cfg.extremes.durations_hr]
        L += ["## 4. Distribution fitted to the annual maxima", "",
              tbl(rows), "",
              f"![gev](figs/{plots.save_gev(ctx, cfg, figs, 1)})", "",
              "> COMMENTARY: justify GEV (extremal types theorem) and the L-moment estimator.", "",
              "## 5. Other methods of extracting extremes", "",
              "> COMMENTARY: block maxima vs peaks-over-threshold vs r-largest. "
              "Pros/cons: data efficiency, threshold choice, independence, "
              "declustering, bias-variance.", ""]

    if "idf" in payloads:
        L += ["## 6. Intensity-Duration-Frequency relationships", "",
              f"Monotonicity checks passed: **{payloads['idf']['idf_monotonic_ok']}**", "",
              tbl(payloads["idf"]["rows"]), "",
              f"![idf](figs/{plots.save_idf(ctx, cfg, figs)})", "",
              "> COMMENTARY: how intensity falls with duration and rises with return period.", ""]

    if "runoff" in payloads:
        r = payloads["runoff"]
        rows = pd.DataFrame(r["rows"])
        L += ["## 7. Synthetic hydrograph and runoff", ""]
        for bp in _basins.resolve(ctx.station_id, cfg):
            eff, _ = scs.effective_rainfall(ctx.hyetograph, bp.cn)
            uh, _ = scs.unit_hydrograph(bp, cfg.design.dt_hr)
            q, _ = convolve.direct_runoff(eff, uh, cfg.design.dt_hr, bp.area_km2)
            L += [f"**{bp.label}** — area {bp.area_km2} km², CN {bp.cn:.1f}, "
                  f"tc {bp.tc_hr} h", "",
                  tbl([{k.split('__')[1]: v for k, v in r.items()
                        if k.startswith(f"{bp.label}__")}]), "",
                  f"![hyd](figs/{plots.save_hydrograph(ctx, cfg, figs, bp.label, q, eff)})", ""]
        L += ["## 8. Uncertainties in the runoff estimate", "",
              "> COMMENTARY: distribution choice, parameter uncertainty at n="
              f"{payloads.get('gev', {}).get('n_years', '?')}, missing data and "
              "window censoring, the assumed basin parameters, SCS-CN and UH "
              "structural assumptions, measurement error.", ""]

    L += ["## 9. Automating over 10,000 files", "",
          "> COMMENTARY: this package is the answer — see README.", ""]

    if "descriptive" in payloads:
        d = payloads["descriptive"]
        L += ["## 10. Autocorrelation structure", "",
              tbl([{"scale": k[3:], "r1": v} for k, v in d.items() if k.startswith("r1_")]), "",
              f"![acf](figs/{plots.save_autocorr(ctx, cfg, figs)})", "",
              "> COMMENTARY: why dependence strengthens with aggregation.", ""]

    if "scaling" in payloads:
        s = payloads["scaling"]
        L += ["## 11. Scaling relationship", "",
              f"H = **{s['H']:.4f}**, linearity r² = **{s['K_linearity_r2']:.4f}** "
              f"→ **{s['scaling_type']}**", "",
              tbl(s["rows"]), "",
              f"![scaling](figs/{plots.save_scaling(ctx, cfg, figs, s)})", "",
              "> COMMENTARY: interpret simple vs multiscaling.", ""]

    (out / "report.md").write_text("\n".join(L), encoding="utf-8")
    for name, payload in payloads.items():
        if "rows" in payload:
            pd.DataFrame(payload["rows"]).to_csv(out / f"{name}.csv", index=False)

    path = out / "report.md"
    path.write_text("\n".join(L), encoding="utf-8")
    for name, payload in payloads.items():
        if "rows" in payload:
            pd.DataFrame(payload["rows"]).to_csv(out / f"{name}.csv", index=False)
    return path
