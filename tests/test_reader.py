
import numpy as np
import pandas as pd
import pytest


def make_csv(tmp_path, values, sentinel=-99.0):
    """TODO: write a small CSV with year/month/date + HRF01..HRF24."""
    raise NotImplementedError


def test_sentinels_become_nan(tmp_path):
    """A -99 must not survive into the series, and must not be summed into a
    daily total. This is bug #1 from the original script."""
    raise NotImplementedError


def test_gaps_are_explicit(tmp_path):
    """A missing calendar day must appear as 24 NaN hours in the index, not as
    absent timestamps — rolling() behaves differently in the two cases."""
    raise NotImplementedError


def test_incomplete_window_is_nan():
    """A 24h rolling window with 20 valid hours must be NaN at 0.90
    completeness, not a 20-hour sum mislabelled as a 24-hour depth."""
    raise NotImplementedError


def test_incomplete_year_excluded_from_ams():
    """A year below min_year_completeness contributes no annual maximum."""
    raise NotImplementedError


def test_unit_hydrograph_conserves_volume():
    """sum(uh) * dt * 3600 == area_km2 * 1e3 m3 per mm, to 1e-9 relative.
    This is the normalisation the original was missing."""
    raise NotImplementedError


def test_convolution_conserves_volume():
    """Total hydrograph volume == effective rainfall depth x area."""
    raise NotImplementedError


def test_alternating_block_totals_match_idf():
    """sum(hyetograph) == IDF depth at the design duration and return period."""
    raise NotImplementedError


def test_idf_is_monotonic():
    """Intensity falls with duration at fixed T; rises with T at fixed duration."""
    raise NotImplementedError
