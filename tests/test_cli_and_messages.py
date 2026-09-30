"""`--help` / `--version`, the download step's noise, and the wording of the
first-run error paths. The examples/notes fixtures back the README's demo, so
one model-backed test pins the two answers it shows."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import local_notes_search as lns
from tests.conftest import requires_model, requires_sqlite_vec

NOTES = Path(__file__).resolve().parent.parent / "examples" / "notes"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(lns, "DB_PATH", tmp_path / "index.db")
    monkeypatch.delenv(lns.ALLOWED_ROOTS_ENV, raising=False)


def test_help_names_every_tool_and_environment_variable(capsys):
    with pytest.raises(SystemExit) as exit_info:
        lns.main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for name in ("index_directory", "search_notes", "ask_notes", "list_indexed_files", "remove_directory"):
        assert name in out
    for env in (lns.DB_ENV, lns.MODEL_DIR_ENV, lns.OFFLINE_ENV, lns.ALLOWED_ROOTS_ENV, "GROQ_API_KEY", "MISTRAL_API_KEY"):
        assert env in out
    assert "--download-model" in out and "--version" in out


def test_version_prints_a_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        lns.main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("local-notes-search-mcp ")


def test_unknown_option_is_a_usage_error():
    with pytest.raises(SystemExit) as exit_info:
        lns.main(["--bogus"])
    assert exit_info.value.code == 2


def test_download_model_is_quiet_about_http_and_says_what_it_fetches(monkeypatch, capsys):
    monkeypatch.delenv(lns.OFFLINE_ENV, raising=False)
    monkeypatch.setattr(lns, "_get_model", lambda: object())
    logging.getLogger("httpx").setLevel(logging.INFO)
    lns.main(["--download-model"])
    captured = capsys.readouterr()
    assert logging.getLogger("httpx").level == logging.WARNING
    assert "0.22 GB" in captured.err and lns.EMBEDDING_MODEL_NAME in captured.err
    assert "is cached in" in captured.out


def test_index_directory_tells_a_missing_path_from_a_file(tmp_path: Path):
    missing = lns._index_directory_sync(str(tmp_path / "nope"), None)
    assert missing.startswith("Hata:") and "bulunamadı" in missing

    a_file = tmp_path / "todo.md"
    a_file.write_text("x")
    file_msg = lns._index_directory_sync(str(a_file), None)
    assert file_msg.startswith("Hata:") and "bir dosya" in file_msg


@requires_sqlite_vec
def test_an_empty_directory_is_not_reported_as_plain_success(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(lns, "embed_texts", lambda texts: [[0.0] * lns._embedding_dim() for _ in texts])
    (tmp_path / "photo.png").write_bytes(b"\x89PNG")
    msg = lns._index_directory_sync(str(tmp_path), None)
    assert "0 dosya (yeni/değişmiş)" in msg  # the documented counts are still there
    assert "indexlenecek dosya bulunamadı" in msg and "extensions=" in msg


def test_model_load_error_has_no_doubled_full_stop(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(lns, "_model", None)
    monkeypatch.setenv(lns.MODEL_DIR_ENV, str(tmp_path / "empty-cache"))
    monkeypatch.setenv(lns.OFFLINE_ENV, "1")
    with pytest.raises(ToolError) as excinfo:
        lns._get_model()
    assert ".." not in str(excinfo.value)


@requires_model
@requires_sqlite_vec
@pytest.mark.asyncio
async def test_readme_demo_notes_answer_the_two_demo_queries():
    assert NOTES.is_dir()
    await lns.index_directory(str(NOTES))
    auth = await lns.search_notes("what did I decide about the auth redesign?", top_k=1)
    pasta = await lns.search_notes("how do I cook pasta", top_k=1)
    assert "2026-08-decisions.md" in auth
    assert "recipes-pasta.md" in pasta
