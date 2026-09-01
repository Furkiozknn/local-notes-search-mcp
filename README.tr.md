<div align="center">

<img src="assets/banner.svg" alt="local-notes-search-mcp - kendi dosyalarinizda anlamsal arama, bir MCP sunucusu olarak" width="100%">

# 🔎 local-notes-search-mcp

### **Kendi dosyalarınızda anlamsal arama — bir MCP sunucusu olarak.**

*Altı ay önce tam olarak hangi kelimeyi yazdığınızı hatırlamaya çalışmak yerine, doğal dilde sorun.*

<br/>

[![CI](https://github.com/Furkiozknn/local-notes-search-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Furkiozknn/local-notes-search-mcp/actions/workflows/ci.yml)
[![Testler](https://img.shields.io/badge/testler-37-3fb950?logo=pytest&logoColor=white)](tests/)
[![Lisans: MIT](https://img.shields.io/badge/lisans-MIT-8957e5)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](.python-version)
[![MCP](https://img.shields.io/badge/MCP-sunucu-000000?logo=anthropic&logoColor=white)](https://modelcontextprotocol.io)

[![API anahtarı yok](https://img.shields.io/badge/index%20%2B%20arama-API%20anahtar%C4%B1%20yok-3fb950)](#-neden-bu-mimari)
[![Çevrimdışı](https://img.shields.io/badge/retrieval-%25100%20%C3%A7evrimd%C4%B1%C5%9F%C4%B1-3fb950)](#-neden-bu-mimari)
[![Sunucu yok](https://img.shields.io/badge/altyap%C4%B1-s%C4%B1f%C4%B1r%20daemon-3fb950)](#-neden-bu-mimari)
[![Opsiyonel LLM](https://img.shields.io/badge/opsiyonel-LLM%20soru%20cevap-4c8dff)](#-mcp-ara%C3%A7lar%C4%B1)
[![Depolama](https://img.shields.io/badge/depolama-sqlite--vec-003b57?logo=sqlite&logoColor=white)](https://github.com/asg017/sqlite-vec)
[![Embedding](https://img.shields.io/badge/embedding-fastembed%20ONNX-ff6b35)](https://github.com/qdrant/fastembed)

<br/>

**🇬🇧 [English README →](README.md)**

</div>

---

## ✨ Ne yapar

Bir klasörü gösterin — proje notlarınız, dağınık bir `Claude projeler/` ağacı,
bir dokümantasyon dizini — ve içinde **anlama göre** arayın, birebir kelime
eşleşmesine göre değil.

```text
▸ index_directory("C:/Users/siz/Desktop/notlar")
  ✅ 128 dosya indexlendi · 941 chunk · 12 değişmemiş (atlandı)

▸ search_notes("auth yeniden tasarımı hakkında ne karar vermiştim?")
  🎯 3 sonuç:

  ── notlar/2026-08-kararlar.md:12-24  ·  mesafe 0.31 ────────────────
     ## Auth yeniden tasarımı
     Kararlaştırdık: JWT yerine session-based, çünkü refresh-token
     rotasyonu çözdüğü problemden daha kötü hale gelmişti...

  ── notlar/toplanti-2026-07-30.md:88-101  ·  mesafe 0.44 ────────────
     ...auth konusunu billing migration bittikten sonra tekrar açacağız.
```

Bu akışın hiçbir adımı ağa çıkmadı. OpenAI anahtarı yok, Pinecone hesabı yok,
Docker konteyneri yok, kendi notlarınızda arama yapmak için önce
`docker compose up` yazmanız gerekmiyor.

Sonuç listesi yerine sentezlenmiş bir cevap mı istiyorsunuz? 💡 **`ask_notes`**
tam olarak aynı retrieval'ı çalıştırır, ardından bir LLM'e *sadece bulunan
parçalara dayanarak* cevap verdirir ve `dosya:satır` kaynaklarını ekler.
Tamamen **opsiyoneldir** — `GROQ_API_KEY` ya da `MISTRAL_API_KEY` ayarlıysa
sentezler; hiçbiri yoksa hata vermeden ham eşleşmeleri döner.

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
        A["📁 Yerel klasör"] --> B["🚶 Tara + filtrele<br/>.git, node_modules,<br/>.venv, &gt; 2MB atlanır"]
        B --> H{"🔐 İçerik hash'i<br/>değişti mi?"}
        H -- "hayır" --> SKIP["⏭️ Atla<br/>sıfır CPU"]
        H -- "evet" --> C["✂️ Satır bazlı chunker<br/>1500 karakter + 200 overlap<br/>satırı asla bölmez"]
        C --> D["🧠 fastembed ONNX<br/>bge-small-en-v1.5 · 384-d"]
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
| 🗂️ **`index_directory(path, extensions=None)`** | Bir dizini recursive indexler. `.git` / `node_modules` / `.venv` / `__pycache__` / `dist` / `build` ve 2 MB üzeri dosyalar atlanır. Değişmemiş dosyalar ucuz bir hash kontrolüyle atlanır; silinmiş dosyalar index'ten temizlenir. |
| 🔍 **`search_notes(query, top_k=5, path_prefix=None)`** | Doğal dilde anlamsal arama. `dosya:satır-aralığı` + snippet + mesafe skoru döner — sadece bir dosya adı yığını değil. `path_prefix` aramayı tek bir alt ağaca daraltır. |
| 💡 **`ask_notes(question, top_k=5, path_prefix=None)`** | *Opsiyonel.* `search_notes` ile aynı retrieval, ardından bir LLM (Groq → Mistral fallback) **sadece** o parçaları kullanarak cevap üretir ve altına `dosya:satır` kaynak listesi ekler. `GROQ_API_KEY` ya da `MISTRAL_API_KEY` gerekir. İkisi de yoksa — ya da sağlayıcı zinciri başarısız olursa — ham eşleşmeleri bir notla döner. Sentez mümkün olmadı diye asla sert bir hata vermez. |
| 📋 **`list_indexed_files(path_prefix=None)`** | Şu an index'te ne var: yol, chunk sayısı, son indexlenme zamanı. Aramadan önce kapsamı görmek ya da bayat bir sonucu debug etmek için. |
| 🧹 **`remove_directory(path)`** | `path` altındaki her şeyi index'ten düşürür. **Dosyalarınızı silmez** — sadece index'i temizler. |

> 🔒 **`index_directory`, `search_notes`, `list_indexed_files` ve `remove_directory`
> hiçbir API anahtarı istemez ve hiç ağa çıkmaz.** Uzak bir sağlayıcıyla
> konuşabilen tek araç `ask_notes`, o da yalnızca siz açıkça bir anahtar
> verdiğinizde.

---

## 🚀 Hızlı başlangıç

```bash
git clone https://github.com/Furkiozknn/local-notes-search-mcp.git
cd local-notes-search-mcp
uv sync
```

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

**İlk** `index_directory` / `search_notes` çağrısında fastembed modeli
(~130 MB) bir kez indirilip yerel olarak cache'lenir. Sonraki tüm çağrılar
tamamen çevrimdışı çalışır.

</details>

<details>
<summary><b>⚙️ Yapılandırma</b></summary>

<br/>

| Ortam değişkeni | Varsayılan | Ne işe yarar |
|---|---|---|
| `LOCAL_NOTES_SEARCH_DB` | `~/.local-notes-search/index.db` | Index'in konumu. **Tüm indexlenen dizinler için TEK bir dosya** — böylece tek bir `search_notes` çağrısı bütün proje klasörlerinizi birden tarayabilir. |
| `GROQ_API_KEY` | *ayarsız* | Opsiyonel. `ask_notes` sentezini Groq üzerinden açar (zincirdeki ilk sağlayıcı). |
| `MISTRAL_API_KEY` | *ayarsız* | Opsiyonel. Groq ayarsızsa ya da başarısız olursa `ask_notes` için fallback sağlayıcı. |

Anahtarlar yalnızca ortam değişkeninden okunur — **asla commit etmeyin ve git'e
girecek bir MCP istemci config dosyasına yazmayın.**

Varsayılan indexlenen uzantılar: `.md` `.txt` `.py` `.js` `.ts` `.tsx` `.jsx`
`.json` `.yaml` `.yml` `.rst` `.toml` — çağrı başına `extensions=[...]` ile
değiştirilebilir.

</details>

---

## 🧠 Neden bu mimari

| Karar | Neden |
|---|---|
| 🗄️ **Vektör depolama için `sqlite-vec` (Apache-2.0)** | Sıradan tek bir `.sqlite` dosyasının içinde bir `vec0` sanal tablosu — **daemon yok, Docker yok, hosted servis yok**. Qdrant ve pgvector değerlendirildi ve *tam olarak* ikisi de çalışan bir sunucu süreci gerektirdiği için elendi. Kişisel bir not index'i ops gerektirmemeli. |
| ⚡ **`sentence-transformers` değil, `fastembed` (Apache-2.0)** | Burada yerel embedding **tek** yol — her index ve her aramada çalışıyor. `sentence-transformers` torch'u (~1 GB) beraberinde getiriyor; bu, nadiren çalışan bir fallback için kabul edilebilir bir bedel, ama sıcak yol için değil. fastembed'in kuantize ONNX modelleri **torch olmadan ~100–150 MB** bandında kalıyor. Bilinçli bir ayrışma, modül docstring'inde belgelendi. |
| 🧬 **`BAAI/bge-small-en-v1.5`, 384 boyut** | Küçük, hızlı, izin verici lisanslı, genel amaçlı. Kişisel not ölçeği için fazlasıyla yeterli. **GPU gerekmiyor.** |
| ✂️ **Satır bazlı chunking, NLP/AST bağımlılığı yok** | Chunk'lar bir karakter bütçesi dolana kadar tam satırlar biriktirir — **bir satır asla ortadan bölünmez**, böylece dönen her `dosya:satır` referansı birebir doğrudur. Overlap penceresi, chunk sınırında bağlamın kopmasını engeller. Deterministik ve embedding modeli hiç yüklenmeden tamamen unit-test edilebilir. |
| 🔐 **Re-index'te tam dosya içerik-hash atlaması** | `index_directory` sık sık yeniden çalıştırılmak üzere tasarlandı. Değişmemiş dosyaları tekrar embed etmek her çağrıda boşuna CPU yakardı — tek bir ucuz hash karşılaştırması bunu önlüyor. |

---

## 🧪 Testler

```bash
uv run pytest -v
```

**37 test, bilinçli iki katmanlı bir strateji üzerine.** Saf mantık testleri
(chunking, hash, dosya tarama, `ask_notes`'un sağlayıcı zinciri ve
degradasyon yolları) her zaman çalışır — model yok, ağ yok, API anahtarı yok.
Gerçek fastembed modelini veya sqlite-vec eklentisini gerektiren testler,
bunlar yüklenemiyorsa (çevrimdışı runner, engellenmiş model indirmesi)
**dürüstçe skip edilir** — sahte bir yeşil sonuç göstermek yerine.

Pratikte ne anlama geldiği, ölçüldüğü gibi:

| Ortam | Sonuç |
|---|---|
| ✅ Orijinal build ortamı (model indirilebiliyor) | **25 / 25 geçti**, gerçek uçtan uca akış dahil — fastembed modeli gerçekten yüklendi, sqlite-vec eklentisi gerçekten çalıştı ve *"pasta nasıl pişirilir"* araması gerçekten alakalı dosyayı bulup alakasız dosyayı hariç tuttu. |
| ⚠️ Model indirmesi engellenmiş bir sandbox | **24 geçti, 13 skip** — modele ihtiyaç duymayan her test yeşil; modele dayanan 13 test ise sahte bir geçiş yerine açık bir gerekçeyle skip edildi. |

İkinci satır, birincinin dürüst bedeli: bu suite, bir şeyi *doğrulayamadığında*
size bunu söylüyor.

---

## ⚠️ Bilinen sınırlamalar

Bilerek yazıldı, çünkü hiçbir zaafı olmadığını iddia eden bir README güvenilmez
bir README'dir.

- **BGE'nin asimetrik query-instruction prefix'i kullanılmıyor.**
  `bge-small-en-v1.5` gibi modeller *sorguyu*, belgelerden farklı olarak bir
  instruction prefix'iyle (`"Represent this sentence for searching relevant
  passages: "`) embed etmeyi önerir. Bu araç ikisini de aynı şekilde embed
  ediyor — arama kalitesinden bir miktar götüren, bilinçli bir v1
  sadeleştirmesi. fastembed'in bu modele özel API yüzeyi (`query_embed`
  benzeri) **canlı doğrulanmadan, hafızadan bir isimle** kullanılmak istenmedi.
- **CI'da fastembed modeli her çalıştırmada yeniden indiriliyor**
  (`actions/cache` yapılandırılmadı). Küçük bir proje için kabul edilebilir,
  sonradan kolayca hızlandırılabilir. Düşük öncelikli ve yapılmadığı dürüstçe
  belirtiliyor.
- **Tek yazarlı SQLite.** *Ayrı süreçlerden* gelen eşzamanlı
  `index_directory` / `search_notes` çağrıları yazmada çakışabilir. Araç tek
  bir MCP istemci oturumu etrafında tasarlandı.

---

## 📜 Lisans

[MIT](LICENSE) — ve her çalışma zamanı bağımlılığı lisans açısından kontrol
edildi: `sqlite-vec` (Apache-2.0), `fastembed` (Apache-2.0), `mcp` (MIT),
`litellm` (MIT). Yığının hiçbir yerinde ticari kullanımı kısıtlayan bir
model/ağırlık yok.

<div align="center">
<br/>

**Küçük, odaklı ve kendi sunucunuzda çalıştırabileceğiniz AI araçlarından oluşan bir ekosistemin parçası.**

</div>
