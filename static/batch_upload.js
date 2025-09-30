/**
 * Batch Upload to Namespace - Frontend Logic
 * 
 * Handles:
 * - Namespace loading and selection
 * - File upload (drag & drop, file picker)
 * - Upload progress tracking
 * - Result display
 */

// ============================================================================
// GLOBAL STATE
// ============================================================================

let selectedFiles = [];
let namespaces = [];
let selectedNamespace = null;

// ============================================================================
// DOM ELEMENTS
// ============================================================================

const namespaceSelector = document.getElementById('namespace-selector');
const namespaceInfo = document.getElementById('namespace-info');
const fileUploadArea = document.getElementById('file-upload-area');
const fileInput = document.getElementById('file-input');
const fileList = document.getElementById('file-list');
const uploadBtn = document.getElementById('upload-btn');
const uploadForm = document.getElementById('upload-form');
const progressContainer = document.getElementById('progress-container');
const progressBar = document.getElementById('progress-bar');
const progressInfo = document.getElementById('progress-info');
const resultContainer = document.getElementById('result-container');

// ============================================================================
// INITIALIZATION
// ============================================================================

// Load namespaces on page load
loadNamespaces();

// ============================================================================
// EVENT LISTENERS
// ============================================================================

// Namespace selection
namespaceSelector.addEventListener('change', function() {
    const selectedOption = this.options[this.selectedIndex];
    if (selectedOption.dataset.namespace) {
        selectedNamespace = JSON.parse(selectedOption.dataset.namespace);
        displayNamespaceInfo(selectedNamespace);
        updateUploadButtonState();
    } else {
        selectedNamespace = null;
        namespaceInfo.style.display = 'none';
        updateUploadButtonState();
    }
});

// File upload area click
fileUploadArea.addEventListener('click', () => fileInput.click());

// File input change
fileInput.addEventListener('change', handleFileSelect);

// Drag and drop handlers
fileUploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    fileUploadArea.classList.add('dragover');
});

fileUploadArea.addEventListener('dragleave', () => {
    fileUploadArea.classList.remove('dragover');
});

fileUploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    fileUploadArea.classList.remove('dragover');
    const files = Array.from(e.dataTransfer.files);
    addFiles(files);
});

// Form submit
uploadForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    await performUpload();
});

// ============================================================================
// NAMESPACE FUNCTIONS
// ============================================================================

/**
 * Load namespaces from API
 */
async function loadNamespaces() {
    try {
        const response = await fetch('/namespaces/?active_only=true');
        if (!response.ok) throw new Error('Failed to load namespaces');
        
        const data = await response.json();
        namespaces = data.namespaces;
        
        // Populate selector
        namespaceSelector.innerHTML = '<option value="">-- Pilih Namespace --</option>';
        namespaces.forEach(ns => {
            const option = document.createElement('option');
            option.value = ns.id;
            const icon = ns.type === 'final' ? '🟢' : '🟡';
            option.textContent = `${ns.name} ${icon}`;
            option.dataset.namespace = JSON.stringify(ns);
            namespaceSelector.appendChild(option);
        });
    } catch (error) {
        console.error('Error loading namespaces:', error);
        namespaceSelector.innerHTML = '<option value="">Error loading namespaces</option>';
    }
}

/**
 * Display namespace information
 */
function displayNamespaceInfo(ns) {
    document.getElementById('ns-client').textContent = ns.client || 'N/A';
    document.getElementById('ns-type').textContent = ns.type;
    
    const statusEl = document.getElementById('ns-status');
    statusEl.innerHTML = ns.type === 'final'
        ? '<span class="badge badge-production">Production</span>'
        : '<span class="badge badge-testing">Testing</span>';
    
    document.getElementById('ns-description').textContent = ns.description;
    namespaceInfo.style.display = 'block';
}

// ============================================================================
// FILE HANDLING FUNCTIONS
// ============================================================================

/**
 * Handle file selection from input
 */
function handleFileSelect(e) {
    const files = Array.from(e.target.files);
    addFiles(files);
}

/**
 * Add files to the upload list
 */
function addFiles(files) {
    files.forEach(file => {
        // Validate file type
        if (!file.name.endsWith('.md') && !file.name.endsWith('.markdown')) {
            alert(`File ${file.name} bukan markdown file`);
            return;
        }

        // Validate file size (100MB max)
        if (file.size > 100 * 1024 * 1024) {
            alert(`File ${file.name} terlalu besar (max 100MB)`);
            return;
        }

        // Check if already added
        if (selectedFiles.some(f => f.name === file.name)) {
            return;
        }

        selectedFiles.push(file);
    });

    renderFileList();
    updateUploadButtonState();
}

/**
 * Render file list UI
 */
function renderFileList() {
    if (selectedFiles.length === 0) {
        fileList.innerHTML = '';
        return;
    }

    fileList.innerHTML = selectedFiles.map((file, index) => `
        <div class="file-item">
            <div class="file-item-info">
                <span class="file-icon">📝</span>
                <div>
                    <span class="file-name">${escapeHtml(file.name)}</span>
                    <span class="file-size">(${formatFileSize(file.size)})</span>
                </div>
            </div>
            <button type="button" class="remove-file-btn" onclick="removeFile(${index})">
                Hapus
            </button>
        </div>
    `).join('');
}

/**
 * Remove file from list
 */
window.removeFile = function(index) {
    selectedFiles.splice(index, 1);
    renderFileList();
    updateUploadButtonState();
};

/**
 * Update upload button state based on selections
 */
function updateUploadButtonState() {
    uploadBtn.disabled = !(selectedNamespace && selectedFiles.length > 0);
}

// ============================================================================
// UPLOAD FUNCTIONS
// ============================================================================

/**
 * Perform upload to server
 */
async function performUpload() {
    // Show progress
    progressContainer.style.display = 'block';
    resultContainer.style.display = 'none';
    uploadBtn.disabled = true;
    
    updateProgress(0, 'Mempersiapkan upload...');

    try {
        // Prepare form data
        const formData = new FormData();
        formData.append('namespace_id', selectedNamespace.id);
        selectedFiles.forEach(file => {
            formData.append('files', file);
        });

        updateProgress(20, 'Mengirim file ke server...');

        // Send request
        const response = await fetch('/namespaces/batch-upload', {
            method: 'POST',
            body: formData
        });

        updateProgress(50, 'Memproses file...');

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Upload failed');
        }

        const result = await response.json();
        
        updateProgress(100, 'Selesai!');

        // Display results
        setTimeout(() => {
            displayResults(result);
            progressContainer.style.display = 'none';
        }, 1000);

    } catch (error) {
        console.error('Upload error:', error);
        progressContainer.style.display = 'none';
        displayError(error.message);
    }
}

/**
 * Update progress bar
 */
function updateProgress(percent, message) {
    progressBar.style.width = percent + '%';
    progressBar.textContent = percent + '%';
    progressInfo.textContent = message;
}

/**
 * Display upload results
 */
function displayResults(result) {
    resultContainer.style.display = 'block';

    const alertClass = result.success ? 'alert-success' : 
                        (result.files_failed === 0 ? 'alert-success' : 'alert-warning');
    
    const alertIcon = result.success ? '✅' : '⚠️';
    const alertMessage = result.success 
        ? 'Upload berhasil! Semua file telah disimpan ke namespace.'
        : `Upload selesai dengan ${result.files_failed} file gagal.`;

    // Extract accumulated stats
    const accStats = result.namespace_accumulated_stats || {};
    const lastUpdated = accStats.last_updated ? new Date(accStats.last_updated).toLocaleString('id-ID') : 'N/A';
    
    resultContainer.innerHTML = `
        <div class="alert ${alertClass}">
            ${alertIcon} ${alertMessage}
        </div>

        <div class="result-summary">
            <h3>📊 Ringkasan Upload Ini</h3>
            <div class="stats-grid">
                <div class="stat-item">
                    <span class="stat-value">${result.files_processed}</span>
                    <span class="stat-label">File Diproses</span>
                </div>
                <div class="stat-item">
                    <span class="stat-value">${result.files_succeeded}</span>
                    <span class="stat-label">Berhasil</span>
                </div>
                <div class="stat-item">
                    <span class="stat-value">${result.files_failed}</span>
                    <span class="stat-label">Gagal</span>
                </div>
                <div class="stat-item">
                    <span class="stat-value">${result.total_chunks_uploaded}</span>
                    <span class="stat-label">Chunks Uploaded</span>
                </div>
            </div>
        </div>

        <div class="result-summary" style="background: linear-gradient(135deg, #f7fafc 0%, #edf2f7 100%); border: 2px solid #667eea;">
            <h3 style="color: #667eea;">📦 TOTAL DATA DI NAMESPACE "${escapeHtml(result.namespace_name)}"</h3>
            <p style="color: #718096; font-size: 14px; margin-bottom: 15px;">
                Informasi keseluruhan data yang tersimpan di Pinecone untuk namespace ini
            </p>
            <div class="stats-grid">
                <div class="stat-item" style="background: white; border: 2px solid #667eea;">
                    <span class="stat-value" style="color: #667eea;">${accStats.total_documents || 0}</span>
                    <span class="stat-label">Total Dokumen</span>
                </div>
                <div class="stat-item" style="background: white; border: 2px solid #48bb78;">
                    <span class="stat-value" style="color: #48bb78;">${formatNumber(accStats.total_chunks || 0)}</span>
                    <span class="stat-label">Total Chunks</span>
                </div>
                <div class="stat-item" style="background: white; border: 2px solid #ed8936;">
                    <span class="stat-value" style="color: #ed8936;">${formatNumber(accStats.total_tokens || 0)}</span>
                    <span class="stat-label">Total Tokens</span>
                </div>
                <div class="stat-item" style="background: white; border: 2px solid #9f7aea;">
                    <span class="stat-value" style="font-size: 16px; color: #9f7aea;">${lastUpdated}</span>
                    <span class="stat-label">Last Updated</span>
                </div>
            </div>
            <div style="margin-top: 15px; padding: 12px; background: rgba(102, 126, 234, 0.1); border-radius: 6px;">
                <p style="font-size: 13px; color: #4a5568; margin: 0;">
                    💡 <strong>Info:</strong> Data ini mencakup <strong>semua upload</strong> yang pernah dilakukan ke namespace ini.
                    Setiap upload baru akan <strong>menambah</strong> (tidak menggantikan) data yang sudah ada.
                </p>
            </div>
        </div>

        <div class="detailed-results">
            <h3 style="margin-bottom: 15px; color: #2d3748;">Detail Per File:</h3>
            ${result.detailed_results.map(file => `
                <div class="result-item ${file.status === 'success' ? 'success' : 'failed'}">
                    <div class="result-item-header">
                        <span class="result-filename">📝 ${escapeHtml(file.filename)}</span>
                        <span class="result-status ${file.status === 'success' ? 'success' : 'failed'}">
                            ${file.status.toUpperCase()}
                        </span>
                    </div>
                    <div class="result-details">
                        ${file.status === 'success' 
                            ? `Chunks uploaded: ${file.chunks_uploaded} | Tokens: ${file.input_tokens}`
                            : `Error: ${escapeHtml(file.error || 'Unknown error')}`
                        }
                    </div>
                </div>
            `).join('')}
        </div>
    `;

    // Reset form if successful
    if (result.success) {
        selectedFiles = [];
        fileInput.value = '';
        renderFileList();
        updateUploadButtonState();
    }

    uploadBtn.disabled = false;
}

/**
 * Display error message
 */
function displayError(message) {
    resultContainer.style.display = 'block';
    resultContainer.innerHTML = `
        <div class="alert alert-error">
            ❌ Error: ${escapeHtml(message)}
        </div>
    `;
    uploadBtn.disabled = false;
}

// ============================================================================
// UTILITY FUNCTIONS
// ============================================================================

/**
 * Format file size for display
 */
function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

/**
 * Format number with thousand separator
 */
function formatNumber(num) {
    return num.toLocaleString('id-ID');
}

/**
 * Escape HTML to prevent XSS
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
