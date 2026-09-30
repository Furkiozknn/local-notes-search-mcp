# Denetim: local-notes-search-mcp (30 Eylül 2026)

Yenilemeden önce `main` (`031d7e5`, 0.1.0) üzerinde, bu makinede (Windows 11, Python 3.12, uv 0.12.5, Git Bash) ölçüldü. Ölçülmeyen bir şey yazılmadı. Ham çıktılar depo dışında: `kanit/local-notes-search-mcp/{once,sonra}/` (aynı komutlar iki sürüme karşı: `olc.py`). Kullanıcının gerçek notlarına ve `~/.local-notes-search`'e dokunulmadı: her koşuda `LOCAL_NOTES_SEARCH_DB` geçici klasördeydi, model önbelleği ayrı bir geçici klasördeydi.

## Temiz ortamda kurulum ve ilk sonuç

| Yol | Süre | Sonuç |
|---|---|---|
| taze `git clone --depth 1` + `uv sync --no-cache --group dev` (boş uv önbelleği) | 21,9 s | kuruldu (litellm dahil) |
| `uv run local-notes-search-mcp --download-model` (boş model önbelleği, 241 MB) | 25,4 s ilk koşu, 19,6-22,4 s sonraki boş-önbellek koşuları (uv başlatma ~5 s dahil) | model indi |
| ilk gerçek sonuç: `examples/stdio_demo.py` (initialize, tools/list, index, 2 arama) | 18,6 s (model sıcak) | çıktı belgelendi |
| `python -m venv` + `pip install -e .` (CONTRIBUTING'deki yol) | 90,6 s | çalıştı, `--version` yanıt verdi |
| `uv --directory <klon> run local_notes_search.py` (README'deki istemci ayarı) stdio `initialize` + `tools/list` | 7,9 s | 5 araç listelendi |

"Tek komut, bir dakikada ilk sonuç": klon + `uv sync` (22 s) + model (~20 s) + demo (19 s) ≈ 60 s. Model indirmesi ağa bağlı; ilk çalıştırmada kaçınılamaz ve README bunu açıkça söylüyor.

## README komutları

| Komut | Sonuç |
|---|---|
| `git clone ... && uv sync` | çalıştı |
| "Try it" bloğu: `LOCAL_NOTES_SEARCH_DB=/tmp/lns-try.db uv run python -c "..."` | Git Bash'te çalıştı (MSYS `/tmp`'yi çeviriyor); **PowerShell ve cmd'de `VAR=x komut` sözdizimi geçerli değil**, Windows'ta `/tmp` yok. Yerine taşınabilir `uv run python examples/stdio_demo.py` kondu |
| `uv run local-notes-search-mcp --download-model` | çalıştı; boş önbellekte **148-153 satır HTTP istemci günlüğü** basıyordu (aşağıda) |
| `LOCAL_NOTES_SEARCH_OFFLINE=1` + model yok | araç çağrısı, çözümü söyleyen bir hata döndü (README'nin dediği gibi); mesajda çift nokta vardı |
| `LOCAL_NOTES_SEARCH_ALLOWED_ROOTS` dışında yol | `Hata: ... izin verilen köklerin dışında`, doğru |
| `ask_notes`, anahtar yok | ham eşleşmeler + not, hata değil (README'nin dediği gibi) |
| `uv run pytest -v` | 91 geçti, 8 skip (Windows: 7 POSIX izin/symlink + 1 `mkfifo`), 22,7 s |
| CI (`ci.yml`: test, sürüm matrisi 3.10/3.11/3.13, paket, mcp-vet) | yerelde koşulmadı; PR'daki koşuya bakıldı |

Sayılar: README'deki "99 tests" → bu makinede 91 geçti + 8 skip = 99 (uyuştu); CI'da 99 geçti (25 Eylül ölçümü, README'de tarihli). Yenilemeden sonra 107 (bkz. TASARIM). README'deki örnek çıktı ("128 files indexed · 941 chunks", "distance 0.31") **koşudan çıkmıyordu**: kendisi "temsilî" diyordu ama tablo ve mesafeler uydurmaydı. Gerçek çıktıda mesafeler 4,3-5,1 arası (sqlite-vec varsayılanı L2), 0-1 puanı değil.

## Hata mesajları ve `--help`

Çıkış kodları ve `Hata:` ile başlayan araç yanıtları (istisna atmayan, `isError=false`) tutarlıydı; sözleşme korunuyor. Bulgular:

| Girdi | Önce | Sorun |
|---|---|---|
| `--help` | tek satır açıklama + `--download-model` | araçlar, ortam değişkenleri, varsayılan yollar, istemciye nasıl kaydedilir yok |
| `--version` | `unrecognized arguments: --version`, çıkış 2 | sürümü öğrenmenin yolu yok |
| `--download-model`, boş önbellek | 148-153 satır `HTTP Request: GET https://huggingface.co/...` (stderr), ne indirildiği/boyutu söylenmiyor | asıl cevap (tek satır) gürültüde kaybolur |
| `index_directory("yok")` ve `index_directory("dosya.md")` | ikisi de `bir dizin değil ya da bulunamadı` | dosya mı yok mu ayırt edilmiyor |
| `index_directory("bos-dizin")` (yalnız `.png` var, ya da yanlış klasör) | `0 dosya (yeni/değişmiş), ... indexlendi.` | başarı gibi okunuyor; hangi uzantıların arandığı söylenmiyor |
| model yok + `OFFLINE=1` | `... from any source.. LOCAL_NOTES_SEARCH_OFFLINE is set ...` | çift nokta; düzeltme yolu ise doğru ve yazılı |
| `search_notes(top_k=0/51)` | pydantic doğrulama hatası (`Input should be greater than or equal to 1`) | anlaşılır, şemada sınır var; değiştirilmedi |
| boş index'te arama | `Sonuç bulunamadı. Önce index_directory ...` | iyi |

## Araç açıklamaları ve mcp-vet

`mcp-vet audit --offline --path .` (mcp-vet 0.6.0 + ana dal): **OVERALL RISK LOW**, çıkış 1 (CI kapısı ≥2'de kapanıyor, geçer). Kaynak alanı HIGH ama "verdict'e girmiyor": üç bulgu (`cloud credential stores`, `.netrc`, `SSH key material`) yalnızca `is_secret_filename` kara listesindeki ad ve docstring'de geçiyor ("only in a denylist or a comment"); bunlar sunucunun **kaçındığı** yerler. MEDIUM: test dosyalarındaki `chmod`/silme, ve `ask_notes` için "ortam değişkeni oku → groq/mistral'e çağrı" eş-konumu (LOW güvenle, belgelenmiş). Araç açıklamalarında (docstring) mcp-vet'in tool-poisoning/enjeksiyon kuralları (`injection.py`) hiçbir bulgu üretmedi. Açıklamaları ayrıca elle okudum: talimat değil sözleşme anlatıyorlar; `ask_notes` açıklaması anahtarın gerektiğini, geri düşüşü ve ağa çıkabildiğini söylüyor. Aracı çalıştırıp `tools/list` ile aldığım gerçek şema: 5 araç (`index_directory(path, extensions)`, `search_notes(query, top_k, path_prefix)`, `ask_notes(question, top_k, path_prefix)`, `list_indexed_files(path_prefix)`, `remove_directory(path)`); `top_k` şemada 1-50.

Yenileme sonrası aynı denetim yeni `scripts/` dosyalarını görüyor (`subprocess`, dosya yazma) ve "outside the shipped server" diye işaretliyor; genel risk yine LOW, çıkış yine 1. Bu turda bulunan bir yanlış pozitif: `demo-kayit.js` içindeki bir yorum satırı ("npm i -g ...") "runtime'da paket kuruyor" bulgusu (HIGH) üretti; yorum yeniden yazıldı. (mcp-vet'in yorum satırlarını bu kuralda saymaması ayrı bir iş; bu depoda değiştirilmedi.)

## Günlük "Ekosistem denetimi" (#19)

Açık konuda bu depoya ait bulgu yok (tek bulgu `Furkiozknn` deposunun `ci.workflows` ayrışması). Kapatılacak bir şey yok.

## Ölçülmeyenler

- Linux/macOS'ta POSIX izin ve symlink testleri bu makinede skip; CI'da koşuyor.
- `uvx --from git+...` yolu ana dalda `--version` bilmediği için yenileme dalından ölçüldü (bkz. TASARIM).
- PyPI paketi yayımlanmadı, `uvx local-notes-search-mcp` (PyPI'den) denenemez.

## Yenileme sonrası CI

PR #22, 30 Eylül 2026: `test` (3.12) ve sürüm matrisi (3.10, 3.11, 3.13) dördünde de 107 geçti; `paket`, `mcp-vet` (çıkış 1, kapı ≥2) ve CodeQL yeşil.
