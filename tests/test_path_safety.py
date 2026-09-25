"""What index_directory is allowed to read, what the index file exposes, and
how big a tool answer can get.

These run the real walk -> index_file -> sqlite-vec path with a stand-in
embedder (a hash of the text spread over the model's dimension), so they
need the sqlite-vec extension but NOT the fastembed model download: the
behaviour under test is which files get in and what happens to the rows,
not how good the vectors are."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

import local_notes_search as lns
from tests.conftest import requires_sqlite_vec

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions / symlinks")


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


async def _indexed_paths() -> str:
    return await lns.list_indexed_files()


# --- symlinks -------------------------------------------------------------

@posix_only
@requires_sqlite_vec
async def test_symlink_to_a_file_outside_the_root_is_not_indexed(tmp_path: Path):
    root = tmp_path / "notes"
    outside = tmp_path / "private"
    root.mkdir()
    outside.mkdir()
    (outside / "diary.md").write_text("outside the root")
    (root / "todo.md").symlink_to(outside / "diary.md")
    (root / "real.md").write_text("inside the root")

    await lns.index_directory(str(root))

    listing = await _indexed_paths()
    assert "real.md" in listing
    assert "todo.md" not in listing


@posix_only
@requires_sqlite_vec
async def test_symlink_escaping_an_allowed_root_is_not_indexed(tmp_path: Path, monkeypatch):
    root = tmp_path / "allowed"
    root.mkdir()
    secret_dir = tmp_path / "elsewhere"
    secret_dir.mkdir()
    (secret_dir / "keys.txt").write_text("not for the index")
    (root / "notes.txt").symlink_to(secret_dir / "keys.txt")
    monkeypatch.setenv(lns.ALLOWED_ROOTS_ENV, str(root))

    await lns.index_directory(str(root))

    assert "Index boş." == await _indexed_paths()


@posix_only
def test_symlink_renaming_a_secret_is_not_indexed(tmp_path: Path):
    # The link's own name is innocent; the file it points at is .env.
    (tmp_path / ".env").write_text("GROQ_API_KEY=secret")
    (tmp_path / "config.txt").symlink_to(tmp_path / ".env")

    found = {p.name for p in lns.walk_indexable_files(tmp_path, lns.DEFAULT_EXTENSIONS)}
    assert found == set()


@posix_only
def test_symlink_inside_the_root_is_still_followed(tmp_path: Path):
    (tmp_path / "a.md").write_text("target")
    (tmp_path / "alias.md").symlink_to(tmp_path / "a.md")

    found = {p.name for p in lns.walk_indexable_files(tmp_path, lns.DEFAULT_EXTENSIONS)}
    assert found == {"a.md", "alias.md"}


@posix_only
def test_dangling_and_looping_symlinks_are_skipped(tmp_path: Path):
    (tmp_path / "gone.md").symlink_to(tmp_path / "missing.md")
    (tmp_path / "loop.md").symlink_to(tmp_path / "loop.md")

    assert list(lns.walk_indexable_files(tmp_path, lns.DEFAULT_EXTENSIONS)) == []


# --- hidden directories and more credential names -------------------------

def test_hidden_directories_are_skipped(tmp_path: Path):
    for hidden in (".ssh", ".aws", ".config/gh", ".docker"):
        d = tmp_path / hidden
        d.mkdir(parents=True)
        (d / "config.json").write_text('{"auth": "token"}')
    (tmp_path / "visible.md").write_text("keep")

    found = {p.relative_to(tmp_path).as_posix() for p in lns.walk_indexable_files(tmp_path, lns.DEFAULT_EXTENSIONS)}
    assert found == {"visible.md"}


def test_a_hidden_directory_given_as_the_root_is_indexed(tmp_path: Path):
    root = tmp_path / ".notes"
    root.mkdir()
    (root / "a.md").write_text("explicitly asked for")

    found = {p.name for p in lns.walk_indexable_files(root, lns.DEFAULT_EXTENSIONS)}
    assert found == {"a.md"}


@pytest.mark.parametrize(
    "name", [".npmrc", ".pypirc", ".git-credentials", "secrets.json", "server.key", "cert.p12",
             "cert.pfx", "putty.ppk", "service-account-prod.json"],
)
def test_more_credential_names_are_never_indexable(name):
    assert lns.is_secret_filename(name) is True


# --- root under a directory whose name is on the skip list ----------------

def test_root_below_a_directory_named_build_is_still_indexed(tmp_path: Path):
    # The skip list used to be matched against every part of the ABSOLUTE
    # path, so ".../build/notes" (or ".../dist/...") indexed nothing.
    root = tmp_path / "build" / "notes"
    root.mkdir(parents=True)
    (root / "a.md").write_text("hello")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "b.md").write_text("still skipped")

    found = {p.name for p in lns.walk_indexable_files(root, lns.DEFAULT_EXTENSIONS)}
    assert found == {"a.md"}


# --- binary / no-longer-readable files -----------------------------------

@requires_sqlite_vec
async def test_binary_file_with_a_text_extension_is_not_indexed(tmp_notes_dir: Path):
    (tmp_notes_dir / "dump.json").write_bytes(b'{"a": 1}\x00\x00\x00binary')
    (tmp_notes_dir / "ok.md").write_text("text")

    summary = await lns.index_directory(str(tmp_notes_dir))

    assert "1 dosya okunamadı" in summary
    listing = await _indexed_paths()
    assert "ok.md" in listing
    assert "dump.json" not in listing


@requires_sqlite_vec
async def test_file_that_stops_being_text_drops_out_of_the_index(tmp_notes_dir: Path):
    note = tmp_notes_dir / "note.md"
    note.write_text("readable at first")
    await lns.index_directory(str(tmp_notes_dir))
    assert "note.md" in await _indexed_paths()

    note.write_bytes(b"\xff\xfe not utf-8 any more")
    summary = await lns.index_directory(str(tmp_notes_dir))

    assert "1 silinmiş dosya temizlendi" in summary
    assert "Index boş." == await _indexed_paths()


# --- index file permissions -----------------------------------------------

@posix_only
@requires_sqlite_vec
def test_index_file_is_owner_only(tmp_path: Path):
    db = tmp_path / "idx" / "index.db"
    lns.get_connection(db).close()
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


@posix_only
@requires_sqlite_vec
def test_default_index_directory_is_owner_only(tmp_path: Path, monkeypatch):
    default = tmp_path / "home" / ".local-notes-search" / "index.db"
    monkeypatch.setattr(lns, "DEFAULT_DB_PATH", default)
    default.parent.mkdir(parents=True, mode=0o755)
    os.chmod(default.parent, 0o755)  # an index directory created by an older version

    lns.get_connection(default).close()

    assert stat.S_IMODE(default.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(default.stat().st_mode) == 0o600


# --- result size caps -----------------------------------------------------

async def test_top_k_above_the_cap_is_refused_with_the_limit():
    result = await lns.search_notes("anything", top_k=lns.MAX_TOP_K + 1)
    assert "Hata" in result
    assert str(lns.MAX_TOP_K) in result


@requires_sqlite_vec
async def test_list_indexed_files_is_capped(tmp_notes_dir: Path, monkeypatch):
    monkeypatch.setattr(lns, "MAX_LISTED_FILES", 3)
    for i in range(5):
        (tmp_notes_dir / f"n{i}.md").write_text(f"note {i}")
    await lns.index_directory(str(tmp_notes_dir))

    listing = await _indexed_paths()

    assert listing.startswith("5 dosya indexlenmiş:")
    assert listing.count(" chunk, ") == 3
    assert "2 dosya daha" in listing


# --- model cache and offline mode -----------------------------------------

def test_model_cache_defaults_to_a_persistent_per_user_directory(monkeypatch):
    monkeypatch.delenv(lns.MODEL_DIR_ENV, raising=False)
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    assert lns.model_dir() == Path.home() / ".local-notes-search" / "models"


def test_model_cache_env_overrides(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "fe"))
    monkeypatch.delenv(lns.MODEL_DIR_ENV, raising=False)
    assert lns.model_dir() == tmp_path / "fe"
    monkeypatch.setenv(lns.MODEL_DIR_ENV, str(tmp_path / "ours"))
    assert lns.model_dir() == tmp_path / "ours"


def test_offline_mode_with_an_empty_cache_fails_with_instructions(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(lns, "_model", None)
    monkeypatch.setenv(lns.MODEL_DIR_ENV, str(tmp_path / "empty-cache"))
    monkeypatch.setenv(lns.OFFLINE_ENV, "1")

    with pytest.raises(ToolError) as excinfo:
        lns._get_model()

    message = str(excinfo.value)
    assert lns.OFFLINE_ENV in message
    assert "--download-model" in message


def test_download_model_refuses_in_offline_mode(monkeypatch):
    monkeypatch.setenv(lns.OFFLINE_ENV, "true")
    with pytest.raises(SystemExit, match=lns.OFFLINE_ENV):
        lns.main(["--download-model"])
