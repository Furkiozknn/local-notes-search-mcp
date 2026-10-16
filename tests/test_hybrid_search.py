"""Hybrid retrieval: an FTS5/BM25 keyword index next to the vectors, fused
with reciprocal rank fusion.

Most tests use a deterministic fake embedder so the keyword side, the
fusion and the index bookkeeping are checked without the model; the last
ones run the real model end to end (skipped when it is unavailable)."""

from __future__ import annotations

import hashlib
import math
import sqlite3
from pathlib import Path

import pytest

import local_notes_search as lns
from tests.conftest import requires_model, requires_sqlite_vec


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


# --- pure logic ----------------------------------------------------------


def test_fts_query_quotes_every_word_so_syntax_is_inert():
    assert lns.fts_query('error E4127 AND NEAR(x) "quoted') == (
        '"error" OR "E4127" OR "AND" OR "NEAR" OR "x" OR "quoted"'
    )


def test_fts_query_keeps_unicode_words_and_folds_dotless_i():
    assert lns.fts_query("çalışma notları") == '"çalişma" OR "notlari"'


@requires_sqlite_vec
@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["calisma", "çalışma", "CALIŞMA", "Çalişma", "notlari", "NOTLARI"])
async def test_turkish_words_match_without_their_diacritics(fake_embedder, tmp_notes_dir: Path, query):
    # Measured before the fold: unicode61 remove_diacritics maps ç/ş to c/s but
    # keeps ı, so "calisma" and "CALIŞMA" missed a note saying "Çalışma".
    (tmp_notes_dir / "tr.md").write_text("Çalışma notları: toplantı perşembe.\n", encoding="utf-8")
    await lns.index_directory(str(tmp_notes_dir))
    result = await lns.search_notes(query, mode="keyword")
    assert "tr.md" in result
    assert "Çalışma notları" in result, "the snippet shows the original text, not the folded copy"


def test_fts_query_without_words_is_none():
    assert lns.fts_query("  ?!  ") is None


def test_rrf_rewards_agreement_between_rankings():
    fused = lns._rrf_fuse([1, 2, 3], [3, 4])
    assert fused[0][0] == 3, "found by both rankings beats rank 1 in only one"
    assert [cid for cid, _ in fused] == [3, 1, 2, 4]


def test_rrf_scores_and_ties_are_deterministic():
    fused = lns._rrf_fuse([10], [20])
    assert fused[0][1] == fused[1][1] == pytest.approx(1 / 61)
    assert [cid for cid, _ in fused] == [10, 20], "tie broken by vector rank first"


def test_rrf_with_one_empty_ranking_keeps_the_other_order():
    assert [cid for cid, _ in lns._rrf_fuse([], [5, 6, 7])] == [5, 6, 7]


# --- index bookkeeping (fake embedder) -------------------------------------


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_keyword_mode_finds_an_exact_code_without_the_model(fake_embedder, tmp_notes_dir: Path):
    (tmp_notes_dir / "incident.md").write_text("The deploy failed with error E4127 after the migration.\n")
    (tmp_notes_dir / "other.md").write_text("The deploy went fine and nobody was paged.\n")
    await lns.index_directory(str(tmp_notes_dir))

    result = await lns.search_notes("E4127", top_k=5, mode="keyword")
    assert "incident.md" in result and "other.md" not in result
    assert "anahtar kelime eşleşmesi" in result


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_hybrid_puts_the_keyword_match_first(fake_embedder, tmp_notes_dir: Path):
    for i in range(8):
        (tmp_notes_dir / f"deploy-{i}.md").write_text(f"Deploy notes number {i}: rollout went fine.\n")
    (tmp_notes_dir / "incident.md").write_text("Rollout aborted, error code ZX-99812 in the logs.\n")
    await lns.index_directory(str(tmp_notes_dir))

    rows, _ = lns._retrieve("ZX-99812", 3, None, "hybrid")
    assert rows[0][0].endswith("incident.md")
    assert rows[0][5] == "both", "the fake vectors still rank every chunk, so it is found by both"


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_vector_mode_is_the_old_behaviour(fake_embedder, tmp_notes_dir: Path):
    (tmp_notes_dir / "a.md").write_text("alpha beta gamma\n")
    await lns.index_directory(str(tmp_notes_dir))
    rows, _ = lns._retrieve("anything", 1, None, "vector")
    assert len(rows) == 1 and rows[0][5] == "vector" and rows[0][4] is not None


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_changed_and_deleted_files_leave_no_keyword_rows(fake_embedder, tmp_notes_dir: Path):
    note = tmp_notes_dir / "n.md"
    note.write_text("original wording kiwifruit\n")
    await lns.index_directory(str(tmp_notes_dir))
    note.write_text("replacement wording mango\n")
    await lns.index_directory(str(tmp_notes_dir))

    assert "n.md" not in await lns.search_notes("kiwifruit", mode="keyword")
    assert "n.md" in await lns.search_notes("mango", mode="keyword")

    note.unlink()
    await lns.index_directory(str(tmp_notes_dir))
    conn = lns.get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 0
    finally:
        conn.close()


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_remove_directory_clears_keyword_rows(fake_embedder, tmp_notes_dir: Path):
    (tmp_notes_dir / "a.md").write_text("pineapple notes\n")
    await lns.index_directory(str(tmp_notes_dir))
    await lns.remove_directory(str(tmp_notes_dir))
    assert lns.NO_RESULTS_MESSAGE in await lns.search_notes("pineapple", mode="keyword")


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_an_index_built_before_fts_is_backfilled(fake_embedder, tmp_notes_dir: Path, tmp_db_path: Path):
    (tmp_notes_dir / "old.md").write_text("legacy index mentions papaya\n")
    await lns.index_directory(str(tmp_notes_dir))
    # Simulate an index from the previous release: no keyword table.
    raw = sqlite3.connect(str(tmp_db_path))
    raw.execute("DROP TABLE chunks_fts")
    raw.commit()
    raw.close()

    result = await lns.search_notes("papaya", mode="keyword")
    assert "old.md" in result


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_path_prefix_filters_keyword_hits(fake_embedder, tmp_notes_dir: Path):
    (tmp_notes_dir / "work").mkdir()
    (tmp_notes_dir / "home").mkdir()
    (tmp_notes_dir / "work" / "w.md").write_text("budget spreadsheet\n")
    (tmp_notes_dir / "home" / "h.md").write_text("budget for groceries\n")
    await lns.index_directory(str(tmp_notes_dir))
    result = await lns.search_notes("budget", mode="keyword", path_prefix=str(tmp_notes_dir / "home"))
    assert "h.md" in result and "w.md" not in result


@requires_sqlite_vec
@pytest.mark.asyncio
async def test_fts_syntax_in_a_query_is_not_an_error(fake_embedder, tmp_notes_dir: Path):
    (tmp_notes_dir / "a.md").write_text("plain text\n")
    await lns.index_directory(str(tmp_notes_dir))
    for query in ('"unbalanced', "a AND OR NOT", "col:value", "x*", "NEAR(a b)"):
        result = await lns.search_notes(query, mode="keyword")
        assert not result.startswith("Hata"), (query, result)


def test_unknown_mode_is_rejected():
    assert lns._retrieve("q", 3, None, "fuzzy").startswith("Hata: mode")


# --- real model ------------------------------------------------------------


@requires_model
@pytest.mark.asyncio
async def test_hybrid_puts_an_exact_identifier_first_with_the_real_model(tmp_notes_dir: Path):
    for i, topic in enumerate(["database migration plan", "release checklist", "incident review",
                               "on-call handbook", "deploy pipeline", "rollback procedure"]):
        (tmp_notes_dir / f"note-{i}.md").write_text(f"# {topic}\n\nGeneral notes about the {topic}.\n")
    (tmp_notes_dir / "ticket.md").write_text("# Misc\n\nRemember to close JIRA-48213 before Friday.\n")
    await lns.index_directory(str(tmp_notes_dir))

    hybrid, _ = lns._retrieve("JIRA-48213", 1, None, "hybrid")
    assert hybrid[0][0].endswith("ticket.md")


@requires_model
@pytest.mark.asyncio
async def test_hybrid_still_answers_paraphrased_questions(tmp_notes_dir: Path):
    (tmp_notes_dir / "cooking.md").write_text(
        "# Pasta Recipe\n\nBoil water, add salt, cook pasta for 9 minutes, drain and serve with sauce.\n"
    )
    (tmp_notes_dir / "unrelated.md").write_text(
        "# Car Maintenance\n\nCheck tire pressure monthly and rotate tires every 10000 km.\n"
    )
    await lns.index_directory(str(tmp_notes_dir))
    result = await lns.search_notes("how do I cook pasta", top_k=1)
    assert "cooking.md" in result
