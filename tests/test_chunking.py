"""Pure-function tests - no model, no DB, no filesystem beyond tmp_path."""

from __future__ import annotations

from pathlib import Path

from local_notes_search import (
    DEFAULT_EXTENSIONS,
    chunk_text,
    content_hash,
    should_index_file,
    walk_indexable_files,
)


def test_empty_text_produces_no_chunks():
    assert chunk_text("") == []


def test_short_text_is_a_single_chunk():
    text = "line one\nline two\nline three"
    chunks = chunk_text(text, chunk_chars=1000)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 3


def test_long_text_splits_into_multiple_chunks():
    lines = [f"line {i}: " + "x" * 50 for i in range(100)]
    text = "\n".join(lines)
    chunks = chunk_text(text, chunk_chars=500, overlap_chars=50)
    assert len(chunks) > 1


def test_chunks_never_split_a_line_in_half():
    lines = [f"line {i}" for i in range(50)]
    text = "\n".join(lines)
    chunks = chunk_text(text, chunk_chars=100, overlap_chars=20)
    for chunk in chunks:
        for line in chunk.text.split("\n"):
            assert line in lines


def test_consecutive_chunks_overlap():
    lines = [f"line{i:03d}" for i in range(60)]
    text = "\n".join(lines)
    chunks = chunk_text(text, chunk_chars=200, overlap_chars=80)
    assert len(chunks) >= 2
    # each chunk after the first should start at or before the previous chunk's end
    for prev, curr in zip(chunks, chunks[1:]):
        assert curr.start_line <= prev.end_line


def test_chunking_always_makes_forward_progress():
    # a pathological single very long line must not create an infinite loop
    text = "x" * 10_000
    chunks = chunk_text(text, chunk_chars=500, overlap_chars=400)
    assert len(chunks) == 1  # can't split a single line, so it's one (oversized) chunk - no crash, no loop


def test_content_hash_is_deterministic():
    assert content_hash("hello") == content_hash("hello")


def test_content_hash_differs_for_different_content():
    assert content_hash("hello") != content_hash("hello!")


def test_should_index_file_respects_extension_allowlist(tmp_path: Path):
    md_file = tmp_path / "notes.md"
    md_file.write_text("hello")
    exe_file = tmp_path / "binary.exe"
    exe_file.write_bytes(b"\x00\x01")

    assert should_index_file(md_file, DEFAULT_EXTENSIONS) is True
    assert should_index_file(exe_file, DEFAULT_EXTENSIONS) is False


def test_should_index_file_rejects_oversized_files(tmp_path: Path):
    big_file = tmp_path / "huge.md"
    big_file.write_bytes(b"x" * (3 * 1024 * 1024))
    assert should_index_file(big_file, DEFAULT_EXTENSIONS) is False


def test_walk_indexable_files_skips_noise_directories(tmp_path: Path):
    (tmp_path / "real.md").write_text("keep me")
    noise_dir = tmp_path / "node_modules"
    noise_dir.mkdir()
    (noise_dir / "should_skip.md").write_text("skip me")

    found = {p.name for p in walk_indexable_files(tmp_path, DEFAULT_EXTENSIONS)}
    assert found == {"real.md"}


def test_walk_indexable_files_recurses_into_real_subdirectories(tmp_path: Path):
    sub = tmp_path / "sub" / "deeper"
    sub.mkdir(parents=True)
    (sub / "nested.md").write_text("hi")

    found = list(walk_indexable_files(tmp_path, DEFAULT_EXTENSIONS))
    assert any(p.name == "nested.md" for p in found)
