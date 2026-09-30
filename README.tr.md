<div align="center">

<img src="assets/banner.svg" alt="local-notes-search-mcp - kendi dosyalarinizda anlamsal arama, bir MCP sunucusu olarak" width="100%">

# 🔎 local-notes-search-mcp

### **Kendi dosyalarınızda anlamsal arama — bir MCP sunucusu olarak.**

*Altı ay önce tam olarak hangi kelimeyi yazdığınızı hatırlamaya çalışmak yerine, doğal dilde sorun.*

<br/>

[![CI](https://github.com/Furkiozknn/local-notes-search-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Furkiozknn/local-notes-search-mcp/actions/workflows/ci.yml)
[![Testler](https://img.shields.io/badge/testler-99-3fb950?logo=pytest&logoColor=white)](tests/)
[![Lisans: MIT](https://img.shields.io/badge/lisans-MIT-8957e5)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](.python-version)
[![MCP](https://img.shields.io/badge/MCP-sunucu-000000?logo=anthropic&logoColor=white)](https://modelcontextprotocol.io)

**🇬🇧 [English README →](README.md)**

</div>

---

## Bir dakikada deneyin

```bash
git clone https://github.com/Furkiozknn/local-notes-search-mcp.git
cd local-notes-search-mcp
uv sync && uv run python examples/stdio_demo.py
```

Bu, sunucuyu başlatır, bir MCP istemcisi gibi stdio üzerinden konuşur ve
[`examples/notes/`](examples/notes/) altındaki örnek notlarda arar: bu depo için
yazılmış beş kısa dosya, **kimsenin gerçek notu değil**; index geçici bir
klasörde durur, `~/.local-notes-search`'e dokunulmaz. Yazarın makinesinde
(Windows 11, Python 3.12, uv 0.12.5, 30 Eylül 2026) ölçüldü: boş uv önbelleğiyle
`uv sync` 22 sn, tek seferlik model indirmesi yaklaşık 20 sn, demonun kendisi
yaklaşık 19 sn. İlk çalıştırma bu indirme için ağ ister; indirmeyi ayrı bir adım
yapmak için [Hızlı başlangıç](#-hızlı-başlangıç)'a bakın.

<p align="center"><img src="docs/demo/demo.gif" alt="examples/stdio_demo.py terminal kaydı: initialize, tools/list, index_directory, iki arama" width="720"></p>
<p align="center"><sub>Gerçek çıktı, yeniden oynatılmış. <a href="docs/demo/demo.mp4">MP4</a> · <a href="docs/demo/komutlar.txt">düz metin kayıt</a> (komut, çıktı, çıkış kodu) · yeniden üretmek için <code>python scripts/demo-uret.py</code></sub></p>

Çıktının önemli kısmı, aynen (araçlar yazarın çalışma dili olan Türkçe cevap verir;
`distance` sqlite-vec varsayılanı L2 uzaklığıdır — küçük olan yakındır, 0–1 arası bir puan değildir):

```text
examples/notes indexlendi: 5 dosya (yeni/değişmiş), 0 değişmemiş dosya atlandı, 5 yeni chunk, 0 silinmiş dosya temizlendi.

search_notes("what did I decide about the auth redesign?", top_k=2)
2 sonuç:

--- examples/notes/2026-08-decisions.md:1-10 (distance=4.5000) ---
# Decisions, August 2026

## Auth redesign
Decided: session cookies instead of JWT. The refresh-token rotation story was
getting worse than the problem it solved, and every client we ship is a browser.
[...]

--- examples/notes/meeting-2026-07-30.md:1-7 (distance=5.0842) ---
# Meeting, 30 July
[...]
- Agreed to revisit the login flow after the billing migration ships.
```

Sorgu "auth redesign" diyor; not "session cookies instead of JWT" diyor. Tam kayıttaki
ikinci sorgu ("how do I cook pasta") İngilizce bir soruya Türkçe bir not da buluyor;
çok dilli modelin işi bu.

### Ne zaman kullanılır, ne zaman kullanılmaz

| Kullanın | Kullanmayın |
|---|---|
| bir notun *ne* dediğini hatırlıyor ama yazdığınız kelimeyi hatırlamıyorsanız | tam metni biliyorsanız: `ripgrep` daha hızlı ve kesindir |
| MCP istemcinizin (Claude Code, Claude Desktop, …) kendi klasörlerinizde `dosya:satır` cevaplarıyla arama yapmasını istiyorsanız | paylaşımlı, çok kullanıcılı ya da barındırılan bir index gerekiyorsa: bu tek bir yerel SQLite dosyasıdır, tek yazar |
| notlar makineden çıkmamalıysa ve verecek API anahtarınız yoksa | derlem başına ayarlı sıralama gerekiyorsa: tek sabit embedding modeli, yeniden sıralama yok |
| Türkçe, İngilizce ya da karışık notlar | dosyalarınız PDF, Word ya da görselse: yalnızca metin biçimleri okunur ([liste](#-hızlı-başlangıç)) |

<p align="center">

[![API anahtarı yok](https://img.shields.io/badge/index%20%2B%20arama-API%20anahtar%C4%B1%20yok-3fb950)](#-neden-bu-mimari)
[![Çevrimdışı](https://img.shields.io/badge/retrieval-%25100%20%C3%A7evrimd%C4%B1%C5%9F%C4%B1-3fb950)](#-neden-bu-mimari)
[![Sunucu yok](https://img.shields.io/badge/altyap%C4%B1-s%C4%B1f%C4%B1r%20daemon-3fb950)](#-neden-bu-mimari)
[![Opsiyonel LLM](https://img.shields.io/badge/opsiyonel-LLM%20soru%20cevap-4c8dff)](#-mcp-ara%C3%A7lar%C4%B1)
[![Depolama](https://img.shields.io/badge/depolama-sqlite--vec-003b57?logo=sqlite&logoColor=white)](https://github.com/asg017/sqlite-vec)
[![Embedding](https://img.shields.io/badge/embedding-fastembed%20ONNX-ff6b35)](https://github.com/qdrant/fastembed)

</p>

Sonuç listesi yerine sentezlenmiş bir cevap mı istiyorsunuz? 💡 **`ask_notes`**
tam olarak aynı retrieval'ı çalıştırır, ardından bir LLM'e *sadece bulunan
parçalara dayanarak* cevap verdirir ve `dosya:satır` kaynaklarını ekler.
Tamamen **opsiyoneldir** — `GROQ_API_KEY` ya da `MISTRAL_API_KEY` ayarlıysa
sentezler; hiçbiri yoksa hata vermeden ham eşleşmeleri döner.

Index ve arama için API anahtarı, Docker konteyneri ya da daemon gerekmez. Ağa
çıkabilen tek çağrı, tek seferlik model indirmesidir.

---

## 🧭 30 saniyelik özet

| | grep / ripgrep | Bulut RAG SaaS | **local-notes-search-mcp** |
|---|:---:|:---:|:---:|
| *"session vs JWT"* yazdığınızda *"auth kararı"*nı bulur | ❌ | ✅ | ✅ |
| **API anahtarı olmadan** çalışır | ✅ | ❌ | ✅ |
| Dosyalarınız **makineden hiç çıkmaz** | ✅ | ❌ | ✅ |
| Çalıştırılacak **sunucu / daemon / konteyner yok** | ✅ | ❌ | ✅ |
| Doğrudan atlayabileceğiniz `dosya:satır` döner | ✅ | ⚠️ | ✅ |
| Sorgu başına para harcar | ✅ ücretsiz | ❌ | ✅ ücretsiz |
| Claude / herhangi bir MCP istemcisi doğrudan kullanabilir | ❌ | ⚠️ | ✅ |
| Kaynaklı, temellendirilmiş LLM cevabı | ❌ | ✅ | ✅ opsiyonel |

---

## 🏗️ Nasıl çalışır

```mermaid
flowchart LR
    subgraph INDEX["📥 Index hattı — siz istediğinizde çalışır"]
        direction LR
        A["📁 Yerel klasör"] --> B["🚶 Tara + filtrele<br/>gizli, node_modules,<br/>.venv, &gt; 2MB atlanır"]
        B --> H{"🔐 İçerik hash'i ya da<br/>gömücü değişti mi?"}
        H -- "hayır" --> SKIP["⏭️ Atla<br/>sıfır CPU"]
        H -- "evet" --> C["✂️ Satır bazlı chunker<br/>1500 karakter + 200 overlap<br/>satırı asla bölmez"]
        C --> D["🧠 fastembed ONNX<br/>paraphrase-multilingual-MiniLM-L12-v2 · 384-d"]
    end

    D --> DB[("🗄️ sqlite-vec<br/>vec0 sanal tablo<br/>~/.local-notes-search/index.db")]

    subgraph QUERY["🔍 Sorgu yolu — %100 çevrimdışı"]
        direction LR
        Q["💬 Doğal dilde<br/>soru"] --> QE["🧠 Sorguyu embed et<br/>aynı model"]
    end

    QE --> DB
    DB --> R["🎯 En iyi k chunk<br/>dosya:satır + snippet<br/>+ mesafe skoru"]

    R -. "opsiyonel: ask_notes<br/>API anahtarı gerekir" .-> LLM["🤖 LLM sentezi<br/>Groq → Mistral fallback<br/>sadece bulunan parçalara dayanır"]
    LLM --> ANS["💡 Cevap + dosya:satır kaynakları"]

    style LLM fill:#1c1730,stroke:#a371f7,color:#ffffff
    style ANS fill:#1c1730,stroke:#a371f7,color:#ffffff
    style DB fill:#003b57,stroke:#00b4d8,color:#ffffff
    style R fill:#1a7f37,stroke:#3fb950,color:#ffffff
    style SKIP fill:#4d3800,stroke:#d4a72c,color:#ffffff
```

---

## 🧰 MCP araçları

| 🛠️ Araç | Ne yapar |
|---|---|
| 🗂️ **`index_directory(path, extensions=None)`** | Bir dizini recursive indexler. Gizli dosya ve dizinler (`.git`, `.ssh`, `.config`, `.claude.json`, …), `node_modules` / `venv` / `__pycache__` / `dist` / `build`, ikili dosyalar, normal dosya olmayan her şey ve 2 MB üzeri dosyalar atlanır. Değişmemiş dosyalar ucuz bir hash kontrolüyle atlanır — başka bir model ya da fastembed sürümüyle embed edilmişlerse yeniden embed edilir; silinmiş (ya da artık okunamayan) dosyalar index'ten temizlenir. |
| 🔍 **`search_notes(query, top_k=5, path_prefix=None)`** | Doğal dilde anlamsal arama. `dosya:satır-aralığı` + snippet + mesafe skoru döner — sadece bir dosya adı yığını değil. `path_prefix` aramayı tek bir alt ağaca daraltır. `top_k` 1–50 arası. |
| 💡 **`ask_notes(question, top_k=5, path_prefix=None)`** | *Opsiyonel.* `search_notes` ile aynı retrieval, ardından bir LLM (Groq → Mistral fallback) **sadece** o parçaları kullanarak cevap üretir ve altına `dosya:satır` kaynak listesi ekler. `GROQ_API_KEY` ya da `MISTRAL_API_KEY` gerekir. İkisi de yoksa — ya da sağlayıcı zinciri başarısız olursa — ham eşleşmeleri bir notla döner. Sentez mümkün olmadı diye asla sert bir hata vermez. |
| 📋 **`list_indexed_files(path_prefix=None)`** | Şu an index'te ne var: yol, chunk sayısı, son indexlenme zamanı (ilk 200 dosya, kalanların sayısıyla); başka bir model/fastembed sürümüyle embed edilmiş dosyalar işaretlenir. Aramadan önce kapsamı görmek ya da bayat bir sonucu debug etmek için. |
| 🧹 **`remove_directory(path)`** | `path` altındaki her şeyi index'ten düşürür. **Dosyalarınızı silmez** — sadece index'i temizler. |

> 🔒 **`index_directory`, `search_notes`, `list_indexed_files` ve `remove_directory`
> hiçbir API anahtarı istemez ve hiçbir yere veri göndermez.** Yapabilecekleri
> tek ağ çağrısı, Hızlı başlangıç'ta anlatılan tek seferlik model indirmesidir
> — onu da `LOCAL_NOTES_SEARCH_OFFLINE=1` ile tamamen kapatabilirsiniz. Uzak bir
> sağlayıcıyla konuşabilen tek araç `ask_notes`, o da yalnızca siz açıkça bir
> anahtar verdiğinizde.

---

## 🚀 Hızlı başlangıç

[uv](https://docs.astral.sh/uv/) ve Python 3.10–3.13 gerekir.

```bash
git clone https://github.com/Furkiozknn/local-notes-search-mcp.git
cd local-notes-search-mcp
uv sync
```

Yukarıdaki demo her şeyin çalıştığını görmenin en hızlı yoludur. Model indirilemezse
çözümü söyleyen bir hata basar (ağ erişimi ya da `--download-model` +
`LOCAL_NOTES_SEARCH_OFFLINE`); `uv run local-notes-search-mcp --help` araçları,
ortam değişkenlerini ve varsayılan yolları listeler.

<details>
<summary><b>🔌 MCP istemcinize bağlayın (Claude Code, Claude Desktop, …)</b></summary>

<br/>

`local_notes_search.py`'yi **stdio** MCP sunucusu olarak tanımlayın:

```json
{
  "mcpServers": {
    "local-notes-search": {
      "command": "uv",
      "args": [
        "--directory", "/mutlak/yol/local-notes-search-mcp",
        "run", "local_notes_search.py"
      ]
    }
  }
}
```

**Model indirmesi, açıkça.** Embedding modeli pakete gömülü değil. Önbellekte
yoksa ilk `index_directory` / `search_notes` çağrısı onu Hugging Face'ten
(fastembed'in listesinde 0.22 GB) `~/.local-notes-search/models` içine indirir;
sonraki tüm çağrılar çevrimdışıdır. Bu indirmeyi bir yan etki değil, açık bir
adım yapmak için:

```bash
uv run local-notes-search-mcp --download-model   # bir kez, ağ varken
export LOCAL_NOTES_SEARCH_OFFLINE=1              # bundan sonra: asla indirme
```

`LOCAL_NOTES_SEARCH_OFFLINE=1` ayarlıyken model önbellekte yoksa araç ağa
çıkmak yerine tam olarak bunu söyleyen bir hatayla durur.

</details>

<details>
<summary><b>⚙️ Yapılandırma</b></summary>

<br/>

| Ortam değişkeni | Varsayılan | Ne işe yarar |
|---|---|---|
| `LOCAL_NOTES_SEARCH_DB` | `~/.local-notes-search/index.db` | Index'in konumu. **Tüm indexlenen dizinler için TEK bir dosya** — böylece tek bir `search_notes` çağrısı bütün proje klasörlerinizi birden tarayabilir. |
| `LOCAL_NOTES_SEARCH_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | Embedding modeli (fastembed'in desteklediği herhangi bir ad). Başka modelle kurulmuş bir index sessizce karşılaştırılmaz, reddedilir. |
| `LOCAL_NOTES_SEARCH_MODEL_DIR` | `~/.local-notes-search/models` | Model önbelleği. Ayarlıysa `FASTEMBED_CACHE_PATH`'e düşer. (fastembed'in kendi varsayılanı sistemin geçici dizini — yeniden başlatmada silinir ve model sonra tekrar indirilir.) |
| `LOCAL_NOTES_SEARCH_OFFLINE` | *ayarsız* | `1` her türlü model indirmesini yasaklar; model önceden önbellekte olmalı (`--download-model`). |
| `LOCAL_NOTES_SEARCH_ALLOWED_ROOTS` | *ayarsız* | Opsiyonel izin listesi. Ayarlandığında `index_directory`, bu dizinlerin içine çözülmeyen hiçbir yolu kabul etmez. `os.pathsep` ile ayrılır (Linux/macOS'ta `:`, Windows'ta `;`). |
| `GROQ_API_KEY` | *ayarsız* | Opsiyonel. `ask_notes` sentezini Groq üzerinden açar (zincirdeki ilk sağlayıcı). |
| `MISTRAL_API_KEY` | *ayarsız* | Opsiyonel. Groq ayarsızsa ya da başarısız olursa `ask_notes` için fallback sağlayıcı. |

Anahtarlar yalnızca ortam değişkeninden okunur — **asla commit etmeyin ve git'e
girecek bir MCP istemci config dosyasına yazmayın.**

Varsayılan indexlenen uzantılar: `.md` `.txt` `.py` `.js` `.ts` `.tsx` `.jsx`
`.json` `.yaml` `.yml` `.rst` `.toml` — çağrı başına `extensions=[...]` ile
değiştirilebilir.

### 🔒 Ne indexlenebilir

`index_directory` kendisine verilen her yolu okur; `ask_notes` ise bir anahtar
yapılandırılmışsa getirdiği parçaları **üçüncü taraf bir LLM'e** (Groq ya da
Mistral) gönderir. Yani indexlenen bir yol, içeriği makineden çıkabilecek bir
yoldur. Şu korumalar var:

**1. `LOCAL_NOTES_SEARCH_ALLOWED_ROOTS` (opsiyonel).** Varsayılan olarak
ayarsız — yani eski davranış: kullanıcının okuyabildiği her dizin
indexlenebilir. Boş bir varsayılan bir kum havuzu değildir ve bu proje öyle
olduğunu iddia etmez. Ayarladığınızda `index_directory` dışarıdaki her yolu
reddeder:

```bash
export LOCAL_NOTES_SEARCH_ALLOWED_ROOTS="$HOME/notlar:$HOME/projeler"
```

Yollar kontrolden önce çözülür (`..` sadeleştirilir, sembolik bağlantılar
izlenir) ve izin verilen bir kökün alt dizini de kabul edilir. Dizin olmayan
bir giriş sessizce atılmak yerine hata verir — bir yazım hatası izin listesini
sessizce kapatmamalı.

**2. Kimlik bilgisi dosya adı kara listesi (her zaman açık).** Aşağıdakiler izin
listesinden ya da `extensions=[...]` argümanından bağımsız olarak asla
indexlenmez:

`.env` · `.env.*` · `.netrc` · `_netrc` · `id_rsa` · `id_dsa` · `id_ecdsa` ·
`id_ed25519` · `credentials.json` · `secrets.json` · `service-account*.json` ·
`.npmrc` · `.pypirc` · `.git-credentials` · `*.pem` · `*.key` · `*.p12` ·
`*.pfx` · `*.ppk`

Eşleşme büyük/küçük harf duyarsızdır. Bu bir **ad** kara listesidir, gizli
bilgi tarayıcısı değil: bariz durumları engeller (`credentials.json` aksi halde
varsayılan `.json` uzantı filtresinden geçerdi), bir `.md` dosyasına
yapıştırılmış anahtarı değil.

**3. Gizli dosya ve dizinler taranmaz (her zaman açık).** `.ssh`, `.aws`,
`.gnupg`, `.docker` (`config.json` registry kimlik bilgisini tutar), `.config`
(`gh` OAuth jetonunu `hosts.yml`'de saklar), `~/.claude.json` (MCP sunucu
ayarları, `env` içindeki API anahtarları dahil) — `index_directory`'yi ev
dizinine yöneltmek bunları `.json` / `.yml` filtresinden içeri almamalı.
Gerçekten indexlemek istediğiniz gizli bir dizini doğrudan `path` olarak
verebilirsiniz. Yalnızca normal dosyalar okunur: `*.md` adlı bir FIFO ya da
aygıt dosyası atlanır (FIFO eskiden tüm çağrıyı kilitliyordu) ve bir dosya,
taramadan sonra büyümüş olsa bile 2 MB'tan fazla okunmaz.

**4. Sembolik bağlantılar kökten çıkamaz (her zaman açık).** Sembolik bağlantı
olan bir dosya, yalnızca indexlenen dizinin içinde, atlanan bir dizinde olmayan
ve kara listedeki bir ada sahip olmayan normal bir dosyaya çözülüyorsa
indexlenir — yani `notlar/todo.md → ~/.aws/credentials` masum bir adla
okunmak yerine atlanır; ne kara listeyi ne izin listesini delebilir. Dizin
bağlantıları hiç izlenmez.

### 🗄️ Index'te ne saklanıyor

Index dosyası, indexlenen her chunk için **tam metnini düz metin olarak**,
yolunu, satır aralığını ve vektörünü tutar. Notlarınıza işaret eden bir liste
değil, notlarınızın bir kopyasıdır. Linux/macOS'ta yalnızca sahibi okuyabilecek
şekilde oluşturulur (`0600`, `0700` bir `~/.local-notes-search/` içinde; eski
bir sürümün oluşturduğu index bir sonraki açılışta sıkılaştırılır). Dosyayı
silin — ya da `remove_directory` kullanın — kopya gider; asıl dosyalarınıza
hiçbir zaman dokunulmaz.

Her indexlenen dosya, vektörlerini **hangi gömücünün ürettiğini** de kaydeder:
model adı *ve* fastembed sürümü. Bu önemli, çünkü fastembed 0.6.0 bu modelin
pooling'ini değiştirdi (CLS → mean); öncesi ve sonrasının vektörleri
karşılaştırılamaz. Bir yükseltmeden sonra `search_notes` / `ask_notes` kaç
dosyanın bayat olduğunu söyleyen bir uyarıyla başlar, `list_indexed_files`
bunları işaretler ve o klasörlerde bir sonraki `index_directory`, içerikleri
değişmemiş olsa da onları yeniden embed eder. Bu kayıttan önce oluşturulmuş
bir index de aynı şekilde ele alınır.

</details>

---

## 🧠 Neden bu mimari

| Karar | Neden |
|---|---|
| 🗄️ **Vektör depolama için `sqlite-vec` (Apache-2.0)** | Sıradan tek bir `.sqlite` dosyasının içinde bir `vec0` sanal tablosu — **daemon yok, Docker yok, hosted servis yok**. Qdrant ve pgvector değerlendirildi ve *tam olarak* ikisi de çalışan bir sunucu süreci gerektirdiği için elendi. Kişisel bir not index'i ops gerektirmemeli. |
| ⚡ **`sentence-transformers` değil, `fastembed` (Apache-2.0)** | Burada yerel embedding **tek** yol — her index ve her aramada çalışıyor. `sentence-transformers` torch'u (~1 GB) beraberinde getiriyor; bu, nadiren çalışan bir fallback için kabul edilebilir bir bedel, ama sıcak yol için değil. fastembed'in kuantize ONNX modelleri **torch olmadan ~100–150 MB** bandında kalıyor. Bilinçli bir ayrışma, modül docstring'inde belgelendi. |
| 🧬 **`paraphrase-multilingual-MiniLM-L12-v2`, 384 boyut** | Küçük (0.22 GB), Apache-2.0 ve — bu araç için belirleyici olan — **gerçekten çok dilli**: önceki `bge-small-en-v1.5` yalnızca İngilizce bir modeldi ve Türkçe notları sessizce embed ediyordu. Simetrik: sorgu ve metin aynı şekilde embed edilir. `LOCAL_NOTES_SEARCH_MODEL` ile değiştirilebilir; farklı modelle kurulmuş bir index sessizce karşılaştırılmaz, reddedilir. **GPU gerekmiyor.** |
| ✂️ **Satır bazlı chunking, NLP/AST bağımlılığı yok** | Chunk'lar bir karakter bütçesi dolana kadar tam satırlar biriktirir — **bir satır asla ortadan bölünmez**, böylece dönen her `dosya:satır` referansı birebir doğrudur. Overlap penceresi, chunk sınırında bağlamın kopmasını engeller. Deterministik ve embedding modeli hiç yüklenmeden tamamen unit-test edilebilir. |
| 🔐 **Re-index'te tam dosya içerik-hash atlaması** | `index_directory` sık sık yeniden çalıştırılmak üzere tasarlandı. Değişmemiş dosyaları tekrar embed etmek her çağrıda boşuna CPU yakardı — tek bir ucuz hash karşılaştırması bunu önlüyor. |

---

## 🧪 Testler

```bash
uv run pytest -v
```

**99 test, bilinçli iki katmanlı bir strateji üzerine.** Saf mantık testleri
(chunking, hash, dosya tarama, `ask_notes`'un sağlayıcı zinciri ve
degradasyon yolları) her zaman çalışır — model yok, ağ yok, API anahtarı yok.
Gerçek fastembed modelini veya sqlite-vec eklentisini gerektiren testler,
bunlar yüklenemiyorsa (çevrimdışı runner, engellenmiş model indirmesi)
**dürüstçe skip edilir** — sahte bir yeşil sonuç göstermek yerine.

Pratikte ne anlama geldiği, ölçüldüğü gibi:

| Ortam | Sonuç |
|---|---|
| ✅ CI (model önbellekte ve *zorunlu*: model yoksa testler skip olmaz, iş kırmızı yanar; Python 3.10, 3.11, 3.12 ve 3.13) | **99 geçti**, dördünde de — 25 Eylül 2026'da ölçüldü — gerçek uçtan uca akış dahil — fastembed modeli gerçekten yüklendi, sqlite-vec eklentisi gerçekten çalıştı ve *"how do I cook pasta"* sorgusu gerçekten tarif notunu buldu, araba bakımı notunu değil. |
| ⚠️ Model indirmesi engellenmiş bir sandbox | **85 geçti, 14 skip** — 25 Eylül 2026'da ölçüldü. Modele ihtiyaç duymayan her test yeşil; modele dayananlar ise sahte bir geçiş yerine açık bir gerekçeyle skip edildi. |

İkinci satır, birincinin dürüst bedeli: bu suite, bir şeyi *doğrulayamadığında*
size bunu söylüyor.

---

## ⚠️ Bilinen sınırlamalar

Bilerek yazıldı, çünkü hiçbir zaafı olmadığını iddia eden bir README güvenilmez
bir README'dir.

- **Sorgu prefix'i artık gerekmiyor.** Önceki, yalnızca İngilizce
  `bge-small-en-v1.5` sorguların bir instruction prefix'iyle embed edilmesini
  öneriyordu ve bu araç bunu v1 sadeleştirmesi olarak atlıyordu. Şimdiki
  varsayılan `paraphrase-multilingual-MiniLM-L12-v2` simetrik bir model:
  sorgu ve metnin aynı şekilde embed edilmesi zaten doğru kullanım.
  `LOCAL_NOTES_SEARCH_MODEL` ile asimetrik bir model (BGE/E5 ailesi) seçerseniz
  onun prefix kuralının hâlâ uygulanmadığını bilin.
- **`path_prefix` vektör aramasından sonra süzer, arama içinde değil.** Arama
  tüm index'ten `4 × top_k` en yakın chunk'ı getirir, sonra prefix altındakileri
  tutar; büyük bir index'te dar bir prefix, eşleşen daha fazla chunk olsa bile
  `top_k`'dan az sonuç döndürebilir.
- **Tek yazarlı SQLite.** *Ayrı süreçlerden* gelen eşzamanlı
  `index_directory` / `search_notes` çağrıları yazmada çakışabilir. Araç tek
  bir MCP istemci oturumu etrafında tasarlandı.

---

## 📓 Değişiklik günlüğü

[CHANGELOG.md](CHANGELOG.md) (İngilizce).

## 📜 Lisans

[MIT](LICENSE) — ve her çalışma zamanı bağımlılığı lisans açısından kontrol
edildi: `sqlite-vec` (Apache-2.0), `fastembed` (Apache-2.0), `mcp` (MIT),
`litellm` (MIT). Yığının hiçbir yerinde ticari kullanımı kısıtlayan bir
model/ağırlık yok.

<div align="center">
<br/>

**Küçük, odaklı ve kendi sunucunuzda çalıştırabileceğiniz AI araçlarından oluşan bir ekosistemin parçası.**

</div>
