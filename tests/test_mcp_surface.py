"""What an MCP client sees: tool annotations, output schemas, and the
structuredContent that travels next to the unchanged text."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

import local_notes_search as lns
from tests.conftest import requires_sqlite_vec


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_db_path: Path):
    monkeypatch.setattr(lns, "DB_PATH", tmp_db_path)
    monkeypatch.delenv(lns.ALLOWED_ROOTS_ENV, raising=False)


def _fake_vector(text: str, dim: int = 384) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [((digest[i % len(digest)] + i * 7) % 251) / 251.0 - 0.5 for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


@pytest.fixture
def fake_embedder(monkeypatch):
    monkeypatch.setattr(lns, "embed_texts", lambda texts: [_fake_vector(t) for t in texts])
    monkeypatch.setattr(lns, "_embedding_dim", lambda: 384)


async def _tools():
    return {t.name: t for t in await lns.mcp.list_tools()}


@pytest.mark.asyncio
async def test_the_five_tools_are_still_served_under_their_names():
    assert set(await _tools()) == {
        "index_directory", "search_notes", "ask_notes", "list_indexed_files", "remove_directory",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("name,read_only,destructive,idempotent,open_world", [
    ("index_directory", False, False, True, False),
    ("search_notes", True, None, True, False),
    ("list_indexed_files", True, None, True, False),
    ("ask_notes", True, None, False, True),
    ("remove_directory", False, True, True, False),
])
async def test_annotations_say_what_each_tool_does(name, read_only, destructive, idempotent, open_world):
    ann = (await _tools())[name].annotations
    assert ann.read_only_hint is read_only
    assert ann.destructive_hint is destructive
    assert ann.idempotent_hint is idempotent
    assert ann.open_world_hint is open_world


@pytest.mark.asyncio
async def test_every_tool_has_a_title():
    assert all(t.title for t in (await _tools()).values())


@pytest.mark.asyncio
async def test_search_and_list_publish_an_output_schema():
    tools = await _tools()
    search = tools["search_notes"].output_schema
    assert set(search["properties"]) >= {"query", "mode", "count", "results", "notice"}
    listing = tools["list_indexed_files"].output_schema
    assert set(listing["properties"]) >= {"count", "shown", "files"}


@pytest.mark.asyncio
async def test_search_input_schema_lists_the_modes():
    schema = (await _tools())["search_notes"].input_schema
    assert schema["properties"]["mode"]["enum"] == ["hybrid", "vector", "keyword"]
    assert schema["properties"]["top_k"]["maximum"] == lns.MAX_TOP_K


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_search_returns_the_old_text_and_the_same_facts_structured(fake_embedder, tmp_notes_dir: Path):
    (tmp_notes_dir / "a.md").write_text("line one\nerror E4127 in line two\n")
    await lns.index_directory(str(tmp_notes_dir))

    result = await lns.mcp.call_tool("search_notes", {"query": "E4127", "mode": "keyword"})
    assert not result.is_error
    text = result.content[0].text
    assert text == await lns.search_notes("E4127", mode="keyword"), "text view unchanged"
    data = result.structured_content
    assert data["count"] == 1 and data["mode"] == "keyword"
    hit = data["results"][0]
    assert hit["path"].endswith("a.md") and hit["start_line"] == 1 and hit["end_line"] == 2
    assert hit["match"] == "keyword" and hit["distance"] is None
    assert f"{hit['path']}:{hit['start_line']}-{hit['end_line']}" in text


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_no_results_is_not_an_error(fake_embedder, tmp_notes_dir: Path):
    result = await lns.mcp.call_tool("search_notes", {"query": "nothing", "mode": "keyword"})
    assert not result.is_error
    assert result.structured_content["count"] == 0
    assert result.content[0].text == lns.NO_RESULTS_MESSAGE


@pytest.mark.asyncio
async def test_an_invalid_query_is_an_error_result_without_structured_content():
    result = await lns.mcp.call_tool("search_notes", {"query": "   "})
    assert result.is_error
    assert result.content[0].text == "Hata: boş sorgu."
    assert result.structured_content is None


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_list_indexed_files_structured(fake_embedder, tmp_notes_dir: Path):
    empty = await lns.mcp.call_tool("list_indexed_files", {})
    assert empty.structured_content == {"count": 0, "shown": 0, "files": []}

    (tmp_notes_dir / "a.md").write_text("alpha\n")
    (tmp_notes_dir / "b.md").write_text("beta\n")
    await lns.index_directory(str(tmp_notes_dir))
    result = await lns.mcp.call_tool("list_indexed_files", {})
    data = result.structured_content
    assert data["count"] == data["shown"] == 2
    assert [Path(f["path"]).name for f in data["files"]] == ["a.md", "b.md"]
    assert all(f["chunk_count"] == 1 and f["needs_reindex"] is False for f in data["files"])
    assert result.content[0].text == await lns.list_indexed_files()
