"""local-notes-search-mcp: semantic search over your own local files, as an
MCP server.

Zero servers, zero API keys, zero cloud calls - everything stays on disk.
Two design choices exist specifically to make that true:

1. **sqlite-vec** (Apache-2.0) for vector storage: a `vec0` virtual table
   living inside one ordinary `.sqlite` file, no daemon/Docker/hosted
   service. Matches this ecosystem's existing preference for embeddable,
   zero-infra local-file stores over anything requiring a running server
   (Qdrant/pgvector were considered and rejected for exactly that reason).

2. **fastembed** (Apache-2.0, Qdrant's ONNX-runtime embedding library) for
   the embedding model, NOT `sentence-transformers`. nvidia-nim-mcp's own
   `create_embedding` tool already wraps sentence-transformers as a *rarely-
   hit fallback* (fine there - most calls never reach it, and it's optional).
   Here local embedding is the ONLY path, hit on every single index/search
   call, so the ~1GB torch dependency that's an acceptable fallback cost
   elsewhere would be a mandatory cost here. fastembed's quantized ONNX
   models run in the ~100-150MB range with no torch requirement - a
   deliberate divergence from the sibling tool's pattern, not an oversight.

sqlite-vec's exact Python binding surface (`sqlite_vec.load()`,
`sqlite_vec.serialize_float32()`) and fastembed's `TextEmbedding` API were
used per their published documentation but were not live-exercised against a
real installed package while writing this (see README "Known limitations")
- the same "verify before fully trusting a name from memory" discipline
nvidia-nim-mcp and voice-io-mcp both document for their own provider names.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mcp.server import MCPServer

logger = logging.getLogger(__name__)

mcp = MCPServer("local-notes-search")

# --- configuration -----------------------------------------------------

DEFAULT_DB_PATH = Path.home() / ".local-notes-search" / "index.db"
DB_PATH = Path(os.environ.get("LOCAL_NOTES_SEARCH_DB", str(DEFAULT_DB_PATH)))

EMBEDDING_MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

DEFAULT_EXTENSIONS = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".rst", ".toml"}
SKIP_DIR_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache", ".next", "egg-info"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # skip anything bigger - pathological chunk counts, probably not a "note"

CHUNK_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200


# --- chunking (pure logic, no model/DB needed - see tests/test_chunking.py) --

@dataclass(frozen=True)
class Chunk:
    text: str
    start_line: int  # 1-indexed, inclusive
    end_line: int  # 1-indexed, inclusive


def chunk_text(text: str, *, chunk_chars: int = CHUNK_CHARS, overlap_chars: int = CHUNK_OVERLAP_CHARS) -> list[Chunk]:
    """Line-based recursive-ish splitting: accumulate whole lines until the
    char budget is hit (never splits a line in half, which keeps chunks
    readable and line-number references exact), then step back roughly
    `overlap_chars` worth of lines so a sentence/function split across the
    boundary isn't orphaned in a single chunk. Deliberately no NLP/AST
    dependency - see README "Neden bu mimari?" for why that's a conscious
    scope choice, not a missing feature."""
    lines = text.splitlines()
    n = len(lines)
    if n == 0:
        return []

    chunks: list[Chunk] = []
    start_idx = 0
    while start_idx < n:
        char_count = 0
        end_idx = start_idx
        while end_idx < n and (end_idx == start_idx or char_count < chunk_chars):
            char_count += len(lines[end_idx]) + 1
            end_idx += 1

        chunk_str = "\n".join(lines[start_idx:end_idx])
        if chunk_str.strip():
            chunks.append(Chunk(text=chunk_str, start_line=start_idx + 1, end_line=end_idx))

        if end_idx >= n:
            break

        back_idx, back_chars = end_idx, 0
        while back_idx > start_idx + 1 and back_chars < overlap_chars:
            back_idx -= 1
            back_chars += len(lines[back_idx]) + 1
        start_idx = back_idx

    return chunks


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def should_index_file(path: Path, extensions: set[str]) -> bool:
    if path.suffix.lower() not in extensions:
        return False
    if any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
        return False
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
    except OSError:
        return False
    return True


def walk_indexable_files(root: Path, extensions: set[str]):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.endswith(".egg-info")]
        for name in filenames:
            path = Path(dirpath) / name
            if should_index_file(path, extensions):
                yield path


def is_under(file_path: str, dir_path: str) -> bool:
    """Directory-boundary-safe prefix check - a plain str.startswith/LIKE
    prefix match would also treat ".../app-backup/x.md" as being "under"
    ".../app", which is wrong and (for the delete call sites) destructive."""
    try:
        return Path(file_path).is_relative_to(Path(dir_path))
    except ValueError:
        return False


# --- embedding (fastembed, lazy-loaded singleton) ----------------------

_model_lock = threading.Lock()
_model = None  # type: ignore[var-annotated]


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:  # re-check inside the lock - two concurrent callers otherwise both load
                from fastembed import TextEmbedding

                _model = TextEmbedding(model_name=EMBEDDING_MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Batched embedding - fastembed's .embed() accepts a list directly,
    notably cheaper than one call per chunk when indexing a whole file.
    Query and document text use the same call (no BGE asymmetric
    query-instruction prefix) - a deliberate v1 simplification, see README."""
    if not texts:
        return []
    model = _get_model()
    return [vec.tolist() for vec in model.embed(texts)]


# --- storage (sqlite-vec) -----------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    indexed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chunks_file_path ON chunks(file_path);
"""


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    import sqlite_vec

    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.executescript(_SCHEMA)

    existing_model = conn.execute("SELECT value FROM meta WHERE key = 'embedding_model'").fetchone()
    if existing_model is None:
        conn.execute("INSERT INTO meta (key, value) VALUES ('embedding_model', ?)", (EMBEDDING_MODEL_NAME,))
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0(embedding float[{EMBEDDING_DIM}])")
        conn.commit()
    elif existing_model[0] != EMBEDDING_MODEL_NAME:
        # A different embedding model's vectors are not comparable to this
        # model's query vectors - refuse rather than silently returning
        # garbage similarity scores (see README "pitfalls"). Close before
        # raising - callers do `conn = get_connection()` then
        # `try/finally: conn.close()`, which never runs if this assignment
        # itself never completes, otherwise leaving the file locked on
        # Windows until GC.
        mismatched_model = existing_model[0]
        conn.close()
        raise RuntimeError(
            f"Index at {path} was built with embedding model {mismatched_model!r}, "
            f"but this build uses {EMBEDDING_MODEL_NAME!r}. Delete the index file "
            f"or set LOCAL_NOTES_SEARCH_DB to a fresh path, then re-index."
        )
    return conn


def _delete_file_rows(conn: sqlite3.Connection, file_path: str) -> None:
    ids = [row[0] for row in conn.execute("SELECT id FROM chunks WHERE file_path = ?", (file_path,)).fetchall()]
    for chunk_id in ids:
        conn.execute("DELETE FROM chunk_vectors WHERE rowid = ?", (chunk_id,))
    conn.execute("DELETE FROM chunks WHERE file_path = ?", (file_path,))
    conn.execute("DELETE FROM files WHERE path = ?", (file_path,))


def index_file(conn: sqlite3.Connection, path: Path) -> int:
    """Indexes one file, skipping it entirely if its whole-file content hash
    matches what's already stored (the cheap re-embedding-avoidance check
    from README "pitfalls" - re-embedding unchanged files burns nothing here
    since embedding is local/free, but it's still wasted CPU on every
    index_directory call otherwise). Returns the number of chunks written
    (0 if skipped as unchanged)."""
    import sqlite_vec

    text = path.read_text(encoding="utf-8", errors="strict")
    file_hash = content_hash(text)
    file_key = str(path)

    existing = conn.execute("SELECT content_hash FROM files WHERE path = ?", (file_key,)).fetchone()
    if existing is not None and existing[0] == file_hash:
        return 0

    _delete_file_rows(conn, file_key)

    chunks = chunk_text(text)
    if not chunks:
        conn.execute(
            "INSERT INTO files (path, content_hash, chunk_count, indexed_at) VALUES (?, ?, 0, ?)",
            (file_key, file_hash, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        return 0

    vectors = embed_texts([c.text for c in chunks])
    for chunk, vector in zip(chunks, vectors):
        cursor = conn.execute(
            "INSERT INTO chunks (file_path, start_line, end_line, text) VALUES (?, ?, ?, ?)",
            (file_key, chunk.start_line, chunk.end_line, chunk.text),
        )
        conn.execute(
            "INSERT INTO chunk_vectors (rowid, embedding) VALUES (?, ?)",
            (cursor.lastrowid, sqlite_vec.serialize_float32(vector)),
        )

    conn.execute(
        "INSERT INTO files (path, content_hash, chunk_count, indexed_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, "
        "chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at",
        (file_key, file_hash, len(chunks), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return len(chunks)


# --- MCP tools -----------------------------------------------------------
# Each tool is a thin async wrapper around a synchronous `_*_sync` function,
# offloaded via asyncio.to_thread - file I/O, ONNX embedding (including a
# possible first-run model download), and sqlite I/O are all blocking calls
# that would otherwise stall the server's event loop for the whole
# operation (the same fix voice-io-mcp needed for its own blocking I/O).

def _index_directory_sync(path: str, extensions: list[str] | None) -> str:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        return f"Hata: {root} bir dizin değil ya da bulunamadı."

    ext_set = {(e if e.startswith(".") else f".{e}").lower() for e in extensions} if extensions else DEFAULT_EXTENSIONS
    conn = get_connection()
    try:
        seen_files = set()
        indexed, skipped_unchanged, chunk_total = 0, 0, 0
        for file_path in walk_indexable_files(root, ext_set):
            seen_files.add(str(file_path))
            try:
                n_chunks = index_file(conn, file_path)
            except (UnicodeDecodeError, OSError) as e:
                logger.warning("skipping %s: %s", file_path, e)
                continue
            if n_chunks == 0:
                skipped_unchanged += 1
            else:
                indexed += 1
                chunk_total += n_chunks

        root_str = str(root)
        all_indexed_paths = [row[0] for row in conn.execute("SELECT path FROM files").fetchall()]
        stale = [p for p in all_indexed_paths if is_under(p, root_str) and p not in seen_files]
        for stale_path in stale:
            _delete_file_rows(conn, stale_path)
        conn.commit()

        return (
            f"{root} indexlendi: {indexed} dosya (yeni/değişmiş), {skipped_unchanged} değişmemiş dosya atlandı, "
            f"{chunk_total} yeni chunk, {len(stale)} silinmiş dosya temizlendi."
        )
    finally:
        conn.close()


@mcp.tool()
async def index_directory(path: str, extensions: list[str] | None = None) -> str:
    """Index (or re-index) a local directory for semantic search. Walks
    recursively, skips .git/node_modules/.venv/etc and files >2MB, and
    skips any file whose content is unchanged since the last index (cheap:
    a whole-file hash check before touching the embedding model). Files
    that were indexed before but no longer exist under `path` are removed
    from the index."""
    return await asyncio.to_thread(_index_directory_sync, path, extensions)


def _search_notes_sync(query: str, top_k: int, path_prefix: str | None) -> str:
    import sqlite_vec

    if not query.strip():
        return "Hata: boş sorgu."
    if top_k <= 0:
        return "Hata: top_k pozitif bir sayı olmalı."

    conn = get_connection()
    try:
        [query_vector] = embed_texts([query])
        rows = conn.execute(
            """
            SELECT chunks.file_path, chunks.start_line, chunks.end_line, chunks.text, chunk_vectors.distance
            FROM chunk_vectors
            JOIN chunks ON chunks.id = chunk_vectors.rowid
            WHERE chunk_vectors.embedding MATCH ? AND k = ?
            ORDER BY chunk_vectors.distance
            """,
            (sqlite_vec.serialize_float32(query_vector), max(top_k * 4, top_k)),  # over-fetch, then filter by prefix below
        ).fetchall()
    finally:
        conn.close()

    if path_prefix:
        prefix = str(Path(path_prefix).expanduser().resolve())
        rows = [r for r in rows if is_under(r[0], prefix)]
    rows = rows[:top_k]

    if not rows:
        return "Sonuç bulunamadı. Önce index_directory ile bir dizin indexlenmiş mi kontrol edin."

    lines = [f"{len(rows)} sonuç:"]
    for file_path, start_line, end_line, text, distance in rows:
        snippet = text if len(text) <= 400 else text[:400] + "…"
        lines.append(f"\n--- {file_path}:{start_line}-{end_line} (distance={distance:.4f}) ---\n{snippet}")
    return "\n".join(lines)


@mcp.tool()
async def search_notes(query: str, top_k: int = 5, path_prefix: str | None = None) -> str:
    """Semantic search across everything indexed so far. Returns the top
    matching chunks with file path, line range, and a relevance-ordered
    snippet - not just a bag of file names."""
    return await asyncio.to_thread(_search_notes_sync, query, top_k, path_prefix)


def _list_indexed_files_sync(path_prefix: str | None) -> str:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT path, chunk_count, indexed_at FROM files ORDER BY path").fetchall()
    finally:
        conn.close()

    if path_prefix:
        prefix = str(Path(path_prefix).expanduser().resolve())
        rows = [r for r in rows if is_under(r[0], prefix)]

    if not rows:
        return "Index boş."
    lines = [f"{len(rows)} dosya indexlenmiş:"]
    for path, chunk_count, indexed_at in rows:
        lines.append(f"  {path} - {chunk_count} chunk, {indexed_at}")
    return "\n".join(lines)


@mcp.tool()
async def list_indexed_files(path_prefix: str | None = None) -> str:
    """Lists what's currently in the index - file path, chunk count, last
    indexed time. Useful to check what's covered before searching, or to
    debug a stale/missing result."""
    return await asyncio.to_thread(_list_indexed_files_sync, path_prefix)


def _remove_directory_sync(path: str) -> str:
    prefix = str(Path(path).expanduser().resolve())
    conn = get_connection()
    try:
        all_paths = [row[0] for row in conn.execute("SELECT path FROM files").fetchall()]
        rows = [p for p in all_paths if is_under(p, prefix)]
        for file_path in rows:
            _delete_file_rows(conn, file_path)
        conn.commit()
    finally:
        conn.close()
    return f"{len(rows)} dosya index'ten kaldırıldı ({prefix} altında)."


@mcp.tool()
async def remove_directory(path: str) -> str:
    """Removes every indexed file/chunk under `path` from the index. The
    index is persistent local state in ~/.local-notes-search/ (or
    LOCAL_NOTES_SEARCH_DB) - this is how you clean it up without deleting
    the whole database file."""
    return await asyncio.to_thread(_remove_directory_sync, path)


if __name__ == "__main__":
    mcp.run(transport="stdio")
