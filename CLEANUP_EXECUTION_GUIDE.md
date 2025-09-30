# 🚀 CLEANUP EXECUTION GUIDE

**Date**: 2025-09-30  
**Status**: Ready for execution after testing  
**Based on**: FINAL_AUDIT_REPORT.md (100% Complete Audit)

---

## ✅ PRE-EXECUTION CHECKLIST

Before running `cleanup_old_files.ps1`, verify:

### **1. Server Testing**:
```powershell
# Test 1: Check imports
python -c "from src.main import app; print('✅ Main app OK')"

# Test 2: Check API routes
python -c "from src.api.routes import router; print('✅ API routes OK')"

# Test 3: Check extraction
python -c "from src.extraction.extractor import extract_pdf_to_markdown; print('✅ Extraction OK')"

# Test 4: Check enhancement  
python -c "from src.enhancement.enhancer import DirectEnhancerV2; print('✅ Enhancement OK')"

# Test 5: Start server
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000
# Should start without errors
```

### **2. Workflow Testing**:
- [ ] Upload PDF successfully
- [ ] Extract to markdown
- [ ] Run enhancement
- [ ] Synthesize markdown v2
- [ ] Vectorize document
- [ ] Test RAG query

### **3. Backup** (Optional but recommended):
```powershell
# Create backup of current state
$date = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item -Path "src" -Destination "backup_src_$date" -Recurse
Write-Output "✅ Backup created: backup_src_$date"
```

---

## 🗑️ WHAT WILL BE DELETED

### **Individual Files** (9 files):

#### **API Module** (2):
- ❌ `src/api/endpoints.py` → Replaced by `routes.py`
- ❌ `src/api/models.py` → Replaced by `schemas.py`

#### **Core Module** (3):
- ❌ `src/core/json_validators.py` → Replaced by `validators.py`
- ❌ `src/core/local_cache.py` → Moved to `rag/cache.py`
- ❌ `src/core/rag_builder.py` → Moved to `rag/retriever.py`

#### **Enhancement Module** (4):
- ❌ `src/enhancement/direct_enhancer_v2.py` → Replaced by `enhancer.py`
- ❌ `src/enhancement/prompts_direct.py` → Moved to `prompts/enhancement.py`
- ❌ `src/enhancement/synthesizer.py` → Moved to `synthesis/synthesizer.py`
- ❌ `src/enhancement/indexer.py` → Moved to `vectorization/indexer.py`

### **Complete Folders** (4 folders + all contents):

#### **Extract Folder** (4 files inside):
- ❌ `src/extract/__init__.py`
- ❌ `src/extract/extractor_helpers.py`
- ❌ `src/extract/extractor_v2.py`
- ❌ `src/extract/extractor_v2_methods.py`
**→ Replaced by**: `src/extraction/` with clean names

#### **Obs Folder** (3 files inside):
- ❌ `src/obs/__init__.py`
- ❌ `src/obs/token_count.py`
- ❌ `src/obs/token_ledger.py`
**→ Replaced by**: `src/observability/` with better naming

#### **Pipeline Folder** (3 files inside):
- ❌ `src/pipeline/__init__.py`
- ❌ `src/pipeline/phase_3_synthesis.py`
- ❌ `src/pipeline/phase_4_vectorization.py`
**→ Split into**: `src/synthesis/` and `src/vectorization/`

#### **Utils Folder** (1 file inside):
- ❌ `src/utils/doc_meta.py`
**→ Replaced by**: `src/shared/document_meta.py`

---

## 📊 TOTAL DELETION COUNT

- **Individual files**: 9 files
- **Files in folders**: 11 files (inside 4 folders)
- **Total files deleted**: **20 files**
- **Total folders deleted**: **4 folders**

---

## 🚀 EXECUTION STEPS

### **Step 1: Final Verification**
```powershell
# Verify new structure exists
Test-Path "src/extraction" # Should be True
Test-Path "src/observability" # Should be True
Test-Path "src/synthesis" # Should be True
Test-Path "src/vectorization" # Should be True
Test-Path "src/rag" # Should be True
Test-Path "src/prompts" # Should be True
Test-Path "src/shared" # Should be True
```

### **Step 2: Run Cleanup Script**
```powershell
# Execute the comprehensive cleanup
.\cleanup_old_files.ps1

# When prompted, type: DELETE
```

### **Step 3: Verify Deletion**
```powershell
# Verify old files are gone
Test-Path "src/extract" # Should be False
Test-Path "src/obs" # Should be False
Test-Path "src/pipeline" # Should be False
Test-Path "src/utils" # Should be False
Test-Path "src/api/endpoints.py" # Should be False
Test-Path "src/api/models.py" # Should be False
```

### **Step 4: Test Again**
```powershell
# Test server starts
python -m uvicorn src.main:app --host 127.0.0.1 --port 8000

# Test workflow
# (Upload PDF → Extract → Enhance → Synthesize → Query)
```

### **Step 5: Commit to Git**
```bash
git add .
git commit -m "refactor: Complete production-ready restructuring

- Renamed modules for clarity (extract→extraction, obs→observability)
- Removed _v2 suffixes from all files
- Organized into logical phases (extraction, enhancement, synthesis, vectorization, rag)
- Centralized prompts in dedicated module
- Updated all imports and paths
- Removed 20 old/duplicate files
- Professional naming conventions applied
- Production-ready structure"
```

---

## ⚠️ TROUBLESHOOTING

### **If Server Won't Start After Cleanup**:

1. **Check for missing imports**:
```powershell
python -c "import src.main" 2>&1 | Select-String "ImportError|ModuleNotFoundError"
```

2. **Restore from backup** (if created):
```powershell
Remove-Item "src" -Recurse -Force
Copy-Item "backup_src_YYYYMMDD_HHMMSS" -Destination "src" -Recurse
```

3. **Check specific module**:
```powershell
# Test each module individually
python -c "import src.extraction.extractor"
python -c "import src.enhancement.enhancer"
python -c "import src.synthesis.synthesizer"
python -c "import src.vectorization.vectorizer"
```

### **If Workflow Fails**:

1. **Check logs** for import errors
2. **Verify all files exist** in new structure
3. **Check if old imports** are still referenced somewhere

---

## ✅ SUCCESS CRITERIA

After cleanup, you should have:

### **Clean Folder Structure**:
```
src/
├── api/              (routes.py, schemas.py only)
├── core/             (no old files)
├── enhancement/      (enhancer.py, not direct_enhancer_v2.py)
├── extraction/       ✅ NEW (no extract/ folder)
├── observability/    ✅ NEW (no obs/ folder)
├── prompts/          ✅ NEW
├── rag/              ✅ NEW
├── shared/           ✅ NEW (no utils/ folder)
├── synthesis/        ✅ NEW (no pipeline/ folder)
└── vectorization/    ✅ NEW (no pipeline/ folder)
```

### **No Old Files**:
- ✅ No files with `_v2` suffix
- ✅ No `extract/` folder
- ✅ No `obs/` folder
- ✅ No `pipeline/` folder
- ✅ No `utils/` folder
- ✅ No duplicate files

### **Working System**:
- ✅ Server starts without errors
- ✅ Complete workflow works end-to-end
- ✅ No import errors in logs
- ✅ All endpoints respond correctly

---

## 🎯 POST-CLEANUP TASKS

### **1. Update Documentation**:
- [ ] Update README.md with new structure
- [ ] Update API documentation
- [ ] Update development guide

### **2. Team Communication**:
- [ ] Inform team of new structure
- [ ] Share REFACTORING_COMPLETION_REPORT.md
- [ ] Update onboarding docs

### **3. Final Quality Check**:
- [ ] Run linter: `pylint src/`
- [ ] Check code style: `black src/ --check`
- [ ] Run tests if available

---

## 🎉 COMPLETION

Once all steps are complete:

✅ Codebase is **production-ready**  
✅ Structure is **professional**  
✅ Code is **maintainable**  
✅ Ready for **code review**  
✅ Ready for **GitHub push**  
✅ Ready for **team collaboration**  

---

**You're done! Your codebase is now clean, professional, and production-ready! 🚀**
