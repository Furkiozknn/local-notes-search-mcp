"""End-to-end index -> search round trip against the real fastembed model
and a real sqlite-vec database file. Requires both to actually be
importable/loadable - see conftest.py's `requires_model` skip marker."""

from __future__ import annotations

from pathlib import Path

import pytest

import local_notes_search as lns
from tests.conftest import requires_model, requires_sqlite_vec


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_db_path: Path):
    monkeypatch.setattr(lns, "DB_PATH", tmp_db_path)


@requires_model
@pytest.mark.asyncio
async def test_index_then_search_finds_relevant_chunk(tmp_notes_dir: Path):
    (tmp_notes_dir / "cooking.md").write_text(
        "# Pasta Recipe\n\nBoil water, add salt, cook pasta for 9 minutes, drain and serve with sauce.\n"
    )
    (tmp_notes_dir / "unrelated.md").write_text(
        "# Car Maintenance\n\nCheck tire pressure monthly and rotate tires every 10000 km.\n"
    )

    await lns.index_directory(str(tmp_notes_dir))

    result = await lns.search_notes("how do I cook pasta", top_k=1)
    assert "cooking.md" in result
    assert "unrelated.md" not in result


@requires_model
@pytest.mark.asyncio
async def test_reindexing_unchanged_file_is_a_no_op(tmp_notes_dir: Path):
    note = tmp_notes_dir / "stable.md"
    note.write_text("This content never changes between the two index calls.")

    first = await lns.index_directory(str(tmp_notes_dir))
    second = await lns.index_directory(str(tmp_notes_dir))

    assert "1 dosya (yeni/değişmiş)" in first
    assert "0 dosya (yeni/değişmiş)" in second
    assert "1 değişmemiş dosya atlandı" in second


@requires_model
@pytest.mark.asyncio
async def test_changed_file_is_reindexed(tmp_notes_dir: Path):
    note = tmp_notes_dir / "evolving.md"
    note.write_text("Original content about gardening and tomatoes.")
    await lns.index_directory(str(tmp_notes_dir))

    note.write_text("Completely different content about quantum computing.")
    summary = await lns.index_directory(str(tmp_notes_dir))
    assert "1 dosya (yeni/değişmiş)" in summary

    result = await lns.search_notes("quantum computing", top_k=1)
    assert "evolving.md" in result


@requires_model
@pytest.mark.asyncio
async def test_deleted_file_is_removed_from_index(tmp_notes_dir: Path):
    note = tmp_notes_dir / "temporary.md"
    note.write_text("Some temporary content.")
    await lns.index_directory(str(tmp_notes_dir))

    note.unlink()
    summary = await lns.index_directory(str(tmp_notes_dir))
    assert "1 silinmiş dosya temizlendi" in summary

    listing = await lns.list_indexed_files()
    assert "temporary.md" not in listing


@requires_model
@pytest.mark.asyncio
async def test_remove_directory_clears_index(tmp_notes_dir: Path):
    (tmp_notes_dir / "a.md").write_text("content a")
    await lns.index_directory(str(tmp_notes_dir))

    result = await lns.remove_directory(str(tmp_notes_dir))
    assert "1 dosya index'ten kaldırıldı" in result

    listing = await lns.list_indexed_files()
    assert "Index boş." == listing


@requires_model
@pytest.mark.asyncio
async def test_search_with_no_index_returns_friendly_message():
    result = await lns.search_notes("anything")
    assert "Sonuç bulunamadı" in result


@pytest.mark.asyncio
async def test_search_rejects_empty_query():
    result = await lns.search_notes("   ")
    assert "boş sorgu" in result.lower()


@pytest.mark.asyncio
async def test_search_rejects_non_positive_top_k():
    result = await lns.search_notes("anything", top_k=0)
    assert "top_k" in result
    result = await lns.search_notes("anything", top_k=-1)
    assert "top_k" in result


@requires_model
@pytest.mark.asyncio
async def test_remove_directory_does_not_touch_sibling_directory_with_shared_prefix(tmp_notes_dir: Path):
    # ".../app" and ".../app-backup" share a string prefix but are different
    # directories - a naive LIKE/startswith prefix match would wrongly treat
    # files in app-backup as "under" app. Regression test for that bug.
    app_dir = tmp_notes_dir / "app"
    app_backup_dir = tmp_notes_dir / "app-backup"
    app_dir.mkdir()
    app_backup_dir.mkdir()
    (app_dir / "a.md").write_text("content in app")
    (app_backup_dir / "b.md").write_text("content in app-backup")

    await lns.index_directory(str(app_dir))
    await lns.index_directory(str(app_backup_dir))

    result = await lns.remove_directory(str(app_dir))
    assert "1 dosya index'ten kaldırıldı" in result

    listing = await lns.list_indexed_files()
    assert "app-backup" in listing
    assert str(app_dir / "a.md") not in listing


@requires_model
@pytest.mark.asyncio
async def test_reindexing_does_not_delete_sibling_directory_with_shared_prefix(tmp_notes_dir: Path):
    app_dir = tmp_notes_dir / "app"
    app_backup_dir = tmp_notes_dir / "app-backup"
    app_dir.mkdir()
    app_backup_dir.mkdir()
    (app_dir / "a.md").write_text("content in app")
    (app_backup_dir / "b.md").write_text("content in app-backup")

    await lns.index_directory(str(app_dir))
    await lns.index_directory(str(app_backup_dir))

    # Re-indexing app (unchanged) must not report app-backup's file as stale.
    summary = await lns.index_directory(str(app_dir))
    assert "0 silinmiş dosya temizlendi" in summary

    listing = await lns.list_indexed_files()
    assert "app-backup" in listing


@requires_model
@pytest.mark.asyncio
async def test_index_directory_is_case_insensitive_for_custom_extensions(tmp_notes_dir: Path):
    (tmp_notes_dir / "a.md").write_text("hello world")
    summary = await lns.index_directory(str(tmp_notes_dir), extensions=["MD"])
    assert "1 dosya (yeni/değişmiş)" in summary


@pytest.mark.asyncio
async def test_index_directory_rejects_nonexistent_path():
    result = await lns.index_directory("Z:/this/path/does/not/exist")
    assert "Hata" in result


@requires_sqlite_vec
def test_mismatched_embedding_model_raises(tmp_db_path: Path, monkeypatch):
    # Build an index under one "model name", then reopen it pretending the
    # code was built with a different model - must refuse, not silently
    # return wrong-dimension similarity scores (see module docstring).
    conn = lns.get_connection(tmp_db_path)
    conn.close()

    monkeypatch.setattr(lns, "EMBEDDING_MODEL_NAME", "some/other-model")
    with pytest.raises(RuntimeError, match="embedding model"):
        lns.get_connection(tmp_db_path)


def test_unsupported_model_override_fails_with_the_supported_list(monkeypatch):
    monkeypatch.setattr(lns, "EMBEDDING_MODEL_NAME", "nonexistent/model")
    with pytest.raises(RuntimeError) as excinfo:
        lns._embedding_dim()
    assert "not a model this fastembed build supports" in str(excinfo.value)
    assert "paraphrase-multilingual-MiniLM-L12-v2" in str(excinfo.value)
