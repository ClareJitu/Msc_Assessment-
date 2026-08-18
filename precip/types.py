# """Data carriers passed between stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class QCReport:

    n_hours_expected: int
    n_hours_present: int
    n_sentinel_masked: int
    n_implausible_masked: int
    pct_missing: float
    first_timestamp: pd.Timestamp
    last_timestamp: pd.Timestamp
    complete_years: tuple[int, ...]
    rejected_years: tuple[int, ...]


@dataclass
class StationSeries:
    station_id: str
    series: pd.Series          
    qc: QCReport


@dataclass
class Context:

    station_id: str
    series: pd.Series
    qc: QCReport

    
    ams: dict[int, pd.Series] = field(default_factory=dict)      # duration_hr -> year->depth_mm
    gev: dict[int, tuple[float, float, float]] = field(default_factory=dict)  # d -> (mu, sigma, xi)
    idf: np.ndarray | None = None            # shape (n_durations, n_return_periods), mm/hr
    hyetograph: np.ndarray | None = None     # mm per dt
    effective_rainfall: np.ndarray | None = None


@dataclass
class StationResult:

    station_id: str
    outputs: dict[str, dict[str, Any]]   # stage name -> stage payload
    errors: list[dict[str, str]]
    elapsed_s: float

    @property
    def ok(self) -> bool:
        return not self.errors
