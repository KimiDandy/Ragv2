// Cache busters: v3.0 - NEW PDF WORKFLOW
let currentDocumentId = null;
let markdownV1 = '';
let suggestionsState = []; 
let pollTimer = null;
let pollAttempts = 0;
let pollIntervalMs = 1200; 
const maxPollAttempts = 60; 
let progressTimer = null;
let progressIntervalMs = 1200;

const API_BASE_URL = window.location.origin;


const uploadContainer = document.getElementById('upload-container');
const loadingContainer = document.getElementById('loading-container');
const mainLayout = document.getElementById('main-layout');
const stepperEl = document.getElementById('stepper');
const modeDialog = document.getElementById('mode-dialog');
const startConversionBtn = document.getElementById('start-conversion-btn');
const modeBasic = document.getElementById('mode-basic');
const modeSmart = document.getElementById('mode-smart');

// Panel 1: Preview
const v1Rendered = document.getElementById('v1-rendered');
const v1Raw = document.getElementById('v1-raw');
const toggleRaw = document.getElementById('toggle-raw');
let highlightedMarkdownV1 = '';

// Panel 2: Suggestions
const startEnhancementBtn = document.getElementById('start-enhancement-btn');
const statusBadge = document.getElementById('status-badge');
const suggestionsList = document.getElementById('suggestions-list');
const suggestionsEmpty = document.getElementById('suggestions-empty');
const finalizeBtn = document.getElementById('finalize-btn');
const approveAllBtn = document.getElementById('approve-all-btn');
const rejectAllBtn = document.getElementById('reject-all-btn');

// Panel 3: Chat
const queryInput = document.getElementById('query-input');
const queryBtn = document.getElementById('query-btn');
const versionSelect = document.getElementById('version-select');
const resultsContainer = document.getElementById('results-container');
const unenrichedResultDiv = document.getElementById('unenriched-result');
const enrichedResultDiv = document.getElementById('enriched-result');
const v1Card = document.getElementById('v1-card');
const v2Card = document.getElementById('v2-card');
const fileInput = document.getElementById('file-input');
const fileNameDiv = document.getElementById('file-name');

// Bukti UI
const toggleEvidence = document.getElementById('toggle-evidence');
const evidenceContainer = document.getElementById('evidence-container');
const v1EvidenceList = document.getElementById('v1-evidence-list');
const v2EvidenceList = document.getElementById('v2-evidence-list');
const v1EvidenceCard = document.getElementById('v1-evidence-card');
const v2EvidenceCard = document.getElementById('v2-evidence-card');

if (toggleEvidence) {
    toggleEvidence.addEventListener('change', () => {
        if (!evidenceContainer) return;
        const anyItems = ((v1EvidenceList && v1EvidenceList.children.length) || (v2EvidenceList && v2EvidenceList.children.length));
        evidenceContainer.style.display = (toggleEvidence.checked && anyItems) ? 'block' : 'none';
    });
}


const messageContainer = document.createElement('div');
messageContainer.className = 'message-container';
document.body.appendChild(messageContainer);

function showMessage(message, type = 'error') {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${type}`;
    messageDiv.textContent = message;
    messageContainer.appendChild(messageDiv);
    setTimeout(() => {
        messageDiv.style.opacity = '0';
        setTimeout(() => messageDiv.remove(), 500);
    }, 5000);
}

function renderEvidence(sources, targetEl) {
    if (!targetEl) return;
    targetEl.innerHTML = '';
    if (!Array.isArray(sources) || sources.length === 0) {
        targetEl.innerHTML = '<div class="muted">Tidak ada evidence dari LangChain.</div>';
        return;
    }
    
    // Header untuk evidence
    const headerDiv = document.createElement('div');
    headerDiv.className = 'evidence-header';
    headerDiv.innerHTML = `<h4>📄 Dokumen Evidence (${sources.length} chunk)</h4>`;
    targetEl.appendChild(headerDiv);
    
    sources.forEach((s, idx) => {
        const item = document.createElement('div');
        item.className = 'evidence-item';
        const hasScore = (s && typeof s.score === 'number' && !Number.isNaN(s.score));
        const scorePct = hasScore ? Math.round(s.score * 100) : null;
        const meta = s && s.metadata ? s.metadata : {};
        
        // Format metadata yang lebih informatif
        const relevantMeta = [];
        if (meta.source_document) relevantMeta.push(`Doc: ${meta.source_document}`);
        if (meta.version) relevantMeta.push(`Ver: ${meta.version}`);
        if (meta.page) relevantMeta.push(`Hal: ${meta.page}`);
        if (meta.chunk_id) relevantMeta.push(`Chunk: ${meta.chunk_id}`);
        if (meta.char_start && meta.char_end) {
            relevantMeta.push(`Pos: ${meta.char_start}-${meta.char_end}`);
        }
        
        item.innerHTML = `
            <div class="evidence-head">
                <span class="evidence-rank">#${idx+1}</span>
                <span class="evidence-score">Similarity: ${scorePct === null ? '—' : scorePct + '%'}</span>
            </div>
            <div class="evidence-snippet">${escapeHtml(s.snippet || '')}</div>
            <div class="evidence-meta">${escapeHtml(relevantMeta.join(' • '))}</div>
        `;
        targetEl.appendChild(item);
    });
}

function displayTokenUsage(tokenUsage, version) {
    // Create or find token usage container
    let tokenContainer = document.getElementById('token-usage-container');
    if (!tokenContainer) {
        tokenContainer = document.createElement('div');
        tokenContainer.id = 'token-usage-container';
        tokenContainer.className = 'token-usage-container';
        
        // Insert after chat results
        const chatContainer = document.getElementById('chat-container');
        if (chatContainer) {
            chatContainer.appendChild(tokenContainer);
        }
    }
    tokenContainer.style.display = 'block';
    
    const versionLabel = version === 'v1' ? 'Original' : 'Enhanced';
    const inputTokens = tokenUsage.input_tokens || 0;
    const outputTokens = tokenUsage.output_tokens || 0;
    const totalTokens = tokenUsage.total_tokens || (inputTokens + outputTokens);
    
    tokenContainer.innerHTML = `
        <div class="token-usage-box">
            <h4>🔢 Token Usage - ${versionLabel}</h4>
            <div class="token-stats">
                <div class="token-stat">
                    <span class="token-label">Input:</span>
                    <span class="token-value">${inputTokens.toLocaleString()}</span>
                </div>
                <div class="token-stat">
                    <span class="token-label">Output:</span>
                    <span class="token-value">${outputTokens.toLocaleString()}</span>
                </div>
                <div class="token-stat total">
                    <span class="token-label">Total:</span>
                    <span class="token-value">${totalTokens.toLocaleString()}</span>
                </div>
            </div>
        </div>
    `;
}

function displayTokenUsageComparison(v1Tokens, v2Tokens) {
    // Create or find token usage container
    let tokenContainer = document.getElementById('token-usage-container');
    if (!tokenContainer) {
        tokenContainer = document.createElement('div');
        tokenContainer.id = 'token-usage-container';
        tokenContainer.className = 'token-usage-container';
        
        // Insert after chat results
        const chatContainer = document.getElementById('chat-container');
        if (chatContainer) {
            chatContainer.appendChild(tokenContainer);
        }
    }
    
    const v1Input = v1Tokens.input_tokens || 0;
    const v1Output = v1Tokens.output_tokens || 0;
    const v1Total = v1Tokens.total_tokens || (v1Input + v1Output);
    
    const v2Input = v2Tokens.input_tokens || 0;
    const v2Output = v2Tokens.output_tokens || 0;
    const v2Total = v2Tokens.total_tokens || (v2Input + v2Output);
    
    const totalCombined = v1Total + v2Total;
    
    tokenContainer.innerHTML = `
        <div class="token-usage-box comparison">
            <h4>🔢 Token Usage Comparison</h4>
            <div class="token-comparison">
                <div class="token-version">
                    <h5>Original (v1)</h5>
                    <div class="token-stats">
                        <div class="token-stat">
                            <span class="token-label">Input:</span>
                            <span class="token-value">${v1Input.toLocaleString()}</span>
                        </div>
                        <div class="token-stat">
                            <span class="token-label">Output:</span>
                            <span class="token-value">${v1Output.toLocaleString()}</span>
                        </div>
                        <div class="token-stat total">
                            <span class="token-label">Total:</span>
                            <span class="token-value">${v1Total.toLocaleString()}</span>
                        </div>
                    </div>
                </div>
                <div class="token-version">
                    <h5>Enhanced (v2)</h5>
                    <div class="token-stats">
                        <div class="token-stat">
                            <span class="token-label">Input:</span>
                            <span class="token-value">${v2Input.toLocaleString()}</span>
                        </div>
                        <div class="token-stat">
                            <span class="token-label">Output:</span>
                            <span class="token-value">${v2Output.toLocaleString()}</span>
                        </div>
                        <div class="token-stat total">
                            <span class="token-label">Total:</span>
                            <span class="token-value">${v2Total.toLocaleString()}</span>
                        </div>
                    </div>
                </div>
                <div class="token-summary">
                    <div class="token-stat grand-total">
                        <span class="token-label">Combined Total:</span>
                        <span class="token-value">${totalCombined.toLocaleString()}</span>
                    </div>
                </div>
            </div>
        </div>
    `;
}

fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        fileNameDiv.textContent = `File terpilih: ${file.name}`;
        uploadPdf(file);
    }
});

async function uploadPdf(file) {
    const formData = new FormData();
    formData.append('file', file);
    try {
        const response = await fetch(`${API_BASE_URL}/upload-pdf`, {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Gagal mengunggah file');
        currentDocumentId = data.document_id;
        // Tampilkan dialog mode
        modeDialog.style.display = 'block';
        showMessage('File terunggah. Pilih mode lalu mulai konversi.', 'success');
    } catch (error) {
        showMessage(`Terjadi kesalahan: ${error.message}`);
        loadingContainer.style.display = 'none';
        uploadContainer.style.display = 'block';
    }
}

startConversionBtn?.addEventListener('click', async () => {
    if (!currentDocumentId) { showMessage('Unggah PDF terlebih dahulu.'); return; }
    const mode = (modeSmart && modeSmart.checked) ? 'smart' : 'basic';
    try {
        // UI states
        uploadContainer.style.display = 'none';
        modeDialog.style.display = 'none';
        loadingContainer.style.display = 'block';
        setStatus('Berjalan...', 'running');
        setStepperPhase('enhance');

        const resp = await fetch(`${API_BASE_URL}/start-conversion`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document_id: currentDocumentId, mode })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Gagal memulai konversi');
        pollConversion(currentDocumentId);
    } catch (err) {
        showMessage(err.message || 'Gagal memulai konversi');
        loadingContainer.style.display = 'none';
        uploadContainer.style.display = 'block';
        setStatus('Galat', 'error');
    }
});

async function pollConversion(docId) {
    try {
        const resp = await fetch(`${API_BASE_URL}/conversion-progress/${docId}`);
        const data = await resp.json();
        if (resp.ok) {
            const pct = Math.round(((data && data.percent) ? data.percent : 0) * 100);
            setStatus(`Berjalan... ${pct}%`, 'running');
            if ((data.status || '') === 'complete' || pct >= 100) {
                await loadConversionResult(docId);
                return;
            }
        }
        setTimeout(() => pollConversion(docId), 1200);
    } catch (_) {
        setTimeout(() => pollConversion(docId), 1500);
    }
}

async function loadConversionResult(docId) {
    try {
        const resp = await fetch(`${API_BASE_URL}/conversion-result/${docId}`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Gagal mengambil hasil konversi');
        markdownV1 = data.markdown_content || '';
        renderV1Preview();
        loadingContainer.style.display = 'none';
        mainLayout.style.display = 'flex';
        setStatus('Siap', 'ready');
        setStepperPhase('enhance');
        showMessage('Konversi selesai. Markdown siap!', 'success');
    } catch (e) {
        showMessage(e.message || 'Gagal memuat hasil konversi');
        loadingContainer.style.display = 'none';
        uploadContainer.style.display = 'block';
        setStatus('Galat', 'error');
    }
}

function renderV1Preview() {
    try {
        const md = highlightedMarkdownV1 || markdownV1 || '';
        if (window.marked) {
            v1Rendered.innerHTML = marked.parse(md);
        } else {
            v1Rendered.textContent = md;
        }
        v1Raw.textContent = markdownV1 || '';
    } catch (e) {
        v1Rendered.textContent = highlightedMarkdownV1 || markdownV1 || '';
        v1Raw.textContent = markdownV1 || '';
    }
}

function clearHighlights() {
    highlightedMarkdownV1 = '';
    renderV1Preview();
    // Clear selected cards
    const cards = suggestionsList.querySelectorAll('.suggestion-card');
    cards.forEach(c => c.classList.remove('card-selected'));
}

function setStatus(text, tone = 'idle') {
    statusBadge.textContent = text;
    statusBadge.className = `badge ${tone}`; 
}

const stepOrder = ['upload','enhance','curate','finalize','qa'];
function setStepperPhase(phase) {
    if (!stepperEl) return;
    const currentIdx = stepOrder.indexOf(phase);
    stepOrder.forEach((key, idx) => {
        const step = document.getElementById(`step-${key}`);
        if (!step) return;
        step.classList.remove('is-active','is-complete','is-pending');
        if (idx < currentIdx) step.classList.add('is-complete');
        else if (idx === currentIdx) step.classList.add('is-active');
        else step.classList.add('is-pending');
    });
}

setStepperPhase('upload');

toggleRaw?.addEventListener('change', (e) => {
    const showRaw = e.target.checked;
    v1Raw.style.display = showRaw ? 'block' : 'none';
    v1Rendered.style.display = showRaw ? 'none' : 'block';
});

function updateBulkButtonsState() {
    const hasSuggestions = (suggestionsState && suggestionsState.length > 0);
    const hasCurated = suggestionsState.some(s => ['approved','edited'].includes((s.status||'').toLowerCase()));
    approveAllBtn && (approveAllBtn.disabled = !hasSuggestions);
    rejectAllBtn && (rejectAllBtn.disabled = !hasSuggestions);
    finalizeBtn && (finalizeBtn.disabled = !hasCurated);
}

startEnhancementBtn?.addEventListener('click', async () => {
    if (!currentDocumentId) return;
    startEnhancementBtn.disabled = true;
    setStatus('Enhancement V2: Memulai...', 'running');
    setStepperPhase('enhance');
    
    // Show enhanced status message
    showMessage('🚀 Memulai Enhancement...', 'info');
    
    try {
        const resp = await fetch(`${API_BASE_URL}/start-enhancement/${currentDocumentId}`, { method: 'POST' });
        if (!resp.ok) {
            const d = await resp.json().catch(()=>({detail:'Gagal memulai Enhancement'}));
            throw new Error(d.detail || 'Gagal memulai Enhancement');
        }
        
        // Show progress phases
        const phases = [
            '🔄 Membuat token windows...',
            '🗺️ Map-Reduce planning...',
            '⚡ Micro-batch generation...',
            '📝 Markdown v2 synthesis...'
        ];
        
        let phaseIndex = 0;
        const phaseInterval = setInterval(() => {
            if (phaseIndex < phases.length) {
                setStatus(phases[phaseIndex], 'running');
                phaseIndex++;
            } else {
                clearInterval(phaseInterval);
                setStatus('Enhancement: Berjalan...', 'running');
            }
        }, 1200);
        
        if (pollTimer) clearTimeout(pollTimer);
        if (progressTimer) clearTimeout(progressTimer);
        pollAttempts = 0;
        // start faster to improve UX
        pollIntervalMs = 1200;
        progressIntervalMs = 1200;
        scheduleNextPoll();
        scheduleProgressPoll();
    } catch (err) {
        showMessage(err.message || 'Gagal memulai Enhancement', 'error');
        startEnhancementBtn.disabled = false;
        setStatus('Galat', 'error');
    }
});

function scheduleNextPoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(fetchSuggestions, pollIntervalMs);
}

function scheduleProgressPoll() {
    if (progressTimer) clearTimeout(progressTimer);
    progressTimer = setTimeout(fetchProgress, progressIntervalMs);
}

async function fetchSuggestions() {
    try {
        const resp = await fetch(`${API_BASE_URL}/get-suggestions/${currentDocumentId}`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Gagal mengambil saran');
        const list = Array.isArray(data.suggestions) ? data.suggestions : [];
        if (list.length > 0) {
            suggestionsState = list.map(s => ({ ...s }));
            renderSuggestions();
            setStatus('Siap', 'ready');
            setStepperPhase('curate');
            updateBulkButtonsState();
            if (pollTimer) clearTimeout(pollTimer);
            if (progressTimer) clearTimeout(progressTimer);
        } else {
            setStatus('Berjalan...', 'running');
            pollAttempts += 1;
            pollIntervalMs = Math.min(Math.round(pollIntervalMs * 1.5), 15000);
            if (pollAttempts >= maxPollAttempts) {
                if (pollTimer) clearTimeout(pollTimer);
                if (progressTimer) clearTimeout(progressTimer);
                setStatus('Waktu habis', 'error');
                showMessage('Proses peningkatan memakan waktu lebih lama dari biasanya. Coba lagi nanti.');
                startEnhancementBtn.disabled = false;
                return;
            }
            scheduleNextPoll();
        }
    } catch (e) {
        setStatus('Galat', 'error');
        startEnhancementBtn.disabled = false;
        if (pollTimer) clearTimeout(pollTimer);
        if (progressTimer) clearTimeout(progressTimer);
    }
}

async function fetchProgress() {
    try {
        if (!currentDocumentId) return;
        const resp = await fetch(`${API_BASE_URL}/progress/${currentDocumentId}`);
        const data = await resp.json();
        if (resp.ok) {
            const pct = Math.min(100, Math.max(0, Math.round(((data && data.percent) ? data.percent : 0) * 100)));
            if (!suggestionsState.length) {
                setStatus(`Berjalan... ${pct}%`, 'running');
            }
            const status = (data && data.status) || 'running';
            if (status === 'complete' || pct >= 100) {
                if (progressTimer) clearTimeout(progressTimer);
            } else {
                progressIntervalMs = Math.min(Math.round(progressIntervalMs * 1.2), 8000);
                scheduleProgressPoll();
            }
        } else {
            progressIntervalMs = Math.min(Math.round(progressIntervalMs * 1.5), 10000);
            scheduleProgressPoll();
        }
    } catch (_) {
        if (progressTimer) clearTimeout(progressTimer);
    }
}

// Duplikasi function dihapus - menggunakan yang lengkap di bawah

// ============ SOURCE HIGHLIGHTING IN MARKDOWN (Panel atas) ============
function buildHighlightTargets(previews) {
    const targets = [];
    console.log('Building targets from previews:', previews);
    
    (previews || []).forEach(p => {
        console.log('Processing preview:', p);
        
        if (p.table_preview) {
            console.log('Table preview found:', p.table_preview);
            // Use header and hit row text for table highlighting
            const lines = String(p.table_preview).split('\n').filter(Boolean);
            lines.forEach(ln => {
                // Extract cell contents for more flexible matching
                const cells = ln.split('|').map(c => c.trim()).filter(c => c && c !== ':---' && !c.match(/^:?-+:?$/));
                cells.forEach(cell => {
                    if (cell.length >= 3) {
                        targets.push(cell.slice(0, 80));
                        console.log('Added table cell target:', cell.slice(0, 80));
                    }
                });
                // Also add the whole line
                const cleaned = ln.replace(/\|/g, ' ').replace(/\s+/g, ' ').trim();
                if (cleaned.length >= 6) {
                    targets.push(cleaned.slice(0, 120));
                    console.log('Added table line target:', cleaned.slice(0, 120));
                }
            });
        } 
        
        if (p.snippet) {
            console.log('Snippet found:', p.snippet);
            // For paragraphs, extract meaningful phrases
            const cleaned = String(p.snippet).replace(/\s+/g, ' ').trim();
            if (cleaned.length >= 6) {
                // Add full snippet
                targets.push(cleaned.slice(0, 160));
                console.log('Added snippet target:', cleaned.slice(0, 50) + '...');
                
                // Extract sentences for better matching
                const sentences = cleaned.split(/[.!?]+/).filter(s => s.trim().length > 10);
                sentences.forEach(sentence => {
                    const trimmed = sentence.trim();
                    if (trimmed.length >= 10) {
                        targets.push(trimmed.slice(0, 120));
                        console.log('Added sentence target:', trimmed.slice(0, 30) + '...');
                    }
                });
            }
        }
        
        // Also try common financial terms from the preview data
        const commonTerms = ['BI RATE', 'FED RATE', 'INTEREST RATES', 'BONDS'];
        const previewText = (p.snippet || p.table_preview || '').toUpperCase();
        commonTerms.forEach(term => {
            if (previewText.includes(term)) {
                targets.push(term);
                console.log('Added common term target:', term);
            }
        });
    });
    
    // Deduplicate and filter out very short targets
    const finalTargets = Array.from(new Set(targets)).filter(t => t.length >= 3);
    console.log('Final targets:', finalTargets);
    return finalTargets;
}

function escapeRegex(s) {
    return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function highlightMarkdownWithTargets(md, targets) {
    let out = md;
    // Sort targets by length descending to match longer phrases first
    const sortedTargets = targets.sort((a, b) => b.length - a.length);
    
    sortedTargets.forEach(t => {
        if (t.length < 3) return;
        
        // Create flexible pattern that handles whitespace variations
        const pat = escapeRegex(t)
            .replace(/\s+/g, '\\s+')  // More flexible whitespace matching
            .replace(/\\\|/g, '\\s*\\|\\s*');  // Handle table pipes with optional spaces
        
        try {
            const re = new RegExp(`(${pat})`, 'gi');
            // Use HTML mark tags that will survive markdown parsing
            out = out.replace(re, '<span class="src-highlight">$1</span>');
        } catch (e) {
            // Skip if regex is invalid
            console.warn('Invalid regex for target:', t);
        }
    });
    return out;
}

function highlightInRenderedHTML(targets) {
    console.log('Trying direct HTML highlighting...');
    let html = v1Rendered.innerHTML;
    const sortedTargets = targets.sort((a, b) => b.length - a.length);
    
    sortedTargets.forEach(t => {
        if (t.length < 3) return;
        
        const pat = escapeRegex(t)
            .replace(/\s+/g, '\\s+')
            .replace(/\\\|/g, '\\s*\\|\\s*');
        
        try {
            const re = new RegExp(`(${pat})`, 'gi');
            html = html.replace(re, '<mark class="src-highlight">$1</mark>');
        } catch (e) {
            console.warn('Invalid HTML regex for target:', t);
        }
    });
    
    if (html !== v1Rendered.innerHTML) {
        v1Rendered.innerHTML = html;
        console.log('Applied direct HTML highlighting');
        const highlights = v1Rendered.querySelectorAll('mark.src-highlight, span.src-highlight');
        if (highlights.length > 0) {
            highlights[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }
}

function scrollToFirstHighlight() {
    setTimeout(() => {
        const el = v1Rendered.querySelector('mark.src-highlight, span.src-highlight');
        if (el && typeof el.scrollIntoView === 'function') {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }, 50);
}

function highlightSourcesInPreview(suggestion) {
    const previews = Array.isArray(suggestion.source_previews) ? suggestion.source_previews : [];
    const targets = buildHighlightTargets(previews);
    
    console.log('Original markdown length:', markdownV1.length);
    console.log('Highlighting targets:', targets);
    
    if (targets.length > 0) {
        highlightedMarkdownV1 = highlightMarkdownWithTargets(markdownV1, targets);
        console.log('Highlighted markdown length:', highlightedMarkdownV1.length);
        console.log('Contains highlights:', highlightedMarkdownV1.includes('src-highlight'));
    } else {
        // Reset to normal if no targets
        highlightedMarkdownV1 = '';
    }
    
    renderV1Preview();
    
    if (targets.length > 0) {
        // Wait longer for DOM update and check if highlights exist
        setTimeout(() => {
            const highlights = v1Rendered.querySelectorAll('mark.src-highlight, span.src-highlight');
            console.log('Found highlights in DOM:', highlights.length);
            if (highlights.length > 0) {
                highlights[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
            } else {
                console.error('No highlights found in DOM after rendering!');
                // Fallback: try direct HTML highlighting
                highlightInRenderedHTML(targets);
            }
        }, 100);
    }
}

// Handle click on suggestion cards to trigger highlight  
function setupCardClickHandlers() {
    const cards = suggestionsList.querySelectorAll('.suggestion-card');
    cards.forEach((card, idx) => {
        card.addEventListener('click', (e) => {
            const target = e.target;
            
            // Ignore clicks on buttons and textarea
            if (target.closest('[data-action]')) return;
            if (target.closest('textarea')) return;
            if (target.tagName === 'BUTTON') return;
            if (target.tagName === 'TEXTAREA') return;
            
            // Highlight this card and show sources in viewer
            cards.forEach(c => c.classList.remove('card-selected'));
            card.classList.add('card-selected');
            
            if (suggestionsState[idx]) {
                highlightSourcesInPreview(suggestionsState[idx]);
            }
        });
    });
}

function renderSuggestions() {
    suggestionsEmpty.style.display = suggestionsState.length ? 'none' : 'block';
    suggestionsList.innerHTML = '';
    suggestionsState.forEach((s, idx) => {
        const card = document.createElement('div');
        card.className = 'suggestion-card';
        card.dataset.idx = idx;
        
        
        card.innerHTML = `
            <div class="suggestion-head">
               <span class="suggestion-type ${s.type}">${labelForType(s.type)}</span>
               <span class="suggestion-hint">Klik untuk lihat rujukan di viewer atas</span>
            </div>
            <div class="suggestion-content">
                ${s.generated_content || `[DEBUG] No content found. Available fields: ${Object.keys(s).join(', ')}`}
            </div>
            <textarea class="suggestion-editor" data-idx="${idx}" style="display:none;">${s.generated_content || ''}</textarea>
            <div class="suggestion-actions">
                <button class="btn btn-secondary" data-action="edit" data-idx="${idx}">Edit</button>
                <button class="btn btn-secondary" data-action="approve" data-idx="${idx}">Setujui</button>
                <button class="btn btn-secondary" data-action="reject" data-idx="${idx}">Tolak</button>
            </div>
        `;

        const st = (s.status || '').toLowerCase();
        if (st) card.dataset.state = st;
        suggestionsList.appendChild(card);
    });

    suggestionsList.querySelectorAll('.suggestion-editor').forEach(el => {
        const autosize = (ta) => { ta.style.height = 'auto'; ta.style.height = `${ta.scrollHeight}px`; };
        autosize(el);
        el.addEventListener('input', (e) => {
            const i = Number(e.target.dataset.idx);
            const val = e.target.value;
            suggestionsState[i].generated_content = val;
            suggestionsState[i].status = 'edited';
            autosize(e.target);
            updateBulkButtonsState();
        });
    });
    suggestionsList.querySelectorAll('[data-action]')?.forEach(btn => {
        btn.addEventListener('click', (e) => {
            const action = e.target.dataset.action;
            const i = Number(e.target.dataset.idx);
            const card = e.target.closest('.suggestion-card');
            
            if (action === 'edit') {
                const contentDiv = card.querySelector('.suggestion-content');
                const textarea = card.querySelector('.suggestion-editor');
                const editBtn = card.querySelector('[data-action="edit"]');
                
                if (textarea.style.display === 'none') {
                    // Show editor
                    contentDiv.style.display = 'none';
                    textarea.style.display = 'block';
                    textarea.focus();
                    editBtn.textContent = 'Simpan';
                } else {
                    // Save and hide editor
                    const newContent = textarea.value;
                    suggestionsState[i].generated_content = newContent;
                    suggestionsState[i].status = 'edited';
                    contentDiv.innerHTML = newContent || 'Konten enhancement tidak tersedia...';
                    contentDiv.style.display = 'block';
                    textarea.style.display = 'none';
                    editBtn.textContent = 'Edit';
                    updateBulkButtonsState();
                }
            } else if (action === 'approve') {
                suggestionsState[i].status = 'approved';
            } else if (action === 'reject') {
                suggestionsState[i].status = 'rejected';
            }
            
            // visual cue
            card.dataset.state = suggestionsState[i].status;
            updateBulkButtonsState();
        });
    });

    // Setup click handlers for highlighting
    setupCardClickHandlers();
    updateBulkButtonsState();
}

// Add double-click to clear highlights on empty area
suggestionsList.addEventListener('dblclick', (e) => {
    if (e.target === suggestionsList) {
        clearHighlights();
    }
});

// Also add a clear button to the preview panel
v1Rendered.addEventListener('dblclick', () => {
    clearHighlights();
});

// Test function for debugging
function testHighlighting() {
    console.log('=== HIGHLIGHTING TEST ===');
    const testTargets = ['BI RATE', 'FED RATE', '5.75', '4.5'];
    console.log('Test targets:', testTargets);
    console.log('Current markdown exists:', !!markdownV1);
    console.log('v1Rendered element exists:', !!v1Rendered);
    
    if (!markdownV1) {
        console.error('No markdownV1 content found!');
        return;
    }
    
    console.log('Original markdown sample (first 200 chars):', markdownV1.slice(0, 200));
    
    // Test markdown highlighting
    const highlighted = highlightMarkdownWithTargets(markdownV1, testTargets);
    console.log('Highlighted markdown contains spans:', highlighted.includes('src-highlight'));
    console.log('Highlighted sample (first 200 chars):', highlighted.slice(0, 200));
    
    // Apply directly to test
    highlightedMarkdownV1 = highlighted;
    renderV1Preview();
    
    setTimeout(() => {
        const highlights = v1Rendered.querySelectorAll('mark.src-highlight, span.src-highlight');
        console.log('DOM highlights found:', highlights.length);
        
        if (highlights.length === 0) {
            console.log('No highlights in DOM, trying direct HTML method...');
            const currentHTML = v1Rendered.innerHTML;
            console.log('Current HTML sample (first 200 chars):', currentHTML.slice(0, 200));
            
            // Test direct HTML highlighting
            highlightInRenderedHTML(testTargets);
            
            setTimeout(() => {
                const newHighlights = v1Rendered.querySelectorAll('mark.src-highlight, span.src-highlight');
                console.log('Direct HTML highlights found:', newHighlights.length);
                if (newHighlights.length > 0) {
                    console.log('SUCCESS: Direct HTML highlighting worked!');
                } else {
                    console.error('FAILED: No highlighting method worked!');
                }
            }, 50);
        } else {
            console.log('SUCCESS: Markdown highlighting worked!');
        }
    }, 100);
}

// Make test function globally available for console debugging
window.testHighlighting = testHighlighting;

finalizeBtn?.addEventListener('click', async () => {
    const curated = suggestionsState.filter(s => ['approved','edited'].includes((s.status||'').toLowerCase()));
    if (curated.length === 0) {
        showMessage('Tidak ada saran yang disetujui/diedit untuk difinalisasi');
        return;
    }
    finalizeBtn.disabled = true;
    setStatus('Memfinalisasi...', 'running');
    setStepperPhase('finalize');
    try {
        const resp = await fetch(`${API_BASE_URL}/finalize-document/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document_id: currentDocumentId, suggestions: curated })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || 'Gagal finalisasi dokumen');
        showMessage('Dokumen berhasil difinalisasi dan divektorisasi', 'success');
        setStatus('Final', 'finalized');
        setStepperPhase('qa');
        const chatPanel = document.getElementById('chat-panel');
        if (chatPanel && typeof chatPanel.scrollIntoView === 'function') {
            chatPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
        try { queryInput?.focus(); } catch (_) {}
    } catch (e) {
        showMessage(e.message || 'Gagal finalisasi dokumen');
        setStatus('Galat', 'error');
    } finally {
        finalizeBtn.disabled = false;
    }
});

async function askQuestion() {
    const prompt = queryInput.value.trim();
    if (!prompt || !currentDocumentId) {
        showMessage('Silakan masukkan pertanyaan dan unggah dokumen terlebih dahulu.');
        return;
    }

    queryBtn.disabled = true;
    queryBtn.textContent = 'Sedang memproses...';
    unenrichedResultDiv.innerHTML = '<span class="loading-text">Sedang memproses...</span>';
    enrichedResultDiv.innerHTML = '<span class="loading-text">Sedang memproses...</span>';
    resultsContainer.style.display = 'block';
    document.getElementById('query-display').textContent = `Pertanyaan: "${prompt}"`;

    const version = versionSelect.value || 'both';
    const traceEnabled = !!(toggleEvidence && toggleEvidence.checked);

    v1Card.style.display = (version === 'v2') ? 'none' : 'block';
    v2Card.style.display = (version === 'v1') ? 'none' : 'block';

    // Reset evidence UI
    if (evidenceContainer) evidenceContainer.style.display = 'none';
    if (v1EvidenceList) v1EvidenceList.innerHTML = '';
    if (v2EvidenceList) v2EvidenceList.innerHTML = '';
    if (v1EvidenceCard) v1EvidenceCard.style.display = (version === 'v2') ? 'none' : 'block';
    if (v2EvidenceCard) v2EvidenceCard.style.display = (version === 'v1') ? 'none' : 'block';
    // Reset token usage UI
    const tokenBox = document.getElementById('token-usage-container');
    if (tokenBox) { tokenBox.style.display = 'none'; tokenBox.innerHTML = ''; }

    try {
        const response = await fetch(`${API_BASE_URL}/ask/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document_id: currentDocumentId, prompt, version, trace: traceEnabled, k: 5 })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Gagal mengirim pertanyaan');

        if (version === 'v1' || version === 'v2') {
            const ans = data.answer || '';
            const html = (window.marked ? marked.parse(ans) : escapeHtml(ans));
            const safe = sanitizeHTML(html);
            if (version === 'v1') {
                unenrichedResultDiv.innerHTML = safe;
            } else {
                enrichedResultDiv.innerHTML = safe;
            }

            // Display token usage untuk query ini
            if (data.token_usage) {
                displayTokenUsage(data.token_usage, version);
            }

            if (traceEnabled) {
                const sources = Array.isArray(data.sources) ? data.sources : [];
                if (version === 'v1') {
                    renderEvidence(sources, v1EvidenceList);
                    if (v2EvidenceList) v2EvidenceList.innerHTML = '';
                } else {
                    renderEvidence(sources, v2EvidenceList);
                    if (v1EvidenceList) v1EvidenceList.innerHTML = '';
                }
                evidenceContainer.style.display = sources.length ? 'block' : 'none';
            }
        } else {
            const v1Ans = data.unenriched_answer || '';
            const v2Ans = data.enriched_answer || '';
            unenrichedResultDiv.innerHTML = sanitizeHTML(window.marked ? marked.parse(v1Ans) : escapeHtml(v1Ans));
            enrichedResultDiv.innerHTML = sanitizeHTML(window.marked ? marked.parse(v2Ans) : escapeHtml(v2Ans));

            // Display token usage untuk kedua versi
            if (data.unenriched_token_usage && data.enriched_token_usage) {
                displayTokenUsageComparison(data.unenriched_token_usage, data.enriched_token_usage);
            }

            if (traceEnabled) {
                const uSrc = Array.isArray(data.unenriched_sources) ? data.unenriched_sources : [];
                const eSrc = Array.isArray(data.enriched_sources) ? data.enriched_sources : [];
                renderEvidence(uSrc, v1EvidenceList);
                renderEvidence(eSrc, v2EvidenceList);
                evidenceContainer.style.display = (uSrc.length + eSrc.length) ? 'block' : 'none';
            }
        }
    } catch (error) {
        showMessage(`Terjadi kesalahan: ${error.message}`);
        unenrichedResultDiv.textContent = `Terjadi kesalahan: ${error.message}`;
        enrichedResultDiv.textContent = `Terjadi kesalahan: ${error.message}`;
    } finally {
        queryBtn.disabled = false;
        queryBtn.textContent = 'Kirim Pertanyaan';
    }
}

queryBtn?.addEventListener('click', askQuestion);
queryInput?.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        askQuestion();
    }
});

approveAllBtn?.addEventListener('click', () => {
    if (!suggestionsState.length) return;
    suggestionsState = suggestionsState.map(s => ({ ...s, status: 'approved' }));
    renderSuggestions();
    updateBulkButtonsState();
    showMessage('Semua saran telah disetujui', 'success');
});

rejectAllBtn?.addEventListener('click', () => {
    if (!suggestionsState.length) return;
    suggestionsState = suggestionsState.map(s => ({ ...s, status: 'rejected' }));
    renderSuggestions();
    updateBulkButtonsState();
    showMessage('Semua saran telah ditolak', 'success');
});

function escapeHtml(str) {
    return (str || '').replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c] || c));
}
function sanitizeHTML(html) {
    const root = document.createElement('div');
    root.innerHTML = html || '';
    root.querySelectorAll('script,style,iframe,object,embed,link,meta').forEach(el => el.remove());
    root.querySelectorAll('*').forEach(el => {
        Array.from(el.attributes).forEach(attr => {
            const name = attr.name.toLowerCase();
            const value = String(attr.value || '');
            if (name.startsWith('on')) el.removeAttribute(attr.name);
            if ((name === 'href' || name === 'src' || name === 'xlink:href') && value.trim().toLowerCase().startsWith('javascript:')) {
                el.removeAttribute(attr.name);
            }
        });
    });
    return root.innerHTML;
}

function labelForType(t) {
    switch (t) {
        case 'glossary': return 'Glossarium';
        case 'highlight': return 'Sorotan';
        case 'faq': return 'FAQ';
        case 'caption': return 'Keterangan';
        case 'term_to_define': return 'Definisi';
        case 'concept_to_simplify': return 'Penyederhanaan';
        default: return 'Pengayaan';
    }
}

function getReadableSourceInfo(suggestion) {
    // 1) Gunakan source_previews jika tersedia (lebih manusiawi)
    const previews = Array.isArray(suggestion.source_previews) ? suggestion.source_previews : null;
    if (previews && previews.length) {
        const list = previews.map(p => {
            const label = p.label || (p.type === 'table' ? 'Tabel' : 'Paragraf');
            const page = p.page ? `Hal ${p.page}` : '';
            return `${label}${page ? ` (${page})` : ''}`;
        });
        return list.join(', ');
    }

    // 2) Fallback: parse source_unit_ids bila previews belum ada
    const sourceUnits = suggestion.source_units || suggestion.source_unit_ids || [];
    if (!sourceUnits.length) return 'Tidak diketahui';

    const sources = sourceUnits.map(unitId => {
        const parts = unitId.split('_');
        if (parts.length >= 5) {
            const type = parts[1];
            const pageNum = parts[3];
            const sequence = parts[parts.length - 1];
            if (type === 't') return `Tabel ${sequence} (${pageNum.replace('p', 'Hal ')})`;
            return `Paragraf ${sequence} (${pageNum.replace('p', 'Hal ')})`;
        }
        return unitId.slice(-8);
    });
    return sources.join(', ');
}
