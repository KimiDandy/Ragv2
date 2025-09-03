
# Genesis-RAG — Master Plan & Implementation Guide (Branch `main`)
**Mode:** KIMI  
**Audience:** Cascade (executor), Kimi (planner)  
**Style:** ID for explanations; EN for code/prompt

> Dokumen ini adalah **satu sumber kebenaran (SSOT)** untuk arsitektur, alur, dan parameter RAG skala besar (hingga ≥12.000 halaman / 50.000–100.000 paragraf). Isinya mencakup konsep, detail teknis, kontrak data, prompt final, parameter awal, metrik, dan checklist PR untuk eksekusi bertahap **tanpa Docker/Redis** terlebih dahulu.

---

## 0) Tujuan & Prinsip

### Tujuan
- **Responsif** untuk dokumen raksasa (≥12k halaman) di mesin pengembangan.
- **Hemat biaya**: LLM dipanggil **hanya** untuk bagian paling penting (budget-aware).
- **Transparan**: semua jawaban punya **evidence** dan panel Enhancement yang **paging / infinite scroll**.
- **Stabil**: tidak “hang” saat batch besar (checkpointing & streaming progress).

### Prinsip (ringkas)
1. **Route → Narrow → Enrich on demand** (bukan enrich semua di awal).
2. **Hybrid hierarchical retrieval**: BM25 → Shard (bab/tema) → Dense → Rerank.
3. **Strict budgets** (token/waktu/jumlah item) + **Early exit** saat yakin.
4. **Dedup** di semua level (boilerplate/disclaimer).
5. **Asynchronous & resumable**: partial result disimpan per batch, UI bisa streaming.
6. **Cache** lokal (SQLite) + **glossary reuse** lintas dokumen.
7. **UI-friendly**: panel Enhancement dengan paging, evidence ranking, tombol “Stop & Finalize”.

---

## 1) Arsitektur End-to-End (aluran besar)

```
PDF → [F0 Ingest (tanpa LLM)]
         ├─ Segmentasi + Metadata
         ├─ BM25 Index (keyword)
         └─ Sharding Bab/Topik (header_path + centroid embedding kecil)
→ [F1 Planner Hemat]
         ├─ Gating non-LLM (Top-K global + kuota per shard)
         ├─ LLM Skim singkat (≤ 2 kandidat/segmen)
         └─ Reduce (dedup/cluster → 200–300 item) → plan.json
→ [F2 Enrichment]
         ├─ Eager: Top 50–100 item (sketch→refine; konteks ≤ 1000 chars)
         └─ Lazy: on-demand saat chat + cache
→ [Retrieval/Chat]
         ├─ Router: BM25 + Shard (≤ 5 shard)
         ├─ Dense retrieval di shard (k≈50)
         ├─ Rerank ringan (top≈10)
         └─ LLM Answer + Evidence + (opsional lazy-enrich)
→ [UI]
         ├─ Panel Enhancement: paging/infinite scroll + streaming progress
         └─ Evidence ranking & provenance
```

---

## 2) Fase-Fase & Detail Teknis

### F0 — Ingest Cepat (tanpa LLM)
**Tujuan:** menyiapkan artefak murah & kaya metadata agar fase berikutnya hemat.

**Langkah:**
- Ekstrak teks per halaman → segmentasi per paragraf.
- Simpan metadata per segmen:
  - `id`, `page`, `char`: `[start, end]`, `header_path`: `["H1","H2",...]`
  - `flags`: `{contains_entities, is_difficult}` (regex angka, instansi, jabatan; heuristik kalimat panjang)
  - `numeric_ratio` (proporsi digit), `hash` (sha256 dari `text`)
- Bangun **BM25 index** dari `text` (Whoosh/lucene-lite cukup).
- **Sharding**: grupkan segmen per `header_path` (bab/section), hitung **centroid embedding kecil** (mis. e5-small).

**Artefak:**
- `artefacts/{doc_id}/segments.json`
- `artefacts/{doc_id}/shards.json`
- (opsional) `section_summaries.json` (lihat Poin-6)

**Contoh schema (ringkas):**
```json
{
  "id":"seg_00001",
  "page":3,
  "char":[120,580],
  "header_path":["Ringkasan","Tujuan Penawaran"],
  "text":"...",
  "flags":{"contains_entities":true,"is_difficult":false},
  "numeric_ratio":0.18,
  "hash":"sha256:..."
}
```
```json
{
  "id":"shard_001",
  "title":"Bab II - Ketentuan Umum",
  "segment_ids":["seg_00031","seg_00032"],
  "centroid":[0.012,-0.004,...]
}
```

---

### F1 — Planner Hemat (Gating → Skim → Reduce)
**Tujuan:** menghasilkan **plan kecil** (200–300 item) dari puluhan-ratusan ribu segmen **tanpa meledak**.

**Gating non-LLM (ketat + merata):**
- Skor segmen (heuristik sederhana):
  ```text
  score = 1.2*contains_entities
        + 0.8*is_difficult
        + 0.6*header_weight
        + 0.4*numeric_ratio
        + 0.4*tfidf_glossary_score   (opsional)
  ```
- **Top-K global**: `min(2000, ceil(0.015 × N_segments))`
- **Kuota per shard**: maks 8–10 segmen/shard (10 untuk “Definisi/Ketentuan”)

**LLM “Skim” super-pendek:**
- Minta **≤ 2 kandidat** per segmen (istilah/konsep) — *boleh kosong*.
- Runtime guard: `max_out ≈ 40`, `timeout ≈ 8s`, `retry=1`, **RPS limiter (3–5)**,
  **token budget** (≈ 40k) dan **stop-when-budget (<10%)**.

**Prompt Planner (FINAL)**

**SYSTEM**
```
You are a careful planner. Work ONLY with the provided SEGMENT.
Return STRICT JSON. No markdown. If nothing useful, return empty arrays.
```

**USER**
```
SEGMENT:
{segment_text}

OUTPUT:
{
  "segment_hash": "{segment_hash}",
  "terms_to_define": [{"label": "...", "confidence": 0.xx}],
  "concepts_to_simplify": [{"label": "...", "confidence": 0.xx}]
}
RULES:
- ≤ 2 items per array
- labels ≤ 6 words
- confidence ∈ [0,1]
- If none, both arrays empty.
```

**Reduce (dedup & clustering):**
- Normalize label → lemma key (lowercase, strip punctuation).
- **Embed label unik** → cluster sinonim (cosine ≥ 0.82).
- Skor global: `frequency + 0.7*centrality(header/shard) + 0.3*numeric_bias`.
- Ambil **200–300 item** (gabungan istilah/konsep), terurut → `plan.json`.

**Output (contoh):**
```json
{
  "terms_to_define":[
    {"label":"BI-Rate","score":0.93,"provenances":[{"seg_id":"seg_00021","page":4,"header_path":["Definisi"]}]}],
  "concepts_to_simplify":[
    {"label":"Risiko Pasar","score":0.88,"provenances":[{"seg_id":"seg_03412","page":120,"header_path":["Risiko"]}]}]
}
```

---

### F2 — Enrichment (Progressive & Lazy)
**Tujuan:** memperkaya **sedikit dulu** agar cepat usable; sisanya *on-demand* saat chat.

**Eager (langsung):**
- Target: **Top 50–100** item teratas dari `plan.json`.
- Dua langkah:
  - **Sketch** semua target (`max_out ≈ 120`)
  - **Refine** **Top-25** terbaik (`max_out ≈ 220`)
- **Konteks lokal**: potong dari `char_span` segmen asal, **≤ 1000 chars** (±350 kiri/kanan + judul header).
- Simpan hasil (termasuk provenance) → `generated_content.json` + `suggestions.json` untuk UI.

**Lazy (on-demand):**
- Jika saat chat diperlukan item yang belum diperkaya → enrich saat itu → simpan ke cache (SQLite).

**Prompt Enricher (FINAL)**

**SYSTEM**
```
You are an enrichment generator. Use ONLY the CONTEXT.
Return STRICT JSON. No markdown.
If context is insufficient, return "content": "".
```

**USER**
```
ITEM:
- label: {label}
- type: {term_to_define|concept_to_simplify}

CONTEXT:
{context_≤1000_chars}

PROVENANCE:
{"page_start":..., "page_end":..., "header_path":"...", "char_span":[s,e]}

OUTPUT:
{
  "label": "...",
  "type": "term_to_define" | "concept_to_simplify",
  "content": "...",         
  "provenance": {"page_start":..., "page_end":..., "header_path":"...", "char_span":[s,e]}
}
```

---

### Retrieval/Chat — Router → Dense → Rerank → Answer
**Tujuan:** waktu jawaban < beberapa detik meski korpus raksasa.

**Langkah:**
1. **Router**: BM25 keywords + centroid shard → pilih **≤ 5 shard** paling relevan.
2. **Dense retrieval** pada shard tersebut → ambil **k ≈ 50** segmen kandidat.
3. **Rerank ringan** (cross-encoder kecil/LLM mini) → **k ≈ 10** evidence final.
4. **Answer**: LLM menyusun jawaban dengan evidence + (bila ada) hasil enrichment; tampilkan evidence ranking & metadata.
5. **Lazy-enrich hook**: jika item penting belum diperkaya dan dipakai oleh jawaban → enrich sekarang + cache.

**Pseudocode (EN):**
```python
def answer(question):
    shard_ids = route_bm25_plus_centroid(question)[:5]
    hits = dense_in_shards(shard_ids, question, k=50)
    top = rerank_light(hits, question)[:10]
    reply = llm_answer(question, top, enriched_cache)
    maybe_lazy_enrich(plan, question, top, cache=enriched_cache)
    return reply, top  # top berisi evidence terurut + metadata
```

---

## 3) 8 Poin Perbaikan (Disetujui)

1. **Router bertingkat sebelum vektor** (BM25 → Shard → Dense → Rerank).  
2. **Sharding topik rapi** (berdasarkan `header_path` + centroid; kuota per shard saat gating).  
3. **Dedup boilerplate** (SimHash/MinHash; skip LLM untuk duplikat).  
4. **Budget & early-exit** (token/waktu/jumlah item; stop-when-budget <10%).  
5. **Paging/infinite scroll** di panel Enhancement (+ streaming batch, no hard slice).  
6. **Hierarki ringkasan per section** saat ingest (1–3 kalimat) untuk bantu router & hemat token.  
7. **Cache lintas dokumen untuk glossary** (key label ternormalisasi + sumber).  
8. **Rerank ringan** (cross-encoder kecil/LLM mini pada top-50 → top-10).

---

## 4) Parameter Awal (Knobs yang bisa di-tune)

- **Gating Top-K global**: `min(2000, ceil(0.015 × N_segments))`  
- **Kuota per shard**: 8 (10 untuk “Definisi/Ketentuan”)  
- **Planner skim**: `max_out=40`, `timeout=8s`, `RPS=3–5`, `token_budget≈40k`  
- **Reduce final**: total **200–300 item**  
- **Enrichment**: eager **Top 50–100**, konteks **≤1000 chars**; refine **Top-25** setelah sketch  
- **Retrieval saat chat**: router **5 shard → dense k=50 → rerank k=10 → jawab**

**Guardrails runtime (tanpa Docker/Redis):**
- `asyncio.Semaphore(concurrency)` + **leaky-bucket** RPS limiter kecil.
- **TokenBudget** (per fase), **timeout** per call, `return_exceptions=True` + checkpoint per batch.
- **SQLite cache** (key: `sha256(seg_text)+prompt_version`) untuk planner & enricher.

---

## 5) Kontrak Data (Ringkas)

**`segments.json`** — lihat di F0 (di atas).  
**`shards.json`** — lihat di F0.  
**`plan.json`**
```json
{
  "terms_to_define":[
    {"label":"...", "score":0.91, "provenances":[{"seg_id":"...", "page":..., "header_path":["..."]}]}
  ],
  "concepts_to_simplify":[
    {"label":"...", "score":0.88, "provenances":[{"seg_id":"...", "page":..., "header_path":["..."]}]}
  ]
}
```
**`generated_content.json`**
```json
{
  "term::BI-Rate": {
    "content":"...",
    "provenance":{"seg_id":"...", "page_start":..., "page_end":..., "header_path":"...", "char_span":[s,e]}
  }
}
```
**`suggestions.json`**: daftar ringkasan untuk UI (dipaging).

---

## 6) UI & API

**Backend API (disarankan)**
- `POST /upload-document` → jalankan F0.
- `POST /start-planner/{doc_id}` → jalankan F1 (budget-aware); progress stream.
- `POST /start-enrichment/{doc_id}` → jalankan F2 eager; progress stream.
- `GET /progress/{doc_id}` → `{phase, processed, total, p50, p95, cache_hits}`.
- `GET /get-suggestions/{doc_id}?page=&page_size=` → paging, bukan fixed 10.
- `POST /ask` → router→dense→rerank→jawab (+ lazy-enrich jika perlu).
- `GET /evidence/{doc_id}?q=` → (opsional) untuk audit.

**Frontend (minimum):**
- Panel Enhancement dengan **paging/infinite scroll**; render per batch.
- Evidence panel menunjukkan **ranking + metadata** (page, header).
- Tombol **Stop & Finalize** menghentikan fase berjalan dengan rapi.

---

## 7) Observability & Testing

**Metrik per fase:**
- `segments_total`, `segments_gated`, `plan_items`, `gen_items`,
- `cache_hits`, `timeouts`, `latency_p50/p95`,
- `cost_estimate_tokens` (input/output).

**DoD (Definition of Done):**
- **22 halaman**: F1 ≤ ~1 menit, F2 ≤ ~1.5 menit, Enhancement menampilkan **≥50** item (paging OK).  
- **Dokumen besar**: chat responsif (router→shard), **tanpa hang**, lazy-enrich berjalan.  
- Evidence transparan & konsisten.

**Uji fungsional:**
- Gating menurunkan kandidat drastis, **merata per shard**.
- Enrichment **tidak** mengulang item yang sama (cache).
- UI **tidak** memotong di 10 item; pagination bekerja.

---

## 8) Risiko & Mitigasi

- **Recall turun** (gate terlalu ketat) → **lazy-enrich** + tombol “Promote to shortlist” (UI) → naikkan ke eager batch berikutnya.
- **Shard tidak seimbang** → atur kuota per shard & cek distribusi; tuning threshold cluster.
- **Biaya meledak** → perketat budget, aktifkan early-exit, dan tingkatkan cache & dedup.

---

## 9) PR Checklist untuk Cascade (Strangler Approach)

**Sprint 0 — Kerangka**
- [ ] Buat struktur folder modular (`core/`, `ingest/`, `planner/`, `enrich/`, `retrieve/`, `api/`, `ui/`).  
- [ ] Tambah util: `TokenBudget`, `AsyncLeakyBucket`, `local_sqlite_cache` (EN).

**Sprint 1 — Ingest**
- [ ] Segmentasi + metadata (flags, numeric_ratio, hash).
- [ ] BM25 index builder + query API sederhana.
- [ ] Sharding (group by `header_path`) + centroid embedding kecil.

**Sprint 2 — Planner**
- [ ] Gating non-LLM (Top-K global + kuota per shard) → list segmen.
- [ ] LLM Skim prompt (FINAL) + guard (RPS, timeout, budget, checkpoint).
- [ ] Reduce (normalize → embed label unik → cluster → score) → `plan.json`.

**Sprint 3 — Enrichment**
- [ ] Context builder (≤ 1000 chars) dari `char_span` + header.
- [ ] Eager (sketch→refine) untuk Top 50–100; simpan `generated_content.json`.
- [ ] Lazy-enrich hook (dipanggil saat chat; cache SQLite).

**Sprint 4 — Retrieval/Chat**
- [ ] Router: BM25 + centroid shard (≤ 5 shard).
- [ ] Dense retrieval di shard; k≈50 → Rerank ringan → k≈10.
- [ ] Answer + Evidence; panggil lazy-enrich bila perlu.

**Sprint 5 — UI**
- [ ] Enhancement panel: paging/infinite scroll; progress streaming per batch.
- [ ] Evidence panel + metadata halaman/header.
- [ ] Tombol Stop & Finalize (graceful cancel).

**Sprint 6 — Metrik & Guardrails**
- [ ] Logging per 10 batch; metrik p50/p95, cache hits, cost.
- [ ] Stop-when-budget; early-exit saat confidence tinggi.
- [ ] Validasi DoD 22 halaman & uji dokumen besar.

---

## 10) Snippets Util (EN, contoh singkat)

**Token Budget**
```python
import tiktoken
enc = tiktoken.encoding_for_model("gpt-4.1")
class TokenBudget:
    def __init__(self, total:int):
        self.total = total; self.used = 0
    def can_afford(self, prompt:str, max_out:int)->bool:
        est = len(enc.encode(prompt)) + max_out
        return self.used + est <= int(self.total*0.9)
    def charge(self, prompt:str, max_out:int):
        self.used += len(enc.encode(prompt)) + max_out
```

**Leaky Bucket (RPS)**
```python
import asyncio, time
class AsyncLeakyBucket:
    def __init__(self, rps:float, capacity:int=10):
        self.rps=rps; self.capacity=capacity
        self.tokens=capacity; self.last=time.monotonic()
        self.lock=asyncio.Lock()
    async def acquire(self):
        async with self.lock:
            now=time.monotonic()
            self.tokens=min(self.capacity, self.tokens+(now-self.last)*self.rps)
            self.last=now
            if self.tokens<1:
                await asyncio.sleep((1 - self.tokens)/self.rps)
                self.tokens=0
            else:
                self.tokens-=1
```

**Local Cache (SQLite)**
```python
import sqlite3, json, os, time, hashlib
DB="cache/local_cache.sqlite"
os.makedirs("cache", exist_ok=True)
con=sqlite3.connect(DB, check_same_thread=False)
con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, ts REAL)")
con.commit()

def key_for(s:str)->str: return hashlib.sha256(s.encode()).hexdigest()
def get(k:str):
    cur=con.execute("SELECT v FROM kv WHERE k=?",(k,)); row=cur.fetchone()
    return json.loads(row[0]) if row else None
def set(k:str, obj:dict):
    con.execute("REPLACE INTO kv(k,v,ts) VALUES(?,?,?)",(k, json.dumps(obj, ensure_ascii=False), time.time()))
    con.commit()
```

---

## 11) Acceptance Criteria (DoD Akhir)

- **22 hlm**: F1 ≤ ~1 menit, F2 ≤ ~1.5 menit; panel Enhancement menampilkan ≥50 item via paging.
- **Dokumen besar (ratusan–ribuan hlm)**: chat responsif (router→shard), lazy-enrich bekerja, tidak hang.
- Evidence transparan (ranking + metadata), *orphan anchors* ≈ 0 (fallback Appendix bila perlu).
- Vector store berisi metadata kaya (header_path, page_start/end, version).

---

### Catatan Penutup
Dokumen ini adalah **titik acuan** untuk seluruh tim. Jika ada perubahan parameter/prompt, **versikan** dokumen ini dan catat alasan perubahan agar eksperimen bisa diulang.
