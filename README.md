# Genesis RAG v3.2

**Production-ready Retrieval-Augmented Generation System**

Genesis RAG adalah sistem RAG (Retrieval-Augmented Generation) yang dirancang untuk production dengan fitur automated document enrichment dan multi-tenant namespace management. Sistem ini memungkinkan upload dokumen markdown ke Pinecone untuk kemudian di-query menggunakan AI, dengan dukungan isolasi data per client melalui namespace.

---

## 📋 Table of Contents

- [Core Features](#-core-features)
- [Tech Stack](#-tech-stack)
- [System Architecture](#-system-architecture)
- [Project Structure](#-project-structure)
- [Getting Started](#-getting-started)
- [Usage Guide](#-usage-guide)
- [API Documentation](#-api-documentation)
- [Configuration](#-configuration)
- [Deployment](#-deployment)

---

## 🎯 Core Features

### **1. Batch Upload dengan Namespace Isolation**
- Upload multiple markdown files dalam satu request
- Multi-tenant support: isolasi data per client via Pinecone namespaces
- Accumulated stats tracking: monitor total documents, chunks, dan tokens per namespace
- Production-ready namespace protection

### **2. Real-time Progress Tracking**
- Live upload progress dengan percentage indicator
- Per-file processing status (success/failed)
- Detailed metrics: chunks uploaded, tokens used, processing time

### **3. Vector Storage & Retrieval**
- **Pinecone v3+**: Cloud-native vector database dengan auto-scaling
- **Efficient embedding**: OpenAI text-embedding-3-small (1536 dimensions)
- **Batch operations**: Optimized upsert untuk large documents
- **Namespace-based querying**: Query hanya data namespace tertentu

### **4. Document Enrichment Pipeline** *(Optional - Available for PDF input)*
- Multi-phase document enhancement untuk meningkatkan quality
- Planning & content generation menggunakan LLM
- Progressive suggestion display

### **5. RAG Query System**
- Semantic search menggunakan cosine similarity
- Context-aware answering dengan LangChain
- Support untuk Indonesian dan English

---

## 🛠 Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Backend Framework** | FastAPI 0.110+ | High-performance async API server |
| **Vector Database** | Pinecone v3+ | Cloud-native vector storage |
| **AI Orchestration** | LangChain | RAG chain & document processing |
| **LLM** | OpenAI GPT-4.1 | Chat completions & reasoning |
| **Embeddings** | OpenAI text-embedding-3-small | Document vectorization (1536-dim) |
| **Document Processing** | PyMuPDF, Camelot, Tesseract | PDF extraction (multi-modal) |
| **Frontend** | Vanilla JavaScript + HTML/CSS | No-framework UI |
| **Observability** | Loguru | Structured logging |
| **Token Tracking** | Custom ledger | Cost monitoring |

---

## 🏗 System Architecture

### **High-Level Workflow**

```
┌─────────────────────────────────────────────────────────────┐
│                     Genesis RAG System                       │
└─────────────────────────────────────────────────────────────┘

┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│   Upload     │   -->   │ Vectorization│   -->   │   Pinecone   │
│ (.md files)  │         │   Pipeline   │         │  (Namespace) │
└──────────────┘         └──────────────┘         └──────────────┘
                                                          │
                                                          v
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│    Query     │   <--   │  RAG Chain   │   <--   │   Retrieval  │
│  (Question)  │         │  (LangChain) │         │  (Semantic)  │
└──────────────┘         └──────────────┘         └──────────────┘
```

### **Namespace Architecture**

```
Pinecone Index: "inspigo-pinecone"
  ├── Namespace: "client-a-testing-1"    # Client A - Testing environment
  ├── Namespace: "client-a-final"        # Client A - Production
  ├── Namespace: "client-b-testing-1"    # Client B - Testing environment
  └── Namespace: "client-b-final"        # Client B - Production

Benefits:
✅ Data isolation per client
✅ Single index = cost efficient
✅ Easy to scale
✅ Testing/production separation
```

### **Data Flow: Upload Process**

```
1. User selects namespace + markdown files
   ↓
2. POST /namespaces/batch-upload
   ↓
3. For each file:
   - Split into chunks (LangChain text splitter)
   - Generate embeddings (OpenAI)
   - Upsert to Pinecone (batch 100 vectors)
   ↓
4. Update namespace metadata
   - Increment document count
   - Add chunk count
   - Track token usage
   - Save timestamp
   ↓
5. Return response:
   - Upload summary (files, chunks)
   - Accumulated stats (total docs, chunks, tokens)
   - Per-file details
```

### **Data Flow: Query Process**

```
1. User enters question
   ↓
2. POST /ask
   ↓
3. Query processing:
   - Generate query embedding
   - Search Pinecone (filtered by namespace)
   - Retrieve top-k documents
   ↓
4. RAG chain:
   - Pass documents + question to LLM
   - LLM generates answer with context
   ↓
5. Return answer + sources (optional)
```

---

## 📁 Project Structure

```
RAGv2/
├── src/                          # Source code (modular architecture)
│   ├── api/                      # API endpoints
│   │   ├── routes.py            # Main API routes (enhancement, query)
│   │   └── namespace_routes.py  # Namespace management endpoints
│   │
│   ├── core/                     # Core configuration & utilities
│   │   ├── config.py            # Environment variables, settings
│   │   ├── namespaces_config.py # Namespace definitions & metadata
│   │   └── json_validators.py   # Schema validation
│   │
│   ├── vectorization/            # Vector operations
│   │   ├── vectorizer.py        # Main vectorization logic
│   │   └── batch_uploader.py    # Batch upload handler
│   │
│   ├── rag/                      # RAG system
│   │   ├── builder.py           # RAG chain construction
│   │   └── answering.py         # Query processing
│   │
│   ├── enhancement/              # Document enrichment (optional)
│   │   ├── planner.py           # Phase 1: Planning
│   │   ├── generator.py         # Phase 2: Content generation
│   │   └── synthesizer.py       # Phase 3: Document synthesis
│   │
│   ├── extraction/               # PDF processing (optional)
│   │   └── extractor.py         # Multi-modal PDF extraction
│   │
│   ├── observability/            # Monitoring & tracking
│   │   ├── token_ledger.py      # Token usage logging
│   │   └── token_counter.py     # Token counting utilities
│   │
│   └── main.py                   # FastAPI application entry point
│
├── static/                       # Frontend assets
│   ├── batch_upload.js          # Upload UI logic
│   ├── batch_upload.css         # Styles
│   └── script.js                # Main app logic
│
├── artefacts/                    # Runtime data (gitignored)
│   ├── namespace_metadata.json  # Namespace stats (documents, chunks, tokens)
│   ├── token_usage.jsonl        # Token consumption logs
│   └── {document_id}/           # Per-document artifacts
│
├── cache/                        # LLM response cache
│   └── local_cache.sqlite       # SQLite KV cache
│
├── index.html                    # Main application UI
├── batch_upload.html             # Batch upload interface
├── requirements.txt              # Python dependencies
├── .env                          # Environment variables (not in git)
├── .env.example                  # Environment template
└── README.md                     # This file
```

### **Key Files Explained**

| File | Purpose |
|------|---------|
| `src/main.py` | FastAPI app initialization, startup/shutdown lifecycle |
| `src/core/namespaces_config.py` | Namespace definitions, metadata management |
| `src/vectorization/batch_uploader.py` | Core upload logic (chunking, embedding, upsert) |
| `src/api/namespace_routes.py` | API endpoints untuk namespace operations |
| `batch_upload.html` | Frontend interface untuk batch upload |
| `artefacts/namespace_metadata.json` | Persistent storage untuk accumulated stats |

---

## 🚀 Getting Started

### **Prerequisites**

- **Python 3.9+**
- **OpenAI API Key** (untuk embeddings & chat)
- **Pinecone API Key** (untuk vector storage)
- **Git** (untuk version control)

### **1. Clone Repository**

```bash
git clone <repository-url>
cd RAGv2
```

### **2. Create Virtual Environment**

```bash
# Windows
python -m venv venv
.\venv\Scripts\activate

# macOS/Linux
python3 -m venv venv
source venv/bin/activate
```

### **3. Install Dependencies**

```bash
pip install -r requirements.txt
```

**Dependencies include:**
- `fastapi` - Web framework
- `uvicorn[standard]` - ASGI server
- `langchain` + `langchain-openai` + `langchain-pinecone` - RAG framework
- `pinecone-client>=3.0.0` - Pinecone SDK
- `pymupdf4llm`, `camelot-py`, `pytesseract` - PDF processing
- `loguru` - Logging
- `tiktoken` - Token counting

### **4. Setup Environment Variables**

```bash
# Copy template
cp .env.example .env

# Edit .env dengan text editor
```

**Minimum `.env` configuration:**

```ini
# OpenAI Configuration
OPENAI_API_KEY=sk-your-openai-api-key-here
LLM_MODEL=gpt-4.1
EMBEDDING_MODEL=text-embedding-3-small

# Pinecone Configuration (v3+ - no environment needed)
PINECONE_API_KEY=pc-your-pinecone-api-key-here
PINECONE_INDEX_NAME=inspigo-pinecone

# Application Settings
PIPELINE_ARTEFACTS_DIR=artefacts

# Performance Limits (optional)
PHASE1_TOKEN_BUDGET=35000
PHASE2_TOKEN_BUDGET=50000
PHASE1_CONCURRENCY=7
PHASE1_RPS=3
PHASE2_CONCURRENCY=4
PHASE2_RPS=2
```

### **5. Setup Pinecone Index**

**Option A: Via Pinecone Dashboard**
1. Login ke [Pinecone Console](https://app.pinecone.io)
2. Create new index:
   - **Name**: `inspigo-pinecone` (atau sesuai .env)
   - **Dimensions**: `1536`
   - **Metric**: `cosine`
   - **Pod Type**: `s1.x1` atau `serverless`

**Option B: Via Python**
```python
from pinecone import Pinecone, ServerlessSpec

pc = Pinecone(api_key="your-api-key")
pc.create_index(
    name="inspigo-pinecone",
    dimension=1536,
    metric="cosine",
    spec=ServerlessSpec(cloud="aws", region="us-east-1")
)
```

### **6. Test Connection**

```bash
python test_pinecone_connection.py
```

**Expected output:**
```
✅ Connected to Pinecone index: inspigo-pinecone
📊 Index stats: {'dimension': 1536, 'namespaces': {...}}
```

### **7. Run Application**

```bash
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000 --reload
```

**Expected startup logs:**
```
2025-10-01 02:00:00 | INFO     | src.main:lifespan - Startup Aplikasi: Menginisialisasi...
2025-10-01 02:00:01 | INFO     | src.main:lifespan - Pinecone client terhubung ke index: inspigo-pinecone
2025-10-01 02:00:02 | INFO     | src.main:lifespan - Model AI berhasil diinisialisasi.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

### **8. Open Application**

Open browser: **http://127.0.0.1:8000**

---

## 📖 Usage Guide

### **A. Batch Upload via UI**

1. **Navigate to Batch Upload**
   ```
   http://127.0.0.1:8000/batch_upload.html
   ```

2. **Select Namespace**
   - Dropdown menampilkan available namespaces
   - Pilih namespace target (e.g., "Client A - Testing Batch 1")
   - Info namespace: client, type, description

3. **Upload Files**
   - Click "Choose File" atau drag & drop
   - Support: `.md` atau `.markdown` files
   - Multiple files allowed
   - Max size: 100MB per file

4. **Monitor Progress**
   - Progress bar shows upload percentage
   - Real-time status updates
   - Per-file processing indicator

5. **View Results**
   - **Upload Summary**: Files processed, succeeded, failed, chunks uploaded
   - **Accumulated Stats**: Total documents, chunks, tokens di namespace
   - **Per-file Details**: Chunks & tokens per file

### **B. Query Documents via UI**

1. **Navigate to Main Page**
   ```
   http://127.0.0.1:8000
   ```

2. **Enter Question**
   - Type your question in Indonesian or English
   - Example: "Apa saja strategi investasi yang direkomendasikan?"

3. **Get Answer**
   - System retrieves relevant chunks
   - LLM generates answer with context
   - Answer displayed with sources (optional)

---

## 🔌 API Documentation

### **Namespace Management**

#### `GET /namespaces/`
List all namespaces.

**Query Parameters:**
- `active_only` (bool): Return only active namespaces (default: false)

**Response:**
```json
{
  "namespaces": [
    {
      "id": "client-a-testing-1",
      "name": "Client A - Testing Batch 1",
      "description": "Testing environment untuk Client A",
      "client": "client-a",
      "type": "testing",
      "is_active": true
    }
  ]
}
```

#### `GET /namespaces/{namespace_id}`
Get detailed info for a specific namespace.

**Response:**
```json
{
  "namespace": {
    "id": "client-a-testing-1",
    "name": "Client A - Testing Batch 1",
    "client": "client-a",
    "type": "testing",
    "is_active": true,
    "description": "Testing environment..."
  },
  "metadata": {
    "document_count": 5,
    "total_chunks": 234,
    "total_tokens": 45670,
    "created_at": "2025-10-01T01:00:00",
    "last_updated": "2025-10-01T02:00:00"
  },
  "display_info": {
    "badge_color": "#FFA500",
    "badge_text": "TESTING",
    "can_clear": true
  }
}
```

#### `POST /namespaces/batch-upload`
Upload multiple markdown files to a namespace.

**Request:** `multipart/form-data`
- `namespace_id` (string): Target namespace ID
- `files` (file[]): List of markdown files

**Response:**
```json
{
  "success": true,
  "namespace_id": "client-a-testing-1",
  "namespace_name": "Client A - Testing Batch 1",
  "files_processed": 2,
  "files_succeeded": 2,
  "files_failed": 0,
  "total_chunks_uploaded": 45,
  "total_input_tokens": 8900,
  "upload_timestamp": "2025-10-01T02:00:00",
  "detailed_results": [
    {
      "filename": "document1.md",
      "doc_id": "batch_abc123",
      "status": "success",
      "chunks_total": 23,
      "chunks_uploaded": 23,
      "input_tokens": 4500,
      "namespace": "client-a-testing-1"
    }
  ],
  "namespace_accumulated_stats": {
    "total_documents": 7,
    "total_chunks": 279,
    "total_tokens": 54570,
    "created_at": "2025-10-01T01:00:00",
    "last_updated": "2025-10-01T02:00:00"
  }
}
```

### **RAG Query**

#### `POST /ask/`
Query documents dalam namespace.

**Request:**
```json
{
  "document_id": "optional-doc-id",
  "prompt": "Apa strategi investasi yang direkomendasikan?",
  "version": "v2",
  "trace": true,
  "k": 5
}
```

**Response:**
```json
{
  "answer": "Berdasarkan dokumen, strategi investasi yang direkomendasikan adalah...",
  "version": "v2",
  "prompt": "Apa strategi investasi yang direkomendasikan?",
  "sources": [
    {
      "content": "...",
      "metadata": {
        "source_document": "batch_abc123",
        "chunk_index": 5
      },
      "score": 0.89
    }
  ]
}
```

---

## ⚙️ Configuration

### **Environment Variables**

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | *required* | OpenAI API key untuk LLM & embeddings |
| `LLM_MODEL` | `gpt-4.1` | Chat completion model |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model (1536-dim) |
| `PINECONE_API_KEY` | *required* | Pinecone API key |
| `PINECONE_INDEX_NAME` | `inspigo-pinecone` | Pinecone index name |
| `PIPELINE_ARTEFACTS_DIR` | `artefacts` | Directory for runtime data |
| `PHASE1_TOKEN_BUDGET` | `35000` | Max tokens for Phase 1 (planning) |
| `PHASE2_TOKEN_BUDGET` | `50000` | Max tokens for Phase 2 (generation) |
| `PHASE1_CONCURRENCY` | `7` | Concurrent LLM calls (Phase 1) |
| `PHASE1_RPS` | `3` | Rate limit requests/second (Phase 1) |
| `PHASE2_CONCURRENCY` | `4` | Concurrent LLM calls (Phase 2) |
| `PHASE2_RPS` | `2` | Rate limit requests/second (Phase 2) |

### **Namespace Configuration**

Edit `src/core/namespaces_config.py` to add/modify namespaces:

```python
NAMESPACES = [
    {
        "id": "client-a-testing-1",
        "name": "Client A - Testing Batch 1",
        "description": "Testing environment untuk Client A",
        "client": "client-a",
        "type": "testing",        # "testing" or "final"
        "is_active": True
    },
    {
        "id": "client-a-final",
        "name": "Client A - Production",
        "description": "Production environment untuk Client A",
        "client": "client-a",
        "type": "final",
        "is_active": True
    }
]
```

**Namespace Types:**
- **`testing`**: Can be cleared anytime, for experiments
- **`final`**: Production data, protected from accidental deletion

---

## 🚢 Deployment

### **Production Checklist**

- [ ] Set strong `OPENAI_API_KEY` and `PINECONE_API_KEY`
- [ ] Use production Pinecone index (not dev/test)
- [ ] Configure proper `PHASE*_CONCURRENCY` and `PHASE*_RPS` for load
- [ ] Setup monitoring for `artefacts/token_usage.jsonl`
- [ ] Enable HTTPS for API endpoints
- [ ] Setup CORS properly in `src/main.py`
- [ ] Configure proper logging level (INFO for prod)
- [ ] Backup `artefacts/namespace_metadata.json` regularly

### **Docker Deployment (Optional)**

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Build & Run:**
```bash
docker build -t genesis-rag .
docker run -p 8000:8000 --env-file .env genesis-rag
```

### **Cloud Deployment**

**Recommended Platforms:**
- **Railway.app** - Simple deployment dari GitHub
- **Render.com** - Free tier available
- **Google Cloud Run** - Serverless containers
- **AWS ECS/Fargate** - Enterprise-grade

---

## 🔍 Troubleshooting

### **Common Issues**

**1. "OPENAI_API_KEY not set"**
```bash
# Check .env file exists and has correct key
cat .env | grep OPENAI_API_KEY
```

**2. "Pinecone index not found"**
```bash
# Verify index name matches
python -c "from pinecone import Pinecone; pc = Pinecone(api_key='...'); print(pc.list_indexes())"
```

**3. "Dimension mismatch"**
```bash
# Ensure index dimension = 1536 for text-embedding-3-small
# Or change EMBEDDING_MODEL in .env
```

**4. "Upload slow/timeout"**
```bash
# Reduce concurrency or increase timeout
# Edit PHASE*_CONCURRENCY in .env
```

**5. "Accumulated stats showing 0"**
```bash
# Check if namespace_metadata.json exists
cat artefacts/namespace_metadata.json

# Should show: {"client-a-testing-1": {"document_count": N, ...}}
```

---

## 📊 Monitoring

### **Token Usage Tracking**

Token usage logged to `artefacts/token_usage.jsonl`:

```jsonl
{"ts": 1759258795.5, "step": "embed", "model": "text-embedding-3-small", "input_tokens": 4105, "output_tokens": 0, "meta": {"phase": "batch_upload", "namespace": "client-a-testing-1", "filename": "doc.md", "num_chunks": 23}}
```

**View summary:**
```bash
cat artefacts/token_usage_summary.md
```

### **Namespace Stats**

Check accumulated stats per namespace:

```bash
cat artefacts/namespace_metadata.json
```

```json
{
  "client-a-testing-1": {
    "document_count": 7,
    "total_chunks": 279,
    "total_tokens": 54570,
    "created_at": "2025-10-01T01:00:00",
    "last_updated": "2025-10-01T02:00:00"
  }
}
```

---

## 🤝 Contributing

**Not accepting external contributions at this time.** This is an internal production system.

---

## 📄 License

Proprietary - All rights reserved. Internal use only.

---

## 📞 Support

For support, contact the development team or refer to internal documentation.

---

**Genesis RAG v3.2** - Built with ❤️ for production RAG workloads
