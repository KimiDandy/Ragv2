# 🚀 Migrasi ke Pinecone v3+ - Production Ready

## ✅ Perubahan yang Telah Dilakukan

### 1. **Konfigurasi Pinecone Terbaru**
- ❌ Menghapus `PINECONE_ENVIRONMENT` (tidak diperlukan di v3+)
- ✅ Hanya menggunakan `PINECONE_API_KEY` dan `PINECONE_INDEX_NAME`
- ✅ Auto-detect region dan environment oleh Pinecone client

### 2. **Dependencies Update**
```bash
# File: requirements.txt
pinecone-client>=3.0.0  # Versi terbaru tanpa environment
langchain-pinecone      # LangChain integration terbaru
```

### 3. **Inisialisasi Pinecone Sederhana** 
```python
# Kode baru (v3+):
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(PINECONE_INDEX_NAME)

# ❌ Kode lama yang dihapus:
# pc = Pinecone(api_key=api_key, environment=environment)
```

### 4. **Optimisasi Operasi Pinecone**
- **Delete by Filter**: Menggunakan `delete(filter={...})` instead of query-then-delete
- **Batch Upsert**: Efisiensi upload 100 vectors per batch
- **Dynamic Dimensions**: Auto-detect embedding dimensions (1536 untuk text-embedding-3-small)

## 🔧 File yang Diupdate

### Core Configuration:
- ✅ `src/core/config.py` - Hapus PINECONE_ENVIRONMENT
- ✅ `src/enhancement/config.py` - Update Pinecone config
- ✅ `requirements.txt` - Pinecone client v3+

### Application Layer:
- ✅ `src/main.py` - Simplified Pinecone initialization  
- ✅ `src/api/endpoints.py` - PineconeVectorStore integration
- ✅ `src/core/rag_builder.py` - Updated retriever logic

### Pipeline Layer:
- ✅ `src/pipeline/phase_4_vectorization.py` - Pinecone batch operations
- ✅ `src/enhancement/indexer.py` - Enhanced document indexing

## 🎯 Environment File (.env)

```bash
# OpenAI Configuration
OPENAI_API_KEY=sk-your-openai-key-here
LLM_MODEL=gpt-4.1
EMBEDDING_MODEL=text-embedding-3-small

# Pinecone Configuration (v3+ - No Environment Needed)
PINECONE_API_KEY=pc-your-pinecone-api-key-here  
PINECONE_INDEX_NAME=inspigo-pinecone

# Application Configuration
PIPELINE_ARTEFACTS_DIR=artefacts
PHASE1_TOKEN_BUDGET=35000
PHASE2_TOKEN_BUDGET=50000
PHASE1_CONCURRENCY=7
PHASE1_RPS=3
PHASE2_CONCURRENCY=4
PHASE2_RPS=2
```

## 🚀 Deployment Steps

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Update Environment File
```bash
cp .env.example .env
# Edit .env dengan API keys yang benar
```

### 3. Verify Pinecone Index
Pastikan index `inspigo-pinecone` sudah dibuat di Pinecone dashboard dengan:
- **Dimensions**: 1536 (untuk text-embedding-3-small)
- **Metric**: cosine
- **Pod Type**: s1.x1 atau serverless

### 4. Test Connection
```bash
python -c "
from src.core.config import PINECONE_API_KEY, PINECONE_INDEX_NAME
from pinecone import Pinecone
pc = Pinecone(api_key=PINECONE_API_KEY)
index = pc.Index(PINECONE_INDEX_NAME)
print(f'✅ Connected to Pinecone index: {PINECONE_INDEX_NAME}')
print(f'📊 Index stats: {index.describe_index_stats()}')
"
```

### 5. Run Application
```bash
python src/main.py
```

## 🔍 Expected Logs

Ketika aplikasi start berhasil, Anda akan melihat:
```
✅ Pinecone client terhubung ke index: inspigo-pinecone
✅ Model AI berhasil diinisialisasi.
```

## 🏗️ Production Benefits

1. **🌐 Cloud-Native**: Tidak perlu maintenance database lokal
2. **⚡ Auto-Scaling**: Pinecone handle traffic spikes otomatis  
3. **🔒 Enterprise Security**: Managed authentication & encryption
4. **📈 Performance**: Distributed vector search dengan low latency
5. **💰 Cost-Efficient**: Pay per usage, tidak perlu server dedicated

## 🛠️ Troubleshooting

### Error: "Index not found"
```bash
# Periksa index name di Pinecone dashboard
# Pastikan PINECONE_INDEX_NAME sesuai dengan dashboard
```

### Error: "Invalid API key"  
```bash
# Periksa API key di Pinecone dashboard
# Pastikan tidak ada trailing spaces di .env file
```

### Error: "Dimension mismatch"
```bash
# Pastikan index dimension = 1536 untuk text-embedding-3-small
# Atau ubah EMBEDDING_MODEL di .env sesuai index dimension
```

---

✅ **Migrasi Complete!** - RAG system sekarang berjalan full cloud-based dengan Pinecone v3+
