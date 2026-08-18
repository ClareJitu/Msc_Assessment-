# precip-pipeline

Batch precipitation → IDF → design storm → runoff, over ~10,000 station files.

## Quick start

```bash
pip install -r requirements.txt
python -m precip stages                      # what can I run?
python -m precip inspect data/station_0001.csv   # QC one file first
python -m precip run --limit 50 --workers 1  # smoke test
python -m precip run --workers 8             # the real thing
```

## Selecting what runs

`--stages` takes a comma-separated list. Dependencies resolve automatically,
so `--stages runoff` expands to
`descriptive → ams → gev → idf → design_storm → runoff`.

| stage | needs | produces |
|---|---|---|
| `descriptive` | — | mean, var, std, L-moments, P(dry), lag-1 autocorrelation |
| `ams` | descriptive | annual maxima per duration (complete years only) |
| `gev` | ams | μ, σ, ξ per duration + fit diagnostics |
| `idf` | gev | intensity-duration-frequency table |
| `design_storm` | idf | alternating-block hyetograph from *this station's* IDF |
| `runoff` | design_storm | effective rainfall, unit hydrograph, DRH, peak Q |
| `scaling` | ams | K(q) exponents, simple vs multiscaling |

## Output

Long-format parquet shards, not 10,000 JSON files — so cross-station questions
are one query instead of a 10,000-iteration loop.

```
runs/latest/
├── summary/part-*.parquet      one row per station (all scalars)
├── ams/part-*.parquet          station × duration × year
├── idf/part-*.parquet          station × duration × return period
├── hydrograph/part-*.parquet   station × basin × hour
├── scaling/part-*.parquet      station × moment order
└── manifest.jsonl              status, errors, timings, config hash
```

```python
import duckdb
duckdb.sql("""
  SELECT station_id, intensity_mm_hr
  FROM 'runs/latest/idf/*.parquet'
  WHERE duration_hr = 24 AND return_period_yr = 100
  ORDER BY intensity_mm_hr DESC LIMIT 20
""")
```

`--resume` reads `manifest.jsonl` and skips completed stations. You will crash
partway through at least once; this makes that cheap.

## Basin parameters

A precipitation file is a rain gauge. A hydrograph needs a catchment.
See `precip/io/basins.py` for the three options — `table` (real catchments),
`scenarios` (archetype screening, the default when you have no catchment data),
and `single`. The default config runs each station against three archetypes and
labels the output accordingly, rather than pretending one basin fits all.

## What's implemented vs. skeletoned

Plumbing is working code: `reader.py`, `writer.py`, `runner.py`, the stage
registry decorator, config dataclasses, `basins.validate`, `tc_kirpich`,
`cn_adjust_amc`.

Domain math is skeletoned with numbered TODOs — you already wrote it once, and
the docstrings say exactly where each piece plugs in and which bug to fix on
the way. `config.load_config`, `stages.resolve`, and `__main__.main` are the
three that unblock everything else; do those first.

## Bugs the skeleton is designed to prevent

1. Sentinel values summed into totals (`reader` masks once, at the boundary)
2. `min_periods=1` turning a 1-hour value into a 24-hour depth
3. Incomplete years contributing annual maxima
4. MLE shape parameters diverging silently across 10k fits (L-moments default)
5. A hardcoded design storm making every station's hydrograph identical
6. An unnormalised unit hydrograph leaking volume into peak discharge
# Msc_Assessment-
