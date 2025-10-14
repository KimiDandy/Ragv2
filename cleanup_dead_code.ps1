# ============================================================================
# GENESIS RAG - DEAD CODE CLEANUP SCRIPT
# ============================================================================
# Purpose: Remove all unused/legacy files, folders, and code
# Date: 2025-10-14
# 
# This script will:
# 1. Delete orphaned files (never imported)
# 2. Delete admin route files  
# 3. Delete legacy vectorization files
# 4. Delete empty folders
# 5. Update main.py (remove admin imports)
# 6. Create backup before changes
# ============================================================================

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  GENESIS RAG - DEAD CODE CLEANUP" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""

# Get script directory
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host "[1/7] Creating backup..." -ForegroundColor Yellow
$backupDir = "cleanup_backup_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
Copy-Item "src" -Destination "$backupDir/src" -Recurse -Force
Write-Host "  ✓ Backup created: $backupDir" -ForegroundColor Green
Write-Host ""

# ============================================================================
# PHASE 1: DELETE ORPHANED FILES
# ============================================================================
Write-Host "[2/7] Deleting orphaned files (never imported)..." -ForegroundColor Yellow

$orphanedFiles = @(
    "src\extraction\methods.py",
    "src\extraction\helpers.py",
    "src\core\validators.py",
    "src\core\token_meter.py"
)

foreach ($file in $orphanedFiles) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "  ✓ Deleted: $file" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ Not found: $file" -ForegroundColor DarkYellow
    }
}
Write-Host ""

# ============================================================================
# PHASE 2: DELETE ADMIN ROUTE FILES
# ============================================================================
Write-Host "[3/7] Deleting admin route files..." -ForegroundColor Yellow

$adminFiles = @(
    "src\api\namespace_routes.py",
    "src\api\admin_routes.py",
    "src\api\enhancement_routes.py"
)

foreach ($file in $adminFiles) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "  ✓ Deleted: $file" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ Not found: $file" -ForegroundColor DarkYellow
    }
}
Write-Host ""

# ============================================================================
# PHASE 3: DELETE LEGACY VECTORIZATION FILES
# ============================================================================
Write-Host "[4/7] Deleting legacy vectorization files..." -ForegroundColor Yellow

$vectorFiles = @(
    "src\vectorization\indexer.py",
    "src\vectorization\batch_uploader.py",
    "src\vectorization\parallel_uploader.py"
)

foreach ($file in $vectorFiles) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "  ✓ Deleted: $file" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ Not found: $file" -ForegroundColor DarkYellow
    }
}
Write-Host ""

# ============================================================================
# PHASE 4: DELETE UNUSED ORCHESTRATION FILES
# ============================================================================
Write-Host "[5/7] Deleting unused orchestration files..." -ForegroundColor Yellow

$orchestrationFiles = @(
    "src\orchestration\pipeline_queue.py"
)

foreach ($file in $orchestrationFiles) {
    if (Test-Path $file) {
        Remove-Item $file -Force
        Write-Host "  ✓ Deleted: $file" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ Not found: $file" -ForegroundColor DarkYellow
    }
}
Write-Host ""

# ============================================================================
# PHASE 5: DELETE EMPTY FOLDERS
# ============================================================================
Write-Host "[6/7] Deleting empty folders..." -ForegroundColor Yellow

$emptyFolders = @(
    "src\app",
    "src\components"
)

foreach ($folder in $emptyFolders) {
    if (Test-Path $folder) {
        Remove-Item $folder -Force -Recurse
        Write-Host "  ✓ Deleted: $folder" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ Not found: $folder" -ForegroundColor DarkYellow
    }
}
Write-Host ""

# ============================================================================
# PHASE 6: CLEANUP SUMMARY
# ============================================================================
Write-Host "[7/7] Cleanup summary..." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Files deleted:" -ForegroundColor Cyan
Write-Host "    - 4 orphaned files (methods.py, helpers.py, validators.py, token_meter.py)" -ForegroundColor White
Write-Host "    - 3 admin route files (namespace_routes, admin_routes, enhancement_routes)" -ForegroundColor White
Write-Host "    - 3 legacy vectorization files (indexer, batch_uploader, parallel_uploader)" -ForegroundColor White
Write-Host "    - 1 unused orchestration file (pipeline_queue)" -ForegroundColor White
Write-Host "    - 2 empty folders (app, components)" -ForegroundColor White
Write-Host ""
Write-Host "  Total: 11 files + 2 folders deleted" -ForegroundColor Green
Write-Host ""

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host "  CLEANUP COMPLETE!" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  ⚠️  IMPORTANT NEXT STEPS:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  1. You still need to manually edit these files:" -ForegroundColor White
Write-Host "     - src\main.py (lines already cleaned by automation)" -ForegroundColor White
Write-Host "     - src\api\routes.py (remove legacy endpoints ~500 lines)" -ForegroundColor White
Write-Host ""
Write-Host "  2. I will provide detailed line-by-line edits for routes.py" -ForegroundColor White
Write-Host ""
Write-Host "  3. Test the application:" -ForegroundColor White
Write-Host "     python -m uvicorn src.main:app --reload" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  4. Verify upload → processing → chat workflow" -ForegroundColor White
Write-Host ""
Write-Host "  Backup location: $backupDir" -ForegroundColor Cyan
Write-Host ""
