"""Load data/raw into the DuckDB ``raw`` schema, under contract.

Validation happens in pandas, partition by partition; the load itself happens
in DuckDB's Parquet reader so the full profile never passes through Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from ..config import RAW_DIR, TRUTH_DIR, WAREHOUSE_PATH, Config, load_config
from ..contracts import PARTITIONED, SOURCE_SCHEMAS

MAX_FULLY_VALIDATED_PARTITIONS = 30


@dataclass
class LoadResult:
    table: str
    rows: int
    partitions: int
    validated: int


def _files(source: str) -> list[Path]:
    root = RAW_DIR / source
    if not root.exists():
        raise FileNotFoundError(f"No raw data for {source!r}. Run `make data` first.")
    pattern = "dt=*/*.parquet" if source in PARTITIONED else "*.parquet"
    return sorted(root.glob(pattern))


def load_raw(cfg: Config | None = None, validate: bool = True) -> list[LoadResult]:
    cfg = cfg or load_config()
    WAREHOUSE_PATH.parent.mkdir(parents=True, exist_ok=True)

    results: list[LoadResult] = []
    con = duckdb.connect(str(WAREHOUSE_PATH))
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS raw")
        for source, schema in SOURCE_SCHEMAS.items():
            files = _files(source)
            validated = 0
            if validate:
                step = max(len(files) // MAX_FULLY_VALIDATED_PARTITIONS, 1)
                for path in files[::step][:MAX_FULLY_VALIDATED_PARTITIONS]:
                    schema.validate(pd.read_parquet(path), lazy=True)
                    validated += 1

            glob = str(
                RAW_DIR / source / ("dt=*/*.parquet" if source in PARTITIONED else "*.parquet")
            )
            con.execute(f"DROP TABLE IF EXISTS raw.{source}")
            con.execute(
                f"CREATE TABLE raw.{source} AS "
                f"SELECT * FROM read_parquet('{glob}', union_by_name = true)"
            )
            row = con.execute(f"SELECT count(*) FROM raw.{source}").fetchone()
            assert row is not None
            results.append(LoadResult(source, int(row[0]), len(files), validated))

        # Truth is loaded into its own schema, kept out of `raw` so no dbt
        # model can reach it by accident. Only the grading code reads it.
        con.execute("CREATE SCHEMA IF NOT EXISTS truth")
        for name, pattern in (
            ("employee_day_truth", "dt=*/*.parquet"),
            ("allocation_truth", "*.parquet"),
        ):
            glob = str(TRUTH_DIR / name / pattern)
            con.execute(f"DROP TABLE IF EXISTS truth.{name}")
            con.execute(f"CREATE TABLE truth.{name} AS SELECT * FROM read_parquet('{glob}')")
    finally:
        con.close()
    return results


if __name__ == "__main__":  # pragma: no cover
    for r in load_raw():
        print(
            f"raw.{r.table:20s} {r.rows:>12,} rows "
            f"({r.partitions} partitions, {r.validated} validated)"
        )
