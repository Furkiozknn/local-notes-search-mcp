# Changelog

All notable changes to this project. Versions follow [semver](https://semver.org/);
the version lives in `pyproject.toml` and `server.json`, and the release
workflow (`.github/workflows/yayinla.yml`) refuses a tag that does not match it.

## [0.1.0] - 2026-09-25

First tagged release. Everything below is what 0.1.0 ships; there is no earlier
published version to diff against.

### Tools

- `index_directory(path, extensions=None)`: recursive, incremental indexing.
  Whole-file content hash skips unchanged files; files that disappear (or stop
  being readable text) are purged.
- `search_notes(query, top_k=5, path_prefix=None)`: semantic search returning
  `file:line-range`, a snippet and the distance. `top_k` is 1-50, and the bound
  is part of the tool's input schema.
- `ask_notes(question, top_k=5, path_prefix=None)`: the same retrieval plus an
  opt-in LLM answer grounded in the retrieved chunks (Groq, then Mistral;
  `GROQ_API_KEY` / `MISTRAL_API_KEY`). Without a key, or when the chain fails,
  it returns the raw matches instead of an error. 120 s timeout.
- `list_indexed_files(path_prefix=None)`, capped at 200 lines.
- `remove_directory(path)`: removes index rows only, never your files.
- `local-notes-search-mcp --download-model`: fetch the embedding model as an
  explicit step; `LOCAL_NOTES_SEARCH_OFFLINE=1` then forbids any download.

### Storage and model

- One sqlite-vec file (`~/.local-notes-search/index.db`,
  `LOCAL_NOTES_SEARCH_DB`), `0600` inside a `0700` directory on POSIX. It holds
  the full text of every chunk.
- Default model `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
  (multilingual, 384-d) via fastembed, cached in `~/.local-notes-search/models`
  (`LOCAL_NOTES_SEARCH_MODEL_DIR`), not in the system temp directory.
- Each indexed file records its embedder: model name and fastembed version.
  fastembed 0.6.0 changed this model's pooling from CLS to mean, so vectors
  from different versions are not comparable. A file embedded under another
  version is re-embedded by the next `index_directory` even if unchanged, and
  until then searches start with a warning and `list_indexed_files` marks it.
  Indexes created before this column existed are migrated and treated as stale.
- An index built with a different model name is refused, not compared.

### What is never indexed

- Credential file names (`.env`, `.env.*`, `id_rsa`, `credentials.json`,
  `*.pem`, `*.key`, `.npmrc`, `.pypirc`, `.git-credentials`, ...),
  case-insensitive.
- Hidden files and directories below the indexed root (`.ssh`, `.aws`,
  `.config`, `~/.claude.json`, ...). A hidden directory passed as the root is
  indexed.
- Symlinks that resolve outside the root, into a skipped directory, or to a
  credential name; dangling links and loops.
- Anything that is not a regular file (a FIFO named `*.md` used to hang
  indexing), files over 2 MB (also enforced at read time), binary files.
- With `LOCAL_NOTES_SEARCH_ALLOWED_ROOTS` set, any path outside those roots.

### Requirements

- Python 3.10-3.13, tested on each in CI with the model required.
- `fastembed>=0.6.0` (the mean-pooling release for the default model) and
  `litellm>=1.101.0` (1.98-1.100 cannot be imported on Python 3.10).

[0.1.0]: https://github.com/Furkiozknn/local-notes-search-mcp/releases/tag/v0.1.0
