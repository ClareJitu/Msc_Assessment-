"""Typed errors. The runner catches these per-station so one bad file
never kills a 10,000-file run."""


class PrecipError(Exception):
    """Base for everything this package raises."""


class SchemaError(PrecipError):
    """File is missing columns, has unparseable dates, or is empty."""


class InsufficientDataError(PrecipError):
    """Record is too short or too gappy for the requested stage."""


class FitError(PrecipError):
    """A distribution fit did not converge or returned implausible parameters."""


class BasinError(PrecipError):
    """Basin parameters are missing or outside the validity range of the method."""
