"""Frozen config, loaded from YAML, overridable from the CLI, hashed into
every output row so you can always tell which settings produced a number."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

import yaml


@dataclass(frozen=True)
class QCConfig:
    valid_min: float = 0.0
    """Values strictly below this are treated as missing sentinels (-99, -999...).
    This is the single place missingness is defined — see reader.read_station."""

    max_plausible_hourly_mm: float = 250.0
    """Hard ceiling for one hour. Catches decimal-point and unit errors that
    would otherwise become a station's annual maximum."""

    min_year_completeness: float = 0.90
    """Fraction of hours in a year that must be non-NaN for that year to
    contribute an annual maximum."""

    min_window_completeness: float = 0.90
    """Fraction of a rolling window that must be non-NaN for the window sum to
    count. Prevents a 1-hour value being labelled a 24-hour depth."""

    require_months: tuple[int, ...] = ()
    """Wet-season months that must all be present in a year for it to count.
    Empty = no seasonal check. Set this once you know the local regime."""


@dataclass(frozen=True)
class ExtremesConfig:
    durations_hr: tuple[int, ...] = (1, 3, 6, 12, 24, 48, 72)
    return_periods_yr: tuple[int, ...] = (2, 5, 10, 25, 50, 100)
    fit_method: str = "lmom"          # "lmom" | "mle"
    min_years: int = 15
    """Below this many complete years, the gev/idf stages refuse to fit rather
    than emit a confident-looking garbage shape parameter."""

    xi_bounds: tuple[float, float] = (-0.5, 0.5)
    """Fitted shape outside this range is flagged (not silently dropped)."""


@dataclass(frozen=True)
class BasinParams:
    area_km2: float
    cn: float
    tc_hr: float
    label: str = "default"


@dataclass(frozen=True)
class BasinConfig:
    source: str = "scenarios"         # "table" | "scenarios" | "single"
    table_path: Path | None = None
    join_key: str = "station_id"
    region_fallback: bool = True
    scenarios: tuple[BasinParams, ...] = (
        BasinParams(area_km2=10.0, cn=65.0, tc_hr=1.5, label="small_pervious"),
        BasinParams(area_km2=50.0, cn=75.0, tc_hr=3.0, label="mid_mixed"),
        BasinParams(area_km2=150.0, cn=88.0, tc_hr=6.0, label="large_urban"),
    )
    single: BasinParams = BasinParams(50.0, 75.0, 3.0, "default")
    amc: str = "II"                   # "I" | "II" | "III"


@dataclass(frozen=True)
class DesignConfig:
    storm_duration_hr: int = 24
    design_return_period_yr: int = 50
    dt_hr: float = 1.0
    peak_position: float = 0.5
    """Where the peak block sits in the storm, as a fraction of duration.
    0.5 = centred (classic alternating block); 0.375 is common in practice."""


@dataclass(frozen=True)
class Config:
    input_glob: str = "data/**/*.csv"
    out_dir: Path = Path("runs/latest")
    stages: tuple[str, ...] = ("descriptive", "ams", "gev", "idf")
    workers: int = 4
    resume: bool = True
    chunksize: int = 32
    flush_every: int = 500
    plots_sample: int = 0
    report_dir: Path | None = None
    seed: int = 12345

    qc: QCConfig = field(default_factory=QCConfig)
    extremes: ExtremesConfig = field(default_factory=ExtremesConfig)
    basin: BasinConfig = field(default_factory=BasinConfig)
    design: DesignConfig = field(default_factory=DesignConfig)

    def fingerprint(self) -> str:
        """Stable short hash of everything that affects the numbers.
        Goes into the manifest and every summary row."""
        payload = asdict(self)
        payload.pop("workers")
        payload.pop("chunksize")
        payload.pop("plots_sample")
        payload.pop("resume")
        payload.pop("report_dir")
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


_TOP_KEYS = {"input_glob", "out_dir", "stages", "workers", "resume",
             "chunksize", "flush_every", "plots_sample", "report_dir", "seed"}
_SECTIONS = {"qc", "extremes", "basin", "design"}


def _build(cls, raw: dict | None):
    """Instantiate a nested config dataclass, coercing YAML lists to tuples
    (frozen dataclasses must stay hashable) and rejecting unknown keys."""
    raw = dict(raw or {})
    known = {f.name for f in fields(cls)}
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown keys {sorted(unknown)}; known: {sorted(known)}")
    return cls(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in raw.items()})


def _build_basin(raw: dict | None) -> BasinConfig:
    raw = dict(raw or {})
    scenarios = raw.pop("scenarios", None)
    single = raw.pop("single", None)
    table_path = raw.pop("table_path", None)
    kw: dict = dict(raw)
    if scenarios is not None:
        kw["scenarios"] = tuple(BasinParams(**s) for s in scenarios)
    if single is not None:
        kw["single"] = BasinParams(**single)
    kw["table_path"] = Path(table_path) if table_path else None
    return _build(BasinConfig, kw)


def _validate(cfg: Config) -> None:
    """Fail here, not 4,000 stations into a run."""
    from .stages import STAGES                     # deferred: stages imports config

    unknown = set(cfg.stages) - set(STAGES)
    if unknown:
        raise ValueError(f"unknown stage(s) {sorted(unknown)}; available: {sorted(STAGES)}")
    if not cfg.stages:
        raise ValueError("no stages selected")
    if cfg.extremes.fit_method not in {"lmom", "mle"}:
        raise ValueError(f"fit_method must be lmom or mle, got {cfg.extremes.fit_method!r}")
    if cfg.basin.source not in {"table", "scenarios", "single"}:
        raise ValueError(f"basin.source must be table|scenarios|single, got {cfg.basin.source!r}")
    if cfg.basin.source == "table" and cfg.basin.table_path is None:
        raise ValueError("basin.source is 'table' but basin.table_path is unset")
    if cfg.basin.amc not in {"I", "II", "III"}:
        raise ValueError(f"basin.amc must be I, II or III, got {cfg.basin.amc!r}")
    if not 0.0 <= cfg.design.peak_position <= 1.0:
        raise ValueError("design.peak_position must be in [0, 1]")
    if cfg.design.storm_duration_hr not in cfg.extremes.durations_hr:
        raise ValueError(
            f"design.storm_duration_hr={cfg.design.storm_duration_hr} is not in "
            f"extremes.durations_hr={list(cfg.extremes.durations_hr)}; the design storm "
            f"is interpolated from the IDF, so the duration must be fitted"
        )
    if cfg.workers < 1:
        raise ValueError("workers must be >= 1")


def load_config(path: Path | None = None, **overrides) -> Config:
    """YAML -> Config, then apply non-None CLI overrides.

    Unknown keys raise rather than being silently ignored: a typo in a YAML
    key would otherwise leave you running defaults and wondering why a setting
    had no effect.
    """
    raw: dict = {}
    if path is not None:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"config not found: {p}")
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    stray = set(raw) - _TOP_KEYS - _SECTIONS
    if stray:
        raise ValueError(f"unknown top-level config keys: {sorted(stray)}")

    top = {k: v for k, v in raw.items() if k in _TOP_KEYS}
    top.update({k: v for k, v in overrides.items() if v is not None})
    if "out_dir" in top:
        top["out_dir"] = Path(top["out_dir"])
    if "stages" in top:
        top["stages"] = tuple(top["stages"])

    cfg = Config(
        qc=_build(QCConfig, raw.get("qc")),
        extremes=_build(ExtremesConfig, raw.get("extremes")),
        basin=_build_basin(raw.get("basin")),
        design=_build(DesignConfig, raw.get("design")),
        **top,
    )
    _validate(cfg)
    return cfg
