"""Two-tier test strategy, same pattern used across this ecosystem (buradane's
DB-skip fixture, voice-io-mcp's local-extra-skip fixture): pure-logic tests
(chunking, hashing, file walking) always run. Anything touching the real
fastembed model or the sqlite-vec extension needs `local_notes_search`'s
dependencies actually importable AND (for fastembed) a first-run model
download over the network - both skip cleanly, not fail, when unavailable,
so `uv run pytest` gives an honest signal in an offline/minimal CI runner."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest


def _model_available() -> bool:
    try:
        import local_notes_search as lns

        lns._get_model()
        return True
    except Exception:
        return False


def _sqlite_vec_available() -> bool:
    try:
        import sqlite3

        import sqlite_vec

        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.close()
        return True
    except Exception:
        return False


MODEL_AVAILABLE = _model_available()
SQLITE_VEC_AVAILABLE = _sqlite_vec_available()

requires_model = pytest.mark.skipif(not MODEL_AVAILABLE, reason="fastembed model not available (no network on first run, or dependency missing)")
requires_sqlite_vec = pytest.mark.skipif(not SQLITE_VEC_AVAILABLE, reason="sqlite-vec extension not loadable in this environment")


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test-index.db"


@pytest.fixture
def tmp_notes_dir():
    d = Path(tempfile.mkdtemp(prefix="lns-test-"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
