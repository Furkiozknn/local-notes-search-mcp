"""The index records WHICH embedder built each file's vectors, not just the
model name.

fastembed 0.6.0 moved paraphrase-multilingual-MiniLM-L12-v2 from CLS to mean
pooling: same model name, incomparable vectors. An index built under 0.5.1
and queried under 0.6+ returned results that were silently worse. These
tests pin that the fastembed version is part of the recorded identity, that
a changed identity forces a re-embed of unchanged files, and that searches
say so until that has happened. Stand-in embedder, so no model download."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

import local_notes_search as lns
from tests.conftest import requires_sqlite_vec


def _fake_embed(texts: list[str]) -> list[list[float]]:
    dim = lns._embedding_dim()
    out = []
    for text in texts:
        digest = hashlib.sha256(text.encode()).digest()
        out.append([digest[i % len(digest)] / 255.0 for i in range(dim)])
    return out


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_db_path: Path):
    monkeypatch.setattr(lns, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(lns, "embed_texts", _fake_embed)
    monkeypatch.delenv(lns.ALLOWED_ROOTS_ENV, raising=False)


def _pretend_fastembed(monkeypatch, version: str) -> None:
    monkeypatch.setattr(lns, "_fastembed_version", lambda: version)


def test_embedder_id_names_the_model_and_the_fastembed_version(monkeypatch):
    _pretend_fastembed(monkeypatch, "0.5.1")
    old = lns.embedder_id()
    _pretend_fastembed(monkeypatch, "0.6.0")
    new = lns.embedder_id()

    assert lns.EMBEDDING_MODEL_NAME in old
    assert "fastembed-0.5.1" in old
    assert old != new


@requires_sqlite_vec
async def test_unchanged_file_is_re_embedded_after_a_fastembed_upgrade(tmp_notes_dir: Path, monkeypatch):
    (tmp_notes_dir / "note.md").write_text("same text before and after the upgrade")
    _pretend_fastembed(monkeypatch, "0.5.1")
    await lns.index_directory(str(tmp_notes_dir))

    same_version = await lns.index_directory(str(tmp_notes_dir))
    assert "1 değişmemiş dosya atlandı" in same_version

    _pretend_fastembed(monkeypatch, "0.6.0")
    upgraded = await lns.index_directory(str(tmp_notes_dir))
    assert "1 dosya (yeni/değişmiş)" in upgraded

    conn = sqlite3.connect(lns.DB_PATH)
    [(recorded,)] = conn.execute("SELECT embedder FROM files").fetchall()
    conn.close()
    assert recorded.endswith("fastembed-0.6.0")


@requires_sqlite_vec
async def test_search_warns_until_the_stale_files_are_re_embedded(tmp_notes_dir: Path, monkeypatch):
    (tmp_notes_dir / "note.md").write_text("indexed under the old embedder")
    _pretend_fastembed(monkeypatch, "0.5.1")
    await lns.index_directory(str(tmp_notes_dir))
    assert "Uyarı" not in await lns.search_notes("old embedder")

    _pretend_fastembed(monkeypatch, "0.6.0")
    before = await lns.search_notes("old embedder")
    assert before.startswith("Uyarı: 1 dosya farklı bir gömme sürümüyle")
    assert "note.md" in before  # still searched, not hidden
    assert "yeniden indexlenmeli" in await lns.list_indexed_files()
    asked = await lns.ask_notes("old embedder")
    assert asked.startswith("Uyarı: 1 dosya")

    await lns.index_directory(str(tmp_notes_dir))
    assert "Uyarı" not in await lns.search_notes("old embedder")
    assert "yeniden indexlenmeli" not in await lns.list_indexed_files()


@requires_sqlite_vec
async def test_an_index_from_before_the_embedder_column_is_migrated_and_flagged(tmp_notes_dir: Path):
    # The files table as every earlier release created it: no embedder column.
    (tmp_notes_dir / "note.md").write_text("indexed by an older release")
    await lns.index_directory(str(tmp_notes_dir))
    conn = sqlite3.connect(lns.DB_PATH)
    conn.executescript(
        """
        CREATE TABLE files_old (path TEXT PRIMARY KEY, content_hash TEXT NOT NULL,
                                chunk_count INTEGER NOT NULL, indexed_at TEXT NOT NULL);
        INSERT INTO files_old SELECT path, content_hash, chunk_count, indexed_at FROM files;
        DROP TABLE files;
        ALTER TABLE files_old RENAME TO files;
        """
    )
    conn.close()

    flagged = await lns.search_notes("older release")
    assert flagged.startswith("Uyarı: 1 dosya")
    assert "gömme sürümü kayıtsız" in await lns.list_indexed_files()

    reindexed = await lns.index_directory(str(tmp_notes_dir))
    assert "1 dosya (yeni/değişmiş)" in reindexed
    assert "Uyarı" not in await lns.search_notes("older release")


async def test_top_k_bounds_are_in_the_tool_schema():
    tools = {t.name: t for t in await lns.mcp.list_tools()}
    for name in ("search_notes", "ask_notes"):
        top_k = tools[name].input_schema["properties"]["top_k"]
        assert top_k["minimum"] == 1
        assert top_k["maximum"] == lns.MAX_TOP_K
