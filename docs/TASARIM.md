# Tasarım: local-notes-search-mcp ilk kullanım ve README yenilemesi (30 Eylül 2026)

## Hedef

Videodan ya da profilden gelen biri ilk dakikada şunu yapabilmeli: aracın ne yaptığını tek cümlede anlamak, tek komutla kurup çalışan bir sunucudan gerçek bir arama sonucu görmek, bir MCP istemcisine bağlamadan önce sunucunun ne yapabildiğini kendi gözüyle doğrulamak. Çekirdek davranış (indeksleme, arama, kara liste/izin listesi, gömme kimliği, çıktı sözleşmesi: `Hata:` ile başlayan Türkçe düz metin, `N dosya (yeni/değişmiş)` sayım cümlesi) değişmedi; sürüm numarası artmadı (0.1.0).

## Önce / sonra

| Konu | Önce | Sonra |
|---|---|---|
| README ilk ekranı | banner, 15 sn'lik sesli reel (`docs/reel`, üreticisi depoda yok), 11 rozet, "temsilî" örnek çıktı | banner, tek cümle tanım, 5 rozet, "Bir dakikada deneyin" (3 satır kurulum + demo), gerçek çıktılı terminal kaydı, gerçek çıktıdan alıntı, "ne zaman kullanılır / kullanılmaz" tablosu; özellik rozetleri tablonun altında |
| Örnek çıktı | "128 files indexed · 941 chunks", `distance 0.31` — koşudan çıkmıyordu | `examples/stdio_demo.py`'nin gerçek çıktısı; mesafenin 0-1 puanı değil L2 olduğu yazılı |
| Demo | yok (reel bir video, komut yok) | `scripts/demo-uret.py`: iki komut gerçekten koşulur; `docs/demo/komutlar.txt` kayıttır; sayfa o kaydı yazma animasyonuyla oynatır; `demo-kayit.js` mp4/gif alır |
| Denenebilir örnek | `LOCAL_NOTES_SEARCH_DB=/tmp/... uv run python -c "..."` (yalnız Git Bash) | `examples/notes/` (depoya ait 5 not) + `examples/stdio_demo.py` (her kabukta çalışır, geçici indeks) |
| `--help` | açıklama + `--download-model` | + araçlar, ortam değişkenleri (varsayılan yollarıyla), istemci kayıt örneği, ilk indirme uyarısı; `--version` |
| `--download-model` | boş önbellekte 148 satır HTTP günlüğü | "Downloading ... (about 0.22 GB, one time)" tek satır stderr + sonuç satırı |
| `index_directory` hataları | dosya/yok ayrımı yok; boş dizin sessiz başarı | "bir dosya; dizin ister" / "bulunamadı"; işlenecek dosya yoksa aranan uzantılar ve `extensions=[...]` ipucu |
| Model yüklenemedi (offline) | `...from any source.. LOCAL_NOTES...` (çift nokta) | tek nokta |
| Test | 99 | 107 (+8: yardım/sürüm/kullanım hatası, indirme sessizliği, dosya/yok ayrımı, boş dizin ipucu, çift nokta, README demosunun iki sorgusu) |

## CLI / istemci akışı

```
kur                  git clone ... && cd ... && uv sync
dene                 uv run python examples/stdio_demo.py       (geçici indeks, örnek notlar)
modeli hazırla       uv run local-notes-search-mcp --download-model      (bir kez; sonra OFFLINE=1)
bağla                istemci ayarına: uv --directory <klon> run local_notes_search.py   (stdio)
kullan (istemciden)  index_directory -> search_notes / ask_notes -> list_indexed_files / remove_directory
yanlış yol           Hata: <yol> bulunamadı ... | bir dosya; index_directory bir dizin ister
```

## Görsel dil (video sisteminden alınanlar)

Demo, `mcp-vet` yenilemesinde kurulan FRK-OS terminal sahnesinin aynısı (`scripts/demo-uret.py`, `demo-kayit.js` oradan uyarlandı; ortak kod tekrarı bilerek yapıldı, depolar birbirine bağımlı olmasın).

| Ne | Nereden | Nerede |
|---|---|---|
| zemin `#0e0d0b`, panel `#14120e`, yazı `#f1ece2`, ilk vurgu `#ffc21a` | `sosyal/uret/tema.mjs` `klasik.akis` | terminal sahnesi zemini, panel, metin, sarı `$` isteği/`###` başlıkları/sol çizgi |
| vurgu `#ff4d6d` (mercan), `#ff7a1a` (turuncu), `#19d3e6` (camgöbeği) | `tema.mjs` klasik vurgular | `Hata:` mercan, sonuç başlığı `--- dosya:satır ---` turuncu, `distance=` camgöbeği. Yalnız boyama; metin değişmez |
| `doku: "izgara"` | `tema.mjs` | gövdede çok soluk sabit ızgara |
| JetBrains Mono | `tema.mjs` `F.jb` | tüm terminal metni; SIL OFL 1.1, `assets/yazi/` (metni ile), sayfaya gömülü |
| 30 ms/harf yazma, satır satır çıktı | `sahne.js` terminal tekniği | `demo-uret.py` sayfası; çıktı satırı adımı 130 ms (uzun çıktıda kayan pencere) |

Bilerek alınmayanlar: League Gothic başlık, geçişler (iris, glitch, flaş): çıktının kendisi okunmalı. `prefers-reduced-motion`'da imleç yanıp sönmesi kapalı.

Kontrast (panel `#14120e` üstünde, WCAG göreli parlaklığından hesaplandı): krem 15,9:1, sönük metin `#b6ae9d` 8,5:1, sarı 11,6:1, mercan 5,8:1, turuncu 7,2:1, camgöbeği 10,2:1; hepsi ≥ 4,5:1. Panelin sayfa zeminine oranı 1,04:1 (sınır çizgisi ve sol sarı çizgi ayırıyor).

## Kararlar ve sınırlar

- **Banner ve `limits.svg` değişmedi.** Banner hesabın banner üreticisinden geliyor; elle değiştirmek üreticinin sonraki çıktısında silinir. `limits.svg` "Known limitations" bölümünün açıklayıcı şeması, demo değil; kaldırılmadı.
- **`docs/reel/reel.{gif,mp4}` README'den ve depodan çıkarıldı** (`git rm`): üreticisi depoda yok, çıktısı yeniden üretilemedi. Git geçmişinde duruyor.
- Demo **örnek notlar** üzerinde (`examples/notes/`, bu depo için yazıldı, kişisel not değil). Yanıt dili Türkçe kaldı (çıktı sözleşmesi); README bunu ve mesafenin ne olduğunu söylüyor. Başka bir dile çevirmek testleri ve istemcileri kırar, kapsam dışı.
- İndeks geçici klasörde, model önbelleği makinede tek kopya; demoyu yeniden üretmek için `LOCAL_NOTES_SEARCH_MODEL_DIR` verilebilir.
- Dikey 1080x1920 kayıt (16,8 s, sessiz, H.264 yuv420p +faststart) depoya girmedi; günlük video hattı için `sosyal/medya/projeler/local-notes-search-mcp/terminal.mp4` altında.
- Sürüm, etiket, PyPI, MCP Registry, awesome-mcp-servers başvurusu, Pages ve GitHub description/homepage **yapılmadı** (onay kapısı). `server.json` `packages` bölümü PyPI paketini varsayıyor, o paket yayımlanmadı; bu yüzden README'de `uvx local-notes-search-mcp` (PyPI) yazılmadı.
