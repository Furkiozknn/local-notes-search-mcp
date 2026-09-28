"""local-notes-search-mcp: semantic search over your own local files, as an
MCP server.

Zero servers, zero API keys, zero cloud calls - everything stays on disk.
The one network access is fetching the embedding model the first time (or
explicitly via `--download-model`); LOCAL_NOTES_SEARCH_OFFLINE=1 forbids it.
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

sqlite-vec's Python binding (`sqlite_vec.load()`, `serialize_float32()`)
and fastembed's `TextEmbedding` API are exercised for real by the
model-backed tests, which CI runs with the model required (see
tests/conftest.py, LOCAL_NOTES_SEARCH_REQUIRE_MODEL).
"""

from __future__ import annotations

import asyncio
import fnmatch
import functools
import hashlib
import importlib.metadata
import logging
import os
import sqlite3
import stat
import threading
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

logger = logging.getLogger(__name__)

mcp = MCPServer("local-notes-search")

# --- configuration -----------------------------------------------------

DEFAULT_DB_PATH = Path.home() / ".local-notes-search" / "index.db"
DB_PATH = Path(os.environ.get("LOCAL_NOTES_SEARCH_DB", str(DEFAULT_DB_PATH)))

# Where the ONNX model is cached. fastembed's own default is
# `<tempdir>/fastembed_cache`, which on most systems is wiped on reboot - so
# "downloaded once" silently became "downloaded again after every restart",
# and a network call could happen at search time long after first use. A
# world-writable /tmp is also a place another local user can pre-seed. The
# cache now lives next to the index, per user, and survives reboots.
MODEL_DIR_ENV = "LOCAL_NOTES_SEARCH_MODEL_DIR"
DEFAULT_MODEL_DIR = Path.home() / ".local-notes-search" / "models"

# Set to 1/true/yes to forbid any model download: the model must already be
# in the cache (see `local-notes-search-mcp --download-model`). Unset, the
# first index/search call downloads it from Hugging Face once - the one
# network call indexing and search can make.
OFFLINE_ENV = "LOCAL_NOTES_SEARCH_OFFLINE"

# The MCP-ecosystem audit's highest-impact finding: this tool's prompts,
# docs and target corpus are Turkish, but bge-small-en-v1.5 is an
# English-only model (fastembed's own metadata says so) - Turkish notes
# were being embedded by a model that has never seen the language.
# paraphrase-multilingual-MiniLM-L12-v2 is the multilingual model
# fastembed actually ships at this size: 50+ languages including Turkish,
# the same 384 dimensions, 0.22 GB, Apache-2.0, and symmetric (queries and
# passages embed identically, so no instruction-prefix asymmetry to
# manage). Overridable for experiments; the index stores the model name
# and get_connection() refuses a mismatched index rather than comparing
# incomparable vectors.
EMBEDDING_MODEL_NAME = os.environ.get(
    "LOCAL_NOTES_SEARCH_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
)


def _embedding_dim() -> int:
    """The configured model's dimension, from fastembed's own registry -
    hardcoding 384 would silently corrupt the vector table the first time
    someone overrides the model with a 768-d one."""
    from fastembed import TextEmbedding

    for model in TextEmbedding.list_supported_models():
        if model["model"] == EMBEDDING_MODEL_NAME:
            return int(model["dim"])
    supported = ", ".join(sorted(m["model"] for m in TextEmbedding.list_supported_models()))
    # ToolError, not RuntimeError: under mcp >= 2.1 a plain exception is
    # masked to a generic "Error executing tool ..." (verified against the
    # installed SDK), and this message - like the index-mismatch one below -
    # exists precisely to tell the caller how to fix their setup.
    raise ToolError(
        f"LOCAL_NOTES_SEARCH_MODEL={EMBEDDING_MODEL_NAME!r} is not a model this "
        f"fastembed build supports. Supported: {supported}"
    )


def embedder_id() -> str:
    """What produced a vector: the model name AND the fastembed version.

    The model name alone is not enough. fastembed 0.6.0 switched this very
    model (paraphrase-multilingual-MiniLM-L12-v2) from CLS to mean pooling,
    so the same model name under fastembed 0.5.1 and 0.6+ gives vectors
    that are not comparable - an index built with one and queried with the
    other still returns results, ranked by distances between vectors from
    two different functions, and nothing says so. Every indexed file
    records this string; a file whose string differs is re-embedded on the
    next index_directory even if its content is unchanged, and searches say
    how many such files are still waiting for that."""
    return f"{EMBEDDING_MODEL_NAME}@fastembed-{_fastembed_version()}"


@functools.lru_cache(maxsize=1)
def _fastembed_version() -> str:
    # Cached: the installed package cannot change under a running process,
    # and index_file asks once per file.
    try:
        return importlib.metadata.version("fastembed")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


DEFAULT_EXTENSIONS = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml", ".rst", ".toml"}
SKIP_DIR_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache", ".next", "egg-info"}
MAX_FILE_BYTES = 2 * 1024 * 1024  # skip anything bigger - pathological chunk counts, probably not a "note"

# Result-size caps. top_k feeds sqlite-vec's KNN `k` (over-fetched 4x for
# path_prefix filtering), and sqlite-vec rejects k > 4096 with a raw SQL
# error; 50 chunks is already more context than any client should paste.
MAX_TOP_K = 50
MAX_LISTED_FILES = 200

# Opt-in directory allowlist. index_directory otherwise happily indexes any
# path the running user can read, and ask_notes then ships retrieved chunks
# to a third-party LLM - so an indexed path is a path whose contents can
# leave the machine. os.pathsep-separated, same shape and spirit as
# mini-creative-toolkit's MCT_ALLOWED_ROOTS, and unset by default: an empty
# default is not a sandbox and this project does not claim it is one.
ALLOWED_ROOTS_ENV = "LOCAL_NOTES_SEARCH_ALLOWED_ROOTS"

# Never indexed, whatever the extension filter, the allowed roots, or the
# caller's intent: file names that hold credentials outright. Cheap insurance
# against the obvious cases only - this is a name denylist, not a secret
# scanner, and it is applied unconditionally precisely because the allowlist
# above is opt-in.
SECRET_FILENAMES = {
    ".env", ".netrc", "_netrc", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials.json",
    ".npmrc", ".pypirc", ".git-credentials", "secrets.json",
}
SECRET_FILENAME_GLOBS = ("*.pem", ".env.*", "*.key", "*.p12", "*.pfx", "*.ppk", "service-account*.json")

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


def is_secret_filename(name: str) -> bool:
    """True for file names that are, by name alone, almost certainly
    credentials. Case-insensitive: Windows and macOS filesystems routinely
    hand back `.ENV` for a file created as `.env`."""
    lowered = name.lower()
    if lowered in SECRET_FILENAMES:
        return True
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in SECRET_FILENAME_GLOBS)


def allowed_roots() -> list[Path]:
    """Resolved roots from LOCAL_NOTES_SEARCH_ALLOWED_ROOTS, or an empty list
    when it is unset (meaning: no restriction, the documented default).

    Read per call rather than at import time so a client can change it
    without a server restart. A configured-but-unusable entry raises instead
    of being dropped - silently discarding every entry would turn the
    allowlist off, which is exactly the wrong way for this to fail."""
    raw = os.environ.get(ALLOWED_ROOTS_ENV)
    if not raw:
        return []
    roots = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        root = Path(part).expanduser().resolve()
        if not root.is_dir():
            raise ToolError(f"{ALLOWED_ROOTS_ENV} entry {part!r} is not a directory")
        roots.append(root)
    return roots


def _is_skipped_dir_name(name: str) -> bool:
    """Noise directories, and every hidden directory: `.ssh`, `.aws`,
    `.gnupg`, `.docker` (config.json holds registry auth), `.config`
    (gh's hosts.yml holds an OAuth token) - pointing index_directory at a home
    directory must not sweep those in through the .json/.yml filters. A hidden
    directory can still be indexed by passing it as the root itself."""
    return name in SKIP_DIR_NAMES or name.endswith(".egg-info") or name.startswith(".")


def should_index_file(path: Path, extensions: set[str], root: Path | None = None) -> bool:
    """`root`, when given, is the directory being indexed: the directory-name
    filter then looks only at the path BELOW it. Checking every part of the
    absolute path meant a root that merely lived under a directory called
    `build` or `dist` (".../build/notes") indexed nothing at all."""
    if is_secret_filename(path.name):
        return False
    if root is not None and path.name.startswith("."):
        # Same reason as hidden directories: pointed at $HOME, `.json` pulled
        # in ~/.claude.json, which holds MCP server configs with their API
        # keys in `env`. A dotfile is configuration, not a note.
        return False
    if path.suffix.lower() not in extensions:
        return False
    if root is not None:
        try:
            dir_parts = path.relative_to(root).parts[:-1]
        except ValueError:
            return False
        if any(_is_skipped_dir_name(part) for part in dir_parts):
            return False
    elif any(part in SKIP_DIR_NAMES or part.endswith(".egg-info") for part in path.parts):
        return False
    try:
        st = path.stat()
    except OSError:
        return False
    # Regular files only. A FIFO named `inbox.md` passed every other check
    # and then blocked read_text() forever, hanging index_directory (and the
    # whole tool call) on the first `open`; device files are no better.
    if not stat.S_ISREG(st.st_mode):
        return False
    return st.st_size <= MAX_FILE_BYTES


def _symlink_target_ok(path: Path, root: Path) -> bool:
    """A symlinked file is indexed only if it resolves to a regular file that
    is still inside `root` and whose REAL name is not a secret. Otherwise
    `notes/todo.md -> ~/.aws/credentials.json` (or any link out of an
    allowed root) would be read under an innocent name, walking straight past
    both the denylist and LOCAL_NOTES_SEARCH_ALLOWED_ROOTS. Directory
    symlinks are never followed (os.walk's default)."""
    try:
        target = path.resolve(strict=True)
    except (OSError, RuntimeError):  # dangling link or a symlink loop
        return False
    if not target.is_file() or is_secret_filename(target.name):
        return False
    if not target.is_relative_to(root):
        return False
    # Inside the root, but not into a directory the walk itself would skip
    # (a link into root/.ssh/ or root/node_modules/).
    return not any(_is_skipped_dir_name(part) for part in target.relative_to(root).parts[:-1])


def walk_indexable_files(root: Path, extensions: set[str]):
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_skipped_dir_name(d)]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() and not _symlink_target_ok(path, root):
                continue
            if should_index_file(path, extensions, root):
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


def model_dir() -> Path:
    """LOCAL_NOTES_SEARCH_MODEL_DIR, else fastembed's own FASTEMBED_CACHE_PATH
    if the user already set one, else ~/.local-notes-search/models."""
    raw = os.environ.get(MODEL_DIR_ENV) or os.environ.get("FASTEMBED_CACHE_PATH")
    return Path(raw).expanduser() if raw else DEFAULT_MODEL_DIR


def offline_mode() -> bool:
    return os.environ.get(OFFLINE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:  # re-check inside the lock - two concurrent callers otherwise both load
                from fastembed import TextEmbedding

                cache = model_dir()
                offline = offline_mode()
                kwargs = {"cache_dir": str(cache)}
                if offline:
                    kwargs["local_files_only"] = True
                try:
                    with warnings.catch_warnings():
                        # fastembed >= 0.6 warns on every load that this model
                        # "now uses mean pooling" and suggests pinning 0.5.1.
                        # That advice is wrong here: mean pooling is what the
                        # model was trained with, and embedder_id() records
                        # the fastembed version so an index built under the
                        # old pooling is re-embedded instead of mixed in.
                        warnings.filterwarnings("ignore", message=r".*now uses mean pooling.*", category=UserWarning)
                        _model = TextEmbedding(model_name=EMBEDDING_MODEL_NAME, **kwargs)
                except Exception as e:
                    # Without this the caller sees fastembed's retry log and a
                    # proxy/HTTP error, with no hint that the fix is a one-time
                    # download or a different cache path.
                    if offline:
                        hint = (
                            f"{OFFLINE_ENV} is set, so nothing is downloaded and the model is not in "
                            f"{cache}. Run `local-notes-search-mcp --download-model` once with network "
                            f"access (same {MODEL_DIR_ENV}), or unset {OFFLINE_ENV}."
                        )
                    else:
                        hint = (
                            "The first index/search call downloads it from Hugging Face once; that "
                            "download failed. Check network access, or copy the model into "
                            f"{cache} ({MODEL_DIR_ENV}) and set {OFFLINE_ENV}=1."
                        )
                    raise ToolError(
                        f"Embedding model {EMBEDDING_MODEL_NAME!r} could not be loaded: {e}. {hint}"
                    ) from e
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
    indexed_at TEXT NOT NULL,
    embedder TEXT
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
    # The index holds the full text of every indexed chunk, in plain text.
    # With the default umask it came out world-readable (0644 file in a
    # 0755 directory), so on a shared machine any local user could read
    # every note that had been indexed. Owner-only on POSIX; Windows
    # profiles are already per-user and chmod cannot express that there.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    _restrict_permissions(path)
    # Every error path out of here must close `conn` first. Callers do
    # `conn = get_connection()` then `try/finally: conn.close()`, and that
    # finally never runs if the assignment itself never completes - the
    # connection would leak, holding the index file locked on Windows until
    # GC. Only the model-mismatch branch used to handle this; _embedding_dim()
    # raising ToolError for an unsupported LOCAL_NOTES_SEARCH_MODEL (and any
    # failure in the extension load or schema setup) leaked straight through.
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.executescript(_SCHEMA)
        # Indexes created before the embedder column existed: add it. Their
        # rows stay NULL ("unknown embedder"), which counts as stale - the
        # version that built them was never written down.
        if "embedder" not in {row[1] for row in conn.execute("PRAGMA table_info(files)")}:
            conn.execute("ALTER TABLE files ADD COLUMN embedder TEXT")
            conn.commit()

        existing_model = conn.execute("SELECT value FROM meta WHERE key = 'embedding_model'").fetchone()
        if existing_model is None:
            conn.execute("INSERT INTO meta (key, value) VALUES ('embedding_model', ?)", (EMBEDDING_MODEL_NAME,))
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0(embedding float[{_embedding_dim()}])"
            )
            conn.commit()
        elif existing_model[0] != EMBEDDING_MODEL_NAME:
            # A different embedding model's vectors are not comparable to this
            # model's query vectors - refuse rather than silently returning
            # garbage similarity scores (see README "pitfalls").
            raise ToolError(
                f"Index at {path} was built with embedding model {existing_model[0]!r}, "
                f"but this build uses {EMBEDDING_MODEL_NAME!r}. Delete the index file "
                f"or set LOCAL_NOTES_SEARCH_DB to a fresh path, then re-index."
            )
    except BaseException:
        conn.close()
        raise
    return conn


def _restrict_permissions(db_path: Path) -> None:
    if os.name != "posix":
        return
    try:
        db_path.chmod(0o600)
        # Only tighten a directory this tool owns - a user who put their DB
        # in an existing folder of their own keeps that folder's mode.
        if db_path.parent == DEFAULT_DB_PATH.parent:
            db_path.parent.chmod(0o700)  # a directory needs x to be entered
    except OSError as e:  # not the owner, read-only fs, ... - never fatal
        logger.warning("could not restrict permissions on %s: %s", db_path, e)


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

    # Bounded read: the size check in should_index_file ran earlier, and a
    # file can grow between that stat and this open. Never read more than the
    # cap, whatever the file has become since.
    with path.open("rb") as fh:
        raw = fh.read(MAX_FILE_BYTES + 1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError(f"{path} grew past {MAX_FILE_BYTES} bytes while indexing")
    text = raw.decode("utf-8", errors="strict")
    if "\x00" in text:
        # NUL is valid UTF-8, so a binary file with a text extension (a
        # .json that is really a database dump, a .txt that is an image)
        # decodes fine and would be embedded as noise.
        raise ValueError(f"{path} looks binary (contains NUL bytes)")
    file_hash = content_hash(text)
    file_key = str(path)
    embedder = embedder_id()

    existing = conn.execute("SELECT content_hash, embedder FROM files WHERE path = ?", (file_key,)).fetchone()
    if existing is not None and existing[0] == file_hash and existing[1] == embedder:
        return 0

    _delete_file_rows(conn, file_key)

    chunks = chunk_text(text)
    if not chunks:
        conn.execute(
            "INSERT INTO files (path, content_hash, chunk_count, indexed_at, embedder) VALUES (?, ?, 0, ?, ?)",
            (file_key, file_hash, datetime.now(timezone.utc).isoformat(), embedder),
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
        "INSERT INTO files (path, content_hash, chunk_count, indexed_at, embedder) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash, "
        "chunk_count=excluded.chunk_count, indexed_at=excluded.indexed_at, embedder=excluded.embedder",
        (file_key, file_hash, len(chunks), datetime.now(timezone.utc).isoformat(), embedder),
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

    roots = allowed_roots()
    if roots and not any(is_under(str(root), str(r)) for r in roots):
        return (
            f"Hata: {root} izin verilen köklerin dışında. "
            f"{ALLOWED_ROOTS_ENV}: {os.pathsep.join(str(r) for r in roots)}"
        )

    ext_set = {(e if e.startswith(".") else f".{e}").lower() for e in extensions} if extensions else DEFAULT_EXTENSIONS
    conn = get_connection()
    try:
        seen_files = set()
        indexed, skipped_unchanged, chunk_total, unreadable = 0, 0, 0, 0
        for file_path in walk_indexable_files(root, ext_set):
            try:
                n_chunks = index_file(conn, file_path)
            except (ValueError, OSError) as e:  # UnicodeDecodeError is a ValueError
                # Not added to seen_files: a file that USED to be readable
                # text and no longer is must drop out of the index below,
                # not keep answering searches with its old content.
                logger.warning("skipping %s: %s", file_path, e)
                unreadable += 1
                continue
            seen_files.add(str(file_path))
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
            f"{chunk_total} yeni chunk, {len(stale)} silinmiş dosya temizlendi"
            + (f", {unreadable} dosya okunamadı (UTF-8 metin değil ya da ikili)." if unreadable else ".")
        )
    finally:
        conn.close()


@mcp.tool()
async def index_directory(path: str, extensions: list[str] | None = None) -> str:
    """Index (or re-index) a local directory for semantic search. Walks
    recursively, skips .git/node_modules/.venv/etc and files >2MB, and
    skips any file whose content is unchanged since the last index (cheap:
    a whole-file hash check before touching the embedding model) - unless
    it was embedded by a different model/fastembed version, in which case it
    is re-embedded. Files that were indexed before but no longer exist under
    `path` are removed from the index.

    Hidden files and directories (.ssh, .aws, .config, .claude.json, ...)
    and anything that is not a regular file (FIFOs, devices) are skipped;
    symlinked files are followed only when their target stays inside `path`.
    Credential-shaped file names (.env, id_rsa, credentials.json, .netrc,
    *.pem, *.key, ...) are never indexed. The index stores the full text of
    every chunk. If LOCAL_NOTES_SEARCH_ALLOWED_ROOTS is
    set, `path` must resolve inside one of its entries; unset (the default)
    means any readable directory is indexable."""
    return await asyncio.to_thread(_index_directory_sync, path, extensions)


# Shared by search_notes and ask_notes - both need the same "embed query,
# KNN lookup, filter by path_prefix, cap at top_k" retrieval step; only how
# the result is presented (or further processed) differs. Extracted after
# ask_notes was added, so retrieval logic exists in exactly one place (the
# same reasoning nvidia-nim-mcp's _run_chat_chain extraction documents).
def _stale_embedder_notice(conn: sqlite3.Connection) -> str:
    """A warning line when some indexed files were embedded by a different
    embedder_id() than the one answering this query, else "". Their vectors
    are still searched - dropping them would silently hide notes instead -
    but their ranking can be off until they are re-embedded."""
    stale = conn.execute(
        "SELECT COUNT(*) FROM files WHERE chunk_count > 0 AND (embedder IS NULL OR embedder != ?)",
        (embedder_id(),),
    ).fetchone()[0]
    if not stale:
        return ""
    return (
        f"Uyarı: {stale} dosya farklı bir gömme sürümüyle indexlenmiş (şu anki: {embedder_id()}); "
        "bu dosyaların eşleşmeleri güvenilir değil. Bu dizinler için index_directory'yi yeniden "
        "çalıştırın - içeriği değişmemiş dosyalar da yeniden gömülür (list_indexed_files hangileri "
        "olduğunu gösterir).\n\n"
    )


def _retrieve(query: str, top_k: int, path_prefix: str | None) -> tuple[list[tuple], str] | str:
    """Returns ((file_path, start_line, end_line, text, distance) rows,
    stale-embedder notice or ""), or a str error message if the query/top_k
    is invalid."""
    import sqlite_vec

    if not query.strip():
        return "Hata: boş sorgu."
    if top_k <= 0:
        return "Hata: top_k pozitif bir sayı olmalı."
    if top_k > MAX_TOP_K:
        return f"Hata: top_k en fazla {MAX_TOP_K} olabilir (istenen: {top_k})."

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
        notice = _stale_embedder_notice(conn)
    finally:
        conn.close()

    if path_prefix:
        prefix = str(Path(path_prefix).expanduser().resolve())
        rows = [r for r in rows if is_under(r[0], prefix)]
    return rows[:top_k], notice


def _format_results(rows: list[tuple]) -> str:
    lines = [f"{len(rows)} sonuç:"]
    for file_path, start_line, end_line, text, distance in rows:
        snippet = text if len(text) <= 400 else text[:400] + "…"
        lines.append(f"\n--- {file_path}:{start_line}-{end_line} (distance={distance:.4f}) ---\n{snippet}")
    return "\n".join(lines)


# The bound is also in the tool's input schema, so a client sees 1-50
# before it calls; _retrieve still checks it for direct (non-MCP) callers.
TopK = Annotated[int, Field(ge=1, le=MAX_TOP_K)]

NO_RESULTS_MESSAGE = "Sonuç bulunamadı. Önce index_directory ile bir dizin indexlenmiş mi kontrol edin."


def _search_notes_sync(query: str, top_k: int, path_prefix: str | None) -> str:
    retrieved = _retrieve(query, top_k, path_prefix)
    if isinstance(retrieved, str):
        return retrieved
    rows, notice = retrieved
    if not rows:
        return notice + NO_RESULTS_MESSAGE
    return notice + _format_results(rows)


@mcp.tool()
async def search_notes(query: str, top_k: TopK = 5, path_prefix: str | None = None) -> str:
    """Semantic search across everything indexed so far. Returns the top
    matching chunks with file path, line range, and a relevance-ordered
    snippet - not just a bag of file names. top_k is 1-50."""
    return await asyncio.to_thread(_search_notes_sync, query, top_k, path_prefix)


# --- ask_notes: retrieve + LLM synthesis --------------------------------
# Independent litellm provider chain, NOT a call into nvidia-nim-mcp - MCP
# servers can't call each other's tools directly (only the orchestrating
# LLM can invoke a tool), so this reuses the *design pattern* of
# nvidia-nim-mcp's free-tier fallback chain, not its code (same
# "independent implementation, shared pattern, no coupling" precedent
# model-comparison-harness's --rubric feature already established in this
# ecosystem). Both model names below are ones nvidia-nim-mcp already
# confirmed working with a real call (2026-08-22) - reused here specifically
# to avoid introducing yet another unverified model name from memory.
LLM_PROVIDER_CHAIN = [
    {"env": "GROQ_API_KEY", "model": "groq/openai/gpt-oss-120b"},
    {"env": "MISTRAL_API_KEY", "model": "mistral/mistral-small-latest"},
]

ASK_NOTES_SYSTEM_PROMPT = (
    "Sen kullanıcının kendi yerel dosyalarından alınan parçaları kullanarak soru "
    "cevaplayan bir asistansın. SADECE aşağıda verilen bağlamdaki bilgiyi kullan; "
    "bağlamda cevap yoksa uydurma, açıkça 'Bu bilgi indexlenen dosyalarda bulunamadı' de."
)


def _build_llm_chain() -> list[dict]:
    chain = []
    for provider in LLM_PROVIDER_CHAIN:
        key = os.environ.get(provider["env"])
        if key:
            chain.append({"model": provider["model"], "api_key": key})
    return chain


def _redact(text: str) -> str:
    """Scrub every configured provider API key out of an error string before
    it is logged. Ported from voice-io-mcp's helper of the same name, widened
    to cover the whole provider chain since there is no single "the" key
    here. Defense-in-depth: no known code path embeds the raw key in an
    exception's str(), but an underlying HTTP client doing so in some failure
    mode isn't ruled out, and logs outlive the request that wrote them."""
    for provider in LLM_PROVIDER_CHAIN:
        key = os.environ.get(provider["env"])
        if key:
            text = text.replace(key, "***")
    return text


async def _synthesize_answer(question: str, rows: list[tuple]) -> str | None:
    """Returns None (not raises) if no LLM provider is configured, every
    configured provider fails, or a provider responds successfully but with
    no usable content (empty `choices`, or empty/None message content - some
    providers return this for a moderation-filtered or tool-call-only
    completion) - the caller degrades to raw search results in every one of
    these cases rather than erroring out or crashing on an IndexError."""
    chain = _build_llm_chain()
    if not chain:
        return None

    context = "\n\n".join(f"[{fp}:{sl}-{el}]\n{text}" for fp, sl, el, text, _ in rows)
    messages = [
        {"role": "system", "content": ASK_NOTES_SYSTEM_PROMPT},
        {"role": "user", "content": f"Bağlam:\n{context}\n\nSoru: {question}"},
    ]
    primary, fallbacks = chain[0], chain[1:]
    try:
        import litellm

        response = await litellm.acompletion(
            messages=messages,
            max_tokens=1024,
            fallbacks=fallbacks or None,
            # litellm's default is 600s; a wedged provider must not hold
            # ask_notes for ten minutes before degrading to raw results.
            timeout=120.0,
            **primary,
        )
        content = response.choices[0].message.content if response.choices else None
    except Exception as e:
        logger.warning("ask_notes: LLM synthesis failed across the whole chain: %s", _redact(str(e)))
        return None
    return content or None


async def _ask_notes_async(question: str, top_k: int, path_prefix: str | None) -> str:
    retrieved = await asyncio.to_thread(_retrieve, question, top_k, path_prefix)
    if isinstance(retrieved, str):
        return retrieved
    rows, notice = retrieved
    if not rows:
        return notice + NO_RESULTS_MESSAGE

    if not _build_llm_chain():
        return notice + (
            "Not: GROQ_API_KEY ya da MISTRAL_API_KEY yapılandırılmamış - cevap sentezlenemedi, "
            "ham eşleşen parçalar:\n\n" + _format_results(rows)
        )

    answer = await _synthesize_answer(question, rows)
    if answer is None:
        return notice + (
            "Not: LLM sağlayıcı(lar)ından geçerli bir yanıt alınamadı (hata ya da boş içerik) - "
            "ham eşleşen parçalar:\n\n" + _format_results(rows)
        )

    sources = "\n".join(f"  - {fp}:{sl}-{el}" for fp, sl, el, _, _ in rows)
    return f"{notice}{answer}\n\nKaynaklar:\n{sources}"


@mcp.tool()
async def ask_notes(question: str, top_k: TopK = 5, path_prefix: str | None = None) -> str:
    """Ask a question in natural language about your indexed files. Retrieves
    the most relevant chunks (same retrieval as search_notes) and asks an LLM
    (Groq, then Mistral fallback - needs GROQ_API_KEY or MISTRAL_API_KEY) to
    synthesize an answer grounded ONLY in those chunks, with file:line
    sources. Without either key configured, degrades to returning the raw
    retrieved chunks with a note that no LLM is available - never fails
    outright just because synthesis isn't possible."""
    return await _ask_notes_async(question, top_k, path_prefix)


def _list_indexed_files_sync(path_prefix: str | None) -> str:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT path, chunk_count, indexed_at, embedder FROM files ORDER BY path").fetchall()
    finally:
        conn.close()

    if path_prefix:
        prefix = str(Path(path_prefix).expanduser().resolve())
        rows = [r for r in rows if is_under(r[0], prefix)]

    if not rows:
        return "Index boş."
    lines = [f"{len(rows)} dosya indexlenmiş:"]
    current = embedder_id()
    for path, chunk_count, indexed_at, embedder in rows[:MAX_LISTED_FILES]:
        stale = f" [yeniden indexlenmeli: {embedder or 'gömme sürümü kayıtsız'}]" if chunk_count and embedder != current else ""
        lines.append(f"  {path} - {chunk_count} chunk, {indexed_at}{stale}")
    if len(rows) > MAX_LISTED_FILES:
        lines.append(
            f"  ... ve {len(rows) - MAX_LISTED_FILES} dosya daha (ilk {MAX_LISTED_FILES} gösterildi; "
            "daraltmak için path_prefix kullanın)"
        )
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


def download_model() -> Path:
    """Fetch the embedding model into model_dir() now, explicitly, so that
    every later index/search call can run with LOCAL_NOTES_SEARCH_OFFLINE=1."""
    if offline_mode():
        raise SystemExit(f"--download-model needs network access; unset {OFFLINE_ENV} for this one run.")
    try:
        _get_model()
    except ToolError as e:
        raise SystemExit(str(e)) from None
    return model_dir()


def main(argv: list[str] | None = None) -> None:
    """Console entry point.

    A separate function because `[project.scripts]` wants a CALLABLE, not a
    module. Without it the package installs but cannot be run: the user would
    have to clone the repository and point at the file, which defeats the
    point of publishing it.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="local-notes-search-mcp",
        description="Semantic search over your own local files, as a stdio MCP server.",
    )
    parser.add_argument(
        "--download-model",
        action="store_true",
        help=f"download the embedding model into the cache ({MODEL_DIR_ENV}, default "
        f"{DEFAULT_MODEL_DIR}) and exit, instead of starting the server",
    )
    args = parser.parse_args(argv)
    if args.download_model:
        path = download_model()
        print(f"{EMBEDDING_MODEL_NAME} is cached in {path}. Set {OFFLINE_ENV}=1 to forbid any further download.")
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
