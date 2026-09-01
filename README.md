# local-notes-search-mcp

Semantic search over your own local files, as an MCP server. Point it at a
folder (project notes, scattered `Claude projeler/` subdirectories, a docs
tree) and ask questions in natural language instead of grepping for exact
keywords.

**Zero servers, zero API keys, zero network calls at query time.** Everything
- the vector index and the embedding model - lives on your machine.

```
index_directory("C:/Users/you/Desktop/notes")
search_notes("what did I decide about the auth redesign?")
→ 3 sonuç:
  --- C:/.../notes/2026-08-decisions.md:12-24 (distance=0.31) ---
  ## Auth redesign
  Kararlaştırdık: JWT yerine session-based...
```

## Neden bu mimari?

| Karar | Neden |
|---|---|
| **sqlite-vec** (Apache-2.0) for vector storage | A `vec0` virtual table inside one ordinary `.sqlite` file - no daemon, no Docker, no hosted service. Qdrant/pgvector were considered and rejected specifically because they need a running server process; this ecosystem consistently prefers embeddable, zero-infra local-file stores (see e.g. `buradane`'s Postgres choice being the *exception*, justified there by real geospatial query needs this tool doesn't have). |
| **fastembed** (Apache-2.0), not `sentence-transformers` | `nvidia-nim-mcp`'s own `create_embedding` tool already wraps `sentence-transformers` (torch, ~1GB) as a rarely-hit fallback - fine there, since most calls never reach it. Here, local embedding is the *only* path, hit on every single index/search call, so a ~1GB mandatory dependency would be a real cost, not a rare one. fastembed's quantized ONNX models run ~100-150MB with no torch requirement. Deliberate divergence from the sibling tool's pattern, not an oversight - see the module docstring in `local_notes_search.py`. |
| `BAAI/bge-small-en-v1.5`, 384 dimensions | A small, fast, permissively-licensed general-purpose embedding model - good enough for personal-notes-scale search, no GPU needed. |
| Line-based recursive-ish chunking (no NLP/AST dependency) | Chunks accumulate whole lines until a char budget is hit (never splits a line in half, so file:line references stay exact) with an overlap window so context isn't lost at a chunk boundary. Simple, deterministic, fully unit-tested without needing the embedding model at all. |
| Whole-file content-hash skip on re-index | `index_directory` is meant to be re-run often (cron, or just "index again before searching") - re-embedding unchanged files would waste CPU on every call for no benefit, so unchanged files are skipped after one cheap hash comparison. |

## MCP araçları

| Tool | Ne yapar |
|---|---|
| `index_directory(path, extensions=None)` | Bir dizini (recursive) indexler. `.git`/`node_modules`/`.venv`/vb. ve 2MB üzeri dosyalar atlanır. Değişmemiş dosyalar atlanır, silinmiş dosyalar index'ten temizlenir. |
| `search_notes(query, top_k=5, path_prefix=None)` | Doğal dilde semantik arama - dosya:satır aralığı + snippet + mesafe skoruyla sonuç döner. |
| `list_indexed_files(path_prefix=None)` | Şu an index'te ne var, ne zaman indexlendi. |
| `remove_directory(path)` | Bir dizin altındaki her şeyi index'ten kaldırır (dosyaları silmez, sadece index'i temizler). |

## Kurulum

```bash
uv sync
```

MCP istemcinize (Claude Code dahil) `local_notes_search.py`'yi stdio üzerinden
çalışacak şekilde ekleyin. İlk `search_notes`/`index_directory` çağrısında
fastembed modeli (~130MB) bir kere indirilip yerel olarak cache'lenir - sonraki
tüm çağrılar tamamen offline çalışır.

İsteğe bağlı: `LOCAL_NOTES_SEARCH_DB` ortam değişkeniyle index dosyasının
konumu değiştirilebilir (varsayılan: `~/.local-notes-search/index.db`, tüm
indexlenen dizinler için TEK bir dosya - böylece farklı proje klasörleri
arasında da tek bir `search_notes` çağrısıyla arama yapılabilir).

## Test

```bash
uv run pytest -v
```

İki katmanlı test stratejisi (bu ekosistemde tekrar eden bir desen - bkz.
`buradane`'in DB-skip fixture'ı, `voice-io-mcp`'nin local-extra-skip
fixture'ı): saf mantık testleri (chunking, hash, dosya tarama) her zaman
çalışır. Gerçek fastembed modeli ve sqlite-vec eklentisi gerektiren testler,
bunlar bu ortamda yüklenemiyorsa (offline bir CI runner'da ilk-çalıştırma
model indirmesi başarısız olursa, ya da bağımlılık kurulu değilse) sessizce
skip edilir - `pytest.raises` yerine sahte bir "başarılı" göstermek yerine
dürüst bir sinyal.

**Bu build ortamında 25/25 test gerçekten çalıştırılıp geçti** - hem saf
mantık testleri hem gerçek model+index+arama uçtan uca akışı (fastembed
modeli gerçekten indirildi/yüklendi, sqlite-vec eklentisi gerçekten çalıştı,
"pasta nasıl pişirilir" araması gerçekten alakalı dosyayı bulup alakasız
dosyayı hariç tuttu).

## Bilinen sınırlamalar

- **BGE'nin asimetrik query-instruction prefix'i kullanılmıyor.** `bge-small-
  en-v1.5` gibi modeller, sorgu metnini "Represent this sentence for
  searching relevant passages: " gibi bir instruction prefix'iyle embed
  etmeyi önerir (belge embed'inden farklı). Bu araç sorguyu ve belgeleri
  aynı şekilde embed ediyor - basitlik için bilinçli bir v1 kısıtlaması,
  arama kalitesini bir miktar düşürür ama fastembed'in bu modele özel
  API yüzeyi (`query_embed` gibi) canlı doğrulanmadan tahmini bir isimle
  kullanılmak istenmedi (bu ekosistemde tekrar eden "hafızadan isim
  kullanmadan önce doğrula" disiplini - bkz. nvidia-nim-mcp/voice-io-mcp'nin
  kendi model-adı dürüstlük notları).
- **sqlite-vec ve fastembed'in tam Python API yüzeyi** (`sqlite_vec.load()`,
  `sqlite_vec.serialize_float32()`, `TextEmbedding.embed()`) resmi
  dokümantasyona göre yazıldı ama bu repo'yu yazarken canlı olarak
  denenmedi - **bu ortamda gerçekten çalıştırılıp 25/25 testle doğrulandı**,
  ama farklı bir Python/OS kombinasyonunda ekstra bir smoke-test iyi olur.
- CI'da fastembed modeli her çalıştırmada yeniden indiriliyor (cache
  yapılandırılmadı) - küçük bir hobi projesi için kabul edilebilir, ama
  `actions/cache` ile hızlandırılabilir (yapılmadı, düşük öncelik).
- Tek-yazarlı SQLite: aynı anda birden fazla `index_directory`/`search_notes`
  çağrısı farklı süreçlerden gelirse (bu araç tek bir Claude Code
  oturumundan kullanılacak şekilde tasarlandı) yazma çakışması olabilir.

## Lisans

[MIT](LICENSE).
