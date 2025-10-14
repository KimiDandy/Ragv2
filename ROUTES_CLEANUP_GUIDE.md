# 📝 **routes.py CLEANUP GUIDE**

**File:** `src/api/routes.py`  
**Current Size:** 1,103 lines  
**Target Size:** ~580 lines (47% reduction)

---

## 🎯 **ENDPOINTS TO REMOVE**

### **Block 1: Legacy Manual Workflow (Lines 209-248)**

**Endpoint:** `POST /upload-document/`

```python
@router.post("/upload-document/", status_code=201, response_model=UploadResponse)
async def upload_document(request: Request, file: UploadFile = File(...)):
    # ... 40 lines of code
```

**Reason:** Old Phase 0 manual workflow - replaced by `/documents/upload-auto`

---

### **Block 2: Legacy Enhancement Start (Lines 251-420)**

**Endpoint:** `POST /start-enhancement/{document_id}`

```python
@router.post("/start-enhancement/{document_id}")
async def start_enhancement(...):
    # ... ~170 lines of code
```

**Reason:** Old Phase 1 manual workflow - replaced by automated DocumentOrchestrator

---

### **Block 3: Legacy Get Suggestions (Lines 424-447)**

**Endpoint:** `GET /get-suggestions/{document_id}`

```python
@router.get("/get-suggestions/{document_id}", response_model=EnhancementResponse)
async def get_suggestions(document_id: str):
    # ... ~24 lines of code
```

**Reason:** Polling for manual workflow - not used anymore

---

### **Block 4: Legacy Finalize Document (Lines 450-480)**

**Endpoint:** `POST /finalize-document/`

```python
@router.post("/finalize-document/")
async def finalize_document(request: Request, payload: CuratedSuggestions):
    # ... ~31 lines of code
```

**Reason:** Old Phase 2 manual workflow - synthesis now automatic

---

### **Block 5: Legacy Progress Tracking (Lines 519-590)**

**Endpoint:** `GET /progress/{document_id}`

```python
@router.get("/progress/{document_id}")
async def get_progress(document_id: str):
    # ... ~72 lines of code
```

**Reason:** Replaced by `/documents/{document_id}/status`

---

### **Block 6: Token Usage Endpoints (Lines 593-654)**

**Endpoints:**
- `GET /artefacts/token-usage/summary`
- `GET /artefacts/token-usage/raw`
- `GET /artefacts/token-usage/stats`

```python
@router.get("/artefacts/token-usage/summary")
# ... ~22 lines

@router.get("/artefacts/token-usage/raw")
# ... ~24 lines

@router.get("/artefacts/token-usage/stats")
# ... ~12 lines
```

**Reason:** Utility endpoints not used by UI

---

### **Block 7: PDF Conversion Workflow (Lines 659-758)**

**Endpoints:**
- `POST /upload-pdf`
- `POST /start-conversion`
- `GET /conversion-progress/{document_id}`
- `GET /conversion-result/{document_id}`

```python
@router.post("/upload-pdf", response_model=UploadPdfResponse)
# ... ~20 lines

@router.post("/start-conversion")
# ... ~36 lines

@router.get("/conversion-progress/{document_id}")
# ... ~15 lines

@router.get("/conversion-result/{document_id}")
# ... ~23 lines
```

**Reason:** Alternative workflow never integrated with UI

---

### **Block 8: Debug Endpoint (Lines 761-806)**

**Endpoint:** `GET /debug/ocr-test`

```python
@router.get("/debug/ocr-test")
async def test_ocr_components():
    # ... ~46 lines of code
```

**Reason:** Debug utility not needed in production

---

## ✅ **ENDPOINTS TO KEEP**

These are the ONLY 4 endpoints actively used:

1. ✅ `POST /ask/` (Line 483) - Chat Q&A
2. ✅ `POST /documents/upload-auto` (Line 813) - Single file upload
3. ✅ `POST /documents/upload-batch` (Line 860) - Multi-file batch
4. ✅ `GET /documents/{document_id}/status` (Line 1006) - Status polling
5. ✅ `GET /documents/{document_id}/result` (Line 1047) - Get final result

Plus all the helper functions (lines 65-206):
- `_build_snippet()`
- `_minmax_normalize()`
- `_docs_to_sources()`
- `_rag_answer_with_sources()`

---

## 🔧 **RECOMMENDED APPROACH**

Due to the size and complexity, I recommend creating a NEW clean routes.py with only the working endpoints, rather than trying to edit in place.

**New Structure:**
```
routes.py (clean)
├── Imports (30 lines)
├── Global state (5 lines)
├── Helper functions (100 lines)
│   ├── _build_snippet()
│   ├── _minmax_normalize()
│   ├── _docs_to_sources()
│   └── _rag_answer_with_sources()
├── /ask/ endpoint (35 lines)
├── AUTOMATED PIPELINE section (200 lines)
│   ├── /documents/upload-auto
│   ├── /documents/upload-batch
│   ├── /documents/{id}/status
│   └── /documents/{id}/result
└── Total: ~370 lines (67% smaller!)
```

---

## 📋 **SCHEMAS TO KEEP**

From schemas.py, these are still needed:
- `QueryRequest`
- `AskSingleVersionResponse` / `AskBothVersionsResponse`
- `RetrievedSource`
- `TokenUsage`

These can be REMOVED from schemas.py:
- `SuggestionItem` (manual workflow)
- `CuratedSuggestions` (manual workflow)
- `UploadResponse` (manual workflow)
- `StartConversionRequest` (alternative workflow)
- `ConversionProgress` (alternative workflow)
- `ConversionResult` (alternative workflow)
- `EnhancementResponse` (manual workflow)
- `EnhancementConfigRequest` (manual workflow)
- `DocumentAnalysisSummary` (enhancement_routes)
- `EnhancementTypeRegistryResponse` (enhancement_routes)

---

## ⚠️ **CAUTION**

Before deleting, verify one more time by checking:
```bash
# Check if any endpoint is called
grep -r "upload-document" static/
grep -r "start-enhancement" static/
grep -r "get-suggestions" static/
grep -r "finalize-document" static/
```

If NO results → Safe to delete! ✅
