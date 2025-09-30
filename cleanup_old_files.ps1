# ============================================================================
# COMPREHENSIVE CLEANUP SCRIPT - Production-Ready Refactoring
# ============================================================================
# This script removes ALL old/duplicate files after refactoring
# Based on FINAL_AUDIT_REPORT.md
# 
# ⚠️  CRITICAL: Run this ONLY after:
#    1. Testing server startup
#    2. Testing complete workflow
#    3. Verifying all imports work
# ============================================================================

Write-Output ""
Write-Output "╔════════════════════════════════════════════════════════════════════════╗"
Write-Output "║                    COMPREHENSIVE CLEANUP SCRIPT                        ║"
Write-Output "║                                                                        ║"
Write-Output "║  This will delete 20 OLD files and 4 OLD folders                      ║"
Write-Output "║  Please ensure you have TESTED the new structure first!               ║"
Write-Output "╚════════════════════════════════════════════════════════════════════════╝"
Write-Output ""

# Safety check
Write-Output "📋 Files that will be DELETED:"
Write-Output ""
Write-Output "   API Module (2 files):"
Write-Output "   - src/api/endpoints.py"
Write-Output "   - src/api/models.py"
Write-Output ""
Write-Output "   Core Module (3 files):"
Write-Output "   - src/core/json_validators.py"
Write-Output "   - src/core/local_cache.py"
Write-Output "   - src/core/rag_builder.py"
Write-Output ""
Write-Output "   Enhancement Module (4 files):"
Write-Output "   - src/enhancement/direct_enhancer_v2.py"
Write-Output "   - src/enhancement/prompts_direct.py"
Write-Output "   - src/enhancement/synthesizer.py"
Write-Output "   - src/enhancement/indexer.py"
Write-Output ""
Write-Output "   Old Folders (ENTIRE folders - 4 folders):"
Write-Output "   - src/extract/           (replaced by extraction/)"
Write-Output "   - src/obs/               (replaced by observability/)"
Write-Output "   - src/pipeline/          (split into synthesis/ & vectorization/)"
Write-Output "   - src/utils/             (replaced by shared/)"
Write-Output ""
Write-Output "════════════════════════════════════════════════════════════════════════"
Write-Output ""

$confirmation = Read-Host "⚠️  Type 'DELETE' to proceed (case-sensitive)"

if ($confirmation -ne 'DELETE') {
    Write-Output ""
    Write-Output "❌ Aborted. No files deleted."
    Write-Output "✅ Your codebase is safe."
    exit
}

Write-Output ""
Write-Output "🗑️  Starting cleanup process..."
Write-Output ""

# Counter for deleted items
$deletedFiles = 0
$deletedFolders = 0

# ============================================================================
# STEP 1: Delete individual old files in modules
# ============================================================================

Write-Output "[STEP 1] Deleting old files in modules..."
Write-Output ""

# API Module
Write-Output "   [API] Deleting old files..."
if (Test-Path "src/api/endpoints.py") {
    Remove-Item "src/api/endpoints.py" -Force
    Write-Output "   ✓ Deleted: src/api/endpoints.py"
    $deletedFiles++
}
if (Test-Path "src/api/models.py") {
    Remove-Item "src/api/models.py" -Force
    Write-Output "   ✓ Deleted: src/api/models.py"
    $deletedFiles++
}

# Core Module
Write-Output "   [CORE] Deleting old files..."
if (Test-Path "src/core/json_validators.py") {
    Remove-Item "src/core/json_validators.py" -Force
    Write-Output "   ✓ Deleted: src/core/json_validators.py"
    $deletedFiles++
}
if (Test-Path "src/core/local_cache.py") {
    Remove-Item "src/core/local_cache.py" -Force
    Write-Output "   ✓ Deleted: src/core/local_cache.py"
    $deletedFiles++
}
if (Test-Path "src/core/rag_builder.py") {
    Remove-Item "src/core/rag_builder.py" -Force
    Write-Output "   ✓ Deleted: src/core/rag_builder.py"
    $deletedFiles++
}

# Enhancement Module
Write-Output "   [ENHANCEMENT] Deleting old files..."
if (Test-Path "src/enhancement/direct_enhancer_v2.py") {
    Remove-Item "src/enhancement/direct_enhancer_v2.py" -Force
    Write-Output "   ✓ Deleted: src/enhancement/direct_enhancer_v2.py"
    $deletedFiles++
}
if (Test-Path "src/enhancement/prompts_direct.py") {
    Remove-Item "src/enhancement/prompts_direct.py" -Force
    Write-Output "   ✓ Deleted: src/enhancement/prompts_direct.py"
    $deletedFiles++
}
if (Test-Path "src/enhancement/synthesizer.py") {
    Remove-Item "src/enhancement/synthesizer.py" -Force
    Write-Output "   ✓ Deleted: src/enhancement/synthesizer.py"
    $deletedFiles++
}
if (Test-Path "src/enhancement/indexer.py") {
    Remove-Item "src/enhancement/indexer.py" -Force
    Write-Output "   ✓ Deleted: src/enhancement/indexer.py"
    $deletedFiles++
}

Write-Output ""
Write-Output "   ✅ Individual files cleanup complete: $deletedFiles files deleted"
Write-Output ""

# ============================================================================
# STEP 2: Delete entire old folders
# ============================================================================

Write-Output "📁 STEP 2: Deleting entire old folders..."
Write-Output ""

# Extract folder (replaced by extraction/)
if (Test-Path "src/extract") {
    $extractFiles = (Get-ChildItem "src/extract" -File).Count
    Remove-Item "src/extract" -Recurse -Force
    Write-Output "   ✓ Deleted: src/extract/ folder ($extractFiles files)"
    $deletedFolders++
}

# Obs folder (replaced by observability/)
if (Test-Path "src/obs") {
    $obsFiles = (Get-ChildItem "src/obs" -File).Count
    Remove-Item "src/obs" -Recurse -Force
    Write-Output "   ✓ Deleted: src/obs/ folder ($obsFiles files)"
    $deletedFolders++
}

# Pipeline folder (split into synthesis/ & vectorization/)
if (Test-Path "src/pipeline") {
    $pipelineFiles = (Get-ChildItem "src/pipeline" -File).Count
    Remove-Item "src/pipeline" -Recurse -Force
    Write-Output "   ✓ Deleted: src/pipeline/ folder ($pipelineFiles files)"
    $deletedFolders++
}

# Utils folder (replaced by shared/)
if (Test-Path "src/utils") {
    $utilsFiles = (Get-ChildItem "src/utils" -File).Count
    Remove-Item "src/utils" -Recurse -Force
    Write-Output "   ✓ Deleted: src/utils/ folder ($utilsFiles files)"
    $deletedFolders++
}

Write-Output ""
Write-Output "   ✅ Folder cleanup complete: $deletedFolders folders deleted"
Write-Output ""

# ============================================================================
# FINAL SUMMARY
# ============================================================================

Write-Output "════════════════════════════════════════════════════════════════════════"
Write-Output ""
Write-Output "🎉 CLEANUP COMPLETED SUCCESSFULLY!"
Write-Output ""
Write-Output "📊 Summary:"
Write-Output "   - Individual files deleted: $deletedFiles"
Write-Output "   - Folders deleted: $deletedFolders"
Write-Output ""
Write-Output "✅ Your codebase now has ONLY the new production-ready structure!"
Write-Output ""
Write-Output "📁 Active modules remaining:"
Write-Output "   ✓ src/api/           (routes.py, schemas.py)"
Write-Output "   ✓ src/extraction/    (Phase 1: PDF Extraction)"
Write-Output "   ✓ src/enhancement/   (Phase 2: Enhancement)"
Write-Output "   ✓ src/prompts/       (Centralized Prompts)"
Write-Output "   ✓ src/synthesis/     (Phase 3: Markdown Synthesis)"
Write-Output "   ✓ src/vectorization/ (Phase 4: Vector Storage)"
Write-Output "   ✓ src/rag/           (Phase 5: RAG Inference)"
Write-Output "   ✓ src/observability/ (Token Tracking)"
Write-Output "   ✓ src/shared/        (Shared Utilities)"
Write-Output "   ✓ src/core/          (Core Infrastructure)"
Write-Output ""
Write-Output "🚀 Next steps:"
Write-Output "   1. Run server to verify: python -m uvicorn src.main:app --reload"
Write-Output "   2. Test complete workflow"
Write-Output "   3. Commit changes to Git"
Write-Output "   4. Ready for production!"
Write-Output ""
Write-Output "════════════════════════════════════════════════════════════════════════"
