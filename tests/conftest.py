from __future__ import annotations

import duckdb
import pytest

from absence_engine.config import WAREHOUSE_PATH, load_config


@pytest.fixture(scope="session")
def cfg():
    return load_config(profile="demo")


@pytest.fixture(scope="session")
def con():
    if not WAREHOUSE_PATH.exists():
        pytest.skip("no warehouse; run `make pipeline` first")
    connection = duckdb.connect(str(WAREHOUSE_PATH), read_only=True)
    yield connection
    connection.close()


def scalar(con, sql: str, params=None):
    row = con.execute(sql, params or []).fetchone()
    return row[0] if row else None
