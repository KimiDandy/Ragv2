/**
 * Inspigo RAG - Chat Interface Logic
 * Handles document upload, processing, and conversational Q&A
 */

// State Management
const state = {
    currentDocId: null,
    currentFile: null,
    pollingInterval: null,
    isProcessing: false,
    messageHistory: [],
    // Multi-file upload state
    selectedFiles: [],
    fileStatuses: new Map(),
    currentFileIndex: 0,
    isMultiFileMode: false
};

// DOM Elements
const elements = {
    uploadBox: null,
    fileInput: null,
    uploadSection: null,
    processingScreen: null,
    chatContainer: null,
    chatMessages: null,
    chatInput: null,
    sendButton: null,
    // Multi-file elements
    multiFileProgress: null,
    fileProgressList: null
};

// Initialize application
document.addEventListener('DOMContentLoaded', () => {
    initializeElements();
    setupEventListeners();
    console.log('Inspigo RAG Chat initialized');
});

// Get DOM elements
function initializeElements() {
    elements.uploadBox = document.getElementById('uploadBox');
    elements.fileInput = document.getElementById('fileInput');
    elements.uploadSection = document.getElementById('uploadSection');
    elements.processingScreen = document.getElementById('processingScreen');
    elements.chatContainer = document.getElementById('chatContainer');
    elements.chatMessages = document.getElementById('chatMessages');
    elements.chatInput = document.getElementById('chatInput');
    elements.sendButton = document.getElementById('sendButton');
    // Multi-file elements
    elements.multiFileProgress = document.getElementById('multiFileProgress');
    elements.fileProgressList = document.getElementById('fileProgressList');
}

// Setup event listeners
function setupEventListeners() {
    // File upload
    elements.uploadBox.addEventListener('click', () => elements.fileInput.click());
    elements.fileInput.addEventListener('change', handleFileSelect);
    
    // Drag and drop
    elements.uploadBox.addEventListener('dragover', handleDragOver);
    elements.uploadBox.addEventListener('dragleave', handleDragLeave);
    elements.uploadBox.addEventListener('drop', handleDrop);
    
    // Chat input
    elements.chatInput.addEventListener('keydown', handleInputKeydown);
    elements.chatInput.addEventListener('input', adjustTextareaHeight);
    elements.sendButton.addEventListener('click', handleSendMessage);
}

// File handling - now supports multiple files
function handleFileSelect(e) {
    const files = Array.from(e.target.files);
    
    if (files.length === 0) return;
    
    // Validate all files are PDFs
    const invalidFiles = files.filter(f => f.type !== 'application/pdf');
    if (invalidFiles.length > 0) {
        alert('Semua file harus berformat PDF');
        return;
    }
    
    // Handle single or multiple files
    if (files.length === 1) {
        // Single file - use old flow
        processFile(files[0]);
    } else {
        // Multiple files - use new flow
        processMultipleFiles(files);
    }
}

function handleDragOver(e) {
    e.preventDefault();
    elements.uploadBox.classList.add('dragging');
}

function handleDragLeave(e) {
    e.preventDefault();
    elements.uploadBox.classList.remove('dragging');
}

function handleDrop(e) {
    e.preventDefault();
    elements.uploadBox.classList.remove('dragging');
    
    const files = Array.from(e.dataTransfer.files);
    
    if (files.length === 0) return;
    
    // Validate all files are PDFs
    const invalidFiles = files.filter(f => f.type !== 'application/pdf');
    if (invalidFiles.length > 0) {
        alert('Semua file harus berformat PDF');
        return;
    }
    
    // Handle single or multiple files
    if (files.length === 1) {
        processFile(files[0]);
    } else {
        processMultipleFiles(files);
    }
}

// Process uploaded file
async function processFile(file) {
    state.currentFile = file;
    state.isProcessing = true;
    
    // Show processing screen
    elements.uploadSection.classList.add('hidden');
    elements.processingScreen.classList.remove('hidden');
    elements.processingScreen.style.display = 'flex';
    
    try {
        const formData = new FormData();
        formData.append('file', file);
        
        const response = await fetch('/documents/upload-auto', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) throw new Error('Upload gagal');
        
        const data = await response.json();
        state.currentDocId = data.document_id;
        
        console.log('File uploaded:', state.currentDocId);
        
        // Start polling for completion
        startProcessingPoll();
        
    } catch (error) {
        console.error('Upload error:', error);
        alert('Gagal mengunggah file: ' + error.message);
        resetToUpload();
    }
}

// === Multi-File Upload Functions ===

// Process multiple files in parallel (batch upload)
async function processMultipleFiles(files) {
    console.log(`Processing ${files.length} files in PARALLEL (batch upload)`);
    
    state.isMultiFileMode = true;
    state.selectedFiles = files;
    state.fileStatuses.clear();
    
    // Hide upload box, show multi-file progress
    elements.uploadBox.style.display = 'none';
    elements.multiFileProgress.classList.remove('hidden');
    
    // Initialize progress UI for all files
    elements.fileProgressList.innerHTML = '';
    
    try {
        // Upload ALL files at once to batch endpoint
        const formData = new FormData();
        files.forEach((file, idx) => {
            formData.append('files', file);
            
            // Initialize UI for this file
            const fileId = `file-${idx}`;
            state.fileStatuses.set(fileId, {
                file: file,
                status: 'uploading',
                progress: 0,
                docId: null,
                error: null
            });
            
            const progressItem = createFileProgressItem(fileId, file.name, 'uploading');
            elements.fileProgressList.appendChild(progressItem);
        });
        
        console.log(`Uploading ${files.length} files to batch endpoint...`);
        
        const response = await fetch('/documents/upload-batch', {
            method: 'POST',
            body: formData
        });
        
        if (!response.ok) {
            throw new Error(`Batch upload failed: ${response.statusText}`);
        }
        
        const batchData = await response.json();
        console.log(`Batch upload successful:`, batchData);
        
        // Map doc_ids to file statuses
        batchData.documents.forEach((docInfo, idx) => {
            const fileId = `file-${idx}`;
            const fileStatus = state.fileStatuses.get(fileId);
            if (fileStatus) {
                fileStatus.docId = docInfo.document_id;
                fileStatus.status = 'processing';
                updateFileProgress(fileId, 'processing', 5);
            }
        });
        
        // Start polling for ALL files in parallel
        await pollAllFilesParallel();
        
    } catch (error) {
        console.error('Batch upload error:', error);
        alert('Gagal mengunggah files: ' + error.message);
        resetToUpload();
    }
}

// Poll all files in parallel
async function pollAllFilesParallel() {
    console.log('Starting parallel polling for all files...');
    
    // Create polling tasks for all files
    const pollingTasks = [];
    
    state.fileStatuses.forEach((fileData, fileId) => {
        if (fileData.docId) {
            pollingTasks.push(pollFileProcessing(fileId, fileData.docId));
        }
    });
    
    // Wait for all files to complete
    await Promise.all(pollingTasks);
    
    // All files processed
    onAllFilesProcessed();
}

// Poll processing status for a specific file
async function pollFileProcessing(fileId, docId) {
    return new Promise((resolve, reject) => {
        const pollInterval = setInterval(async () => {
            try {
                const response = await fetch(`/documents/${docId}/status`);
                const status = await response.json();
                
                const progress = Math.round(status.progress_percentage || 0);
                
                // Build details with stage and ETA
                let details = null;
                if (status.current_stage && status.estimated_remaining_seconds) {
                    const eta_minutes = Math.ceil(status.estimated_remaining_seconds / 60);
                    details = `${getStageLabel(status.current_stage)} • ETA ${eta_minutes}m`;
                } else if (status.current_stage) {
                    details = getStageLabel(status.current_stage);
                }
                
                updateFileProgress(fileId, 'processing', progress, details);
                
                if (status.is_complete) {
                    clearInterval(pollInterval);
                    
                    // Mark as completed
                    const fileData = state.fileStatuses.get(fileId);
                    fileData.status = 'completed';
                    fileData.progress = 100;
                    updateFileProgress(fileId, 'completed', 100);
                    
                    console.log(`File ${fileId} completed: ${docId}`);
                    
                    // Process next file
                    state.currentFileIndex++;
                    resolve();
                    await processNextFile();
                }
                
                if (status.errors && status.errors.length > 0) {
                    clearInterval(pollInterval);
                    const errorMsg = status.errors[status.errors.length - 1].error;
                    
                    const fileData = state.fileStatuses.get(fileId);
                    fileData.status = 'error';
                    fileData.error = errorMsg;
                    updateFileProgress(fileId, 'error', 0, errorMsg);
                    
                    // Continue to next file
                    state.currentFileIndex++;
                    resolve();
                    await processNextFile();
                }
                
            } catch (error) {
                console.error('Status check error:', error);
            }
        }, 2000);
    });
}

// Create file progress item UI
function createFileProgressItem(fileId, fileName, status) {
    const div = document.createElement('div');
    div.className = 'file-progress-item';
    div.id = fileId;
    div.innerHTML = `
        <div class="file-progress-header">
            <div class="file-progress-name" title="${fileName}">${fileName}</div>
            <span class="file-progress-status ${status}">${getStatusText(status)}</span>
        </div>
        <div class="file-progress-bar">
            <div class="file-progress-bar-fill" style="width: 0%"></div>
        </div>
        <div class="file-progress-details"></div>
    `;
    return div;
}

// Update file progress
function updateFileProgress(fileId, status, progress, details = '') {
    const item = document.getElementById(fileId);
    if (!item) return;
    
    // Update item class
    item.className = `file-progress-item ${status}`;
    
    // Update status badge
    const statusBadge = item.querySelector('.file-progress-status');
    statusBadge.className = `file-progress-status ${status}`;
    statusBadge.textContent = getStatusText(status);
    
    // Update progress bar
    const progressBar = item.querySelector('.file-progress-bar-fill');
    progressBar.style.width = `${progress}%`;
    
    // Update details
    const detailsDiv = item.querySelector('.file-progress-details');
    if (details) {
        detailsDiv.textContent = details;
    } else if (status === 'uploading') {
        detailsDiv.innerHTML = `<span class="file-progress-spinner"></span>Mengunggah...`;
    } else if (status === 'processing') {
        detailsDiv.innerHTML = `<span class="file-progress-spinner"></span>Memproses... ${progress}%`;
    } else if (status === 'completed') {
        detailsDiv.textContent = 'Selesai ✓';
    } else if (status === 'waiting') {
        detailsDiv.textContent = 'Menunggu...';
    }
}

// Get status text
function getStatusText(status) {
    const statusTexts = {
        'uploading': 'Mengunggah',
        'waiting': 'Menunggu',
        'processing': 'Memproses',
        'completed': 'Selesai',
        'error': 'Error'
    };
    return statusTexts[status] || status;
}

// Get user-friendly stage label
function getStageLabel(stage) {
    const stageLabels = {
        'uploaded': 'Diunggah',
        'ocr_in_progress': 'OCR & Konversi',
        'ocr_completed': 'OCR Selesai',
        'enhancement_in_progress': 'Membuat Enhancement',
        'enhancement_completed': 'Enhancement Selesai',
        'auto_approval_completed': 'Auto-approval',
        'synthesis_in_progress': 'Menyusun Dokumen Final',
        'synthesis_completed': 'Sintesis Selesai',
        'vectorization_in_progress': 'Vektorisasi ke Pinecone',
        'vectorization_completed': 'Vektorisasi Selesai',
        'ready': 'Siap'
    };
    return stageLabels[stage] || stage;
}

// All files processed
function onAllFilesProcessed() {
    console.log('All files processed!');
    
    state.isMultiFileMode = false;
    
    // Count successes and failures
    let completed = 0;
    let failed = 0;
    
    state.fileStatuses.forEach(fileData => {
        if (fileData.status === 'completed') completed++;
        if (fileData.status === 'error') failed++;
    });
    
    console.log(`Results: ${completed} completed, ${failed} failed`);
    
    // Store last processed doc_id for chat
    const lastCompleted = Array.from(state.fileStatuses.values())
        .reverse()
        .find(f => f.status === 'completed');
    
    if (lastCompleted) {
        state.currentDocId = lastCompleted.docId;
    }
    
    // Show completion message and transition to chat after delay
    setTimeout(() => {
        elements.multiFileProgress.classList.add('hidden');
        elements.uploadSection.classList.add('hidden');
        elements.chatContainer.classList.remove('hidden');
        elements.chatContainer.style.display = 'flex';
        elements.chatInput.focus();
        
        console.log(`Chat ready. Total documents in namespace: ${completed}`);
    }, 2000);
}

// === Single File Functions (Original) ===

// Poll processing status
function startProcessingPoll() {
    if (state.pollingInterval) {
        clearInterval(state.pollingInterval);
    }
    
    state.pollingInterval = setInterval(async () => {
        try {
            const response = await fetch(`/documents/${state.currentDocId}/status`);
            const status = await response.json();
            
            console.log('Processing status:', status.current_stage, status.progress_percentage + '%');
            
            if (status.is_complete) {
                clearInterval(state.pollingInterval);
                onProcessingComplete();
            }
            
            if (status.errors && status.errors.length > 0) {
                clearInterval(state.pollingInterval);
                const errorMsg = status.errors[status.errors.length - 1].error;
                alert('Kesalahan saat memproses: ' + errorMsg);
                resetToUpload();
            }
            
        } catch (error) {
            console.error('Status check error:', error);
        }
    }, 2000);
}

// Processing complete - show chat
function onProcessingComplete() {
    state.isProcessing = false;
    
    elements.processingScreen.classList.add('hidden');
    elements.processingScreen.style.display = 'none';
    elements.chatContainer.classList.remove('hidden');
    elements.chatContainer.style.display = 'flex';
    
    elements.chatInput.focus();
    
    console.log('Processing complete. Chat ready.');
}

// Reset to upload screen
function resetToUpload() {
    state.isProcessing = false;
    state.currentDocId = null;
    state.currentFile = null;
    
    elements.processingScreen.classList.add('hidden');
    elements.processingScreen.style.display = 'none';
    elements.chatContainer.classList.add('hidden');
    elements.uploadSection.classList.remove('hidden');
    
    elements.fileInput.value = '';
}

// Chat input handling
function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSendMessage();
    }
}

function adjustTextareaHeight() {
    elements.chatInput.style.height = 'auto';
    elements.chatInput.style.height = Math.min(elements.chatInput.scrollHeight, 120) + 'px';
}

// Send message
async function handleSendMessage() {
    const message = elements.chatInput.value.trim();
    
    if (!message || state.isProcessing) return;
    
    // Add user message to UI
    addMessageToUI('user', message);
    
    // Clear input
    elements.chatInput.value = '';
    elements.chatInput.style.height = 'auto';
    
    // Show thinking state
    const thinkingId = addThinkingMessage();
    
    // Disable input
    state.isProcessing = true;
    elements.chatInput.disabled = true;
    elements.sendButton.disabled = true;
    
    try {
        const response = await fetch('/ask/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                prompt: message,
                document_id: state.currentDocId,
                version: 'v2',
                trace: true,
                k: 15  // Increased to 15 for better coverage across multiple documents
            })
        });
        
        console.log('Response status:', response.status);
        
        if (!response.ok) {
            const errorText = await response.text();
            console.error('Response not OK:', errorText);
            throw new Error('Gagal mendapatkan respons');
        }
        
        const data = await response.json();
        console.log('Response data:', data);
        
        // Remove thinking message
        removeMessage(thinkingId);
        
        // Validate response structure
        if (!data || typeof data.answer === 'undefined') {
            console.error('Invalid response structure:', data);
            throw new Error('Response tidak memiliki field answer');
        }
        
        // Add AI response
        addMessageToUI('ai', data.answer || 'Maaf, tidak dapat menemukan jawaban.', data.sources);
        
    } catch (error) {
        console.error('Query error:', error);
        removeMessage(thinkingId);
        addMessageToUI('ai', 'Maaf, terjadi kesalahan saat memproses pertanyaan Anda.', null, true);
    } finally {
        // Re-enable input
        state.isProcessing = false;
        elements.chatInput.disabled = false;
        elements.sendButton.disabled = false;
        elements.chatInput.focus();
    }
}

// Add message to UI
function addMessageToUI(type, content, sources = null, isError = false) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}`;
    
    const timestamp = new Date().toLocaleTimeString('id-ID', { 
        hour: '2-digit', 
        minute: '2-digit' 
    });
    
    if (type === 'user') {
        messageDiv.innerHTML = `
            <div class="message-content">
                <div class="message-bubble">${escapeHtml(content)}</div>
                <div class="message-time">${timestamp}</div>
            </div>
            <div class="message-avatar">U</div>
        `;
    } else {
        const bubbleClass = isError ? 'message-bubble error-message' : 'message-bubble';
        const renderedContent = isError ? escapeHtml(content) : renderMarkdown(content);
        
        let sourcesHtml = '';
        if (sources && sources.length > 0) {
            sourcesHtml = `
                <div class="sources-section">
                    <div class="sources-toggle" onclick="toggleSources(this)">
                        <div class="sources-toggle-label">
                            <span>📄</span>
                            <span>Sumber Jawaban</span>
                            <span class="sources-count">${sources.length}</span>
                        </div>
                        <span class="sources-toggle-icon">▼</span>
                    </div>
                    <div class="sources-list">
                        ${sources.map((source, idx) => {
                            const text = source.snippet || source.text || source.content || '';
                            const preview = text.length > 200 ? text.substring(0, 200) + '...' : text;
                            return `
                                <div class="source-item">
                                    <div class="source-header">Sumber ${idx + 1}</div>
                                    <div class="source-preview">${escapeHtml(preview)}</div>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>
            `;
        }
        
        messageDiv.innerHTML = `
            <div class="message-avatar">AI</div>
            <div class="message-content">
                <div class="${bubbleClass}">
                    <div class="answer-content">${renderedContent}</div>
                    ${sourcesHtml}
                </div>
                <div class="message-time">${timestamp}</div>
            </div>
        `;
    }
    
    elements.chatMessages.appendChild(messageDiv);
    scrollToBottom();
    
    return messageDiv;
}

// Add thinking message
function addThinkingMessage() {
    const thinkingDiv = document.createElement('div');
    thinkingDiv.className = 'message ai';
    thinkingDiv.id = 'thinking-message';
    
    thinkingDiv.innerHTML = `
        <div class="message-avatar">AI</div>
        <div class="message-content">
            <div class="ai-thinking">
                <span>Berpikir</span>
                <div class="thinking-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        </div>
    `;
    
    elements.chatMessages.appendChild(thinkingDiv);
    scrollToBottom();
    
    return 'thinking-message';
}

// Remove message
function removeMessage(id) {
    const element = document.getElementById(id);
    if (element) element.remove();
}

// Toggle sources visibility
function toggleSources(element) {
    element.classList.toggle('expanded');
}

// Make it available globally
window.toggleSources = toggleSources;

// Scroll to bottom of chat
function scrollToBottom() {
    setTimeout(() => {
        elements.chatMessages.scrollTop = elements.chatMessages.scrollHeight;
    }, 100);
}

// Render markdown
function renderMarkdown(text) {
    if (typeof marked !== 'undefined') {
        // Configure marked for better rendering
        marked.setOptions({
            breaks: true,
            gfm: true,
            headerIds: false,
            mangle: false
        });
        return marked.parse(text);
    }
    
    // Fallback: enhanced formatting
    let html = text;
    
    // Headers
    html = html.replace(/^### (.*$)/gim, '<h3>$1</h3>');
    html = html.replace(/^## (.*$)/gim, '<h2>$1</h2>');
    html = html.replace(/^# (.*$)/gim, '<h1>$1</h1>');
    
    // Bold and italic
    html = html.replace(/\*\*\*(.*?)\*\*\*/g, '<strong><em>$1</em></strong>');
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    
    // Lists
    html = html.replace(/^\* (.*$)/gim, '<li>$1</li>');
    html = html.replace(/^- (.*$)/gim, '<li>$1</li>');
    html = html.replace(/^\d+\. (.*$)/gim, '<li>$1</li>');
    
    // Wrap lists
    html = html.replace(/(<li>.*<\/li>)/s, '<ul>$1</ul>');
    
    // Line breaks
    html = html.replace(/\n\n/g, '</p><p>');
    html = html.replace(/\n/g, '<br>');
    
    // Wrap in paragraphs if not already
    if (!html.startsWith('<')) {
        html = '<p>' + html + '</p>';
    }
    
    return html;
}

// Escape HTML
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
