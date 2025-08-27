let currentDocumentId = null;
let markdownV1 = '';
let suggestionsState = []; 
let pollTimer = null;
let pollAttempts = 0;
let pollIntervalMs = 3000; 
const maxPollAttempts = 60; 
let progressTimer = null;
let progressIntervalMs = 2000;

const API_BASE_URL = window.location.origin;


const uploadContainer = document.getElementById('upload-container');
const loadingContainer = document.getElementById('loading-container');
const mainLayout = document.getElementById('main-layout');
const stepperEl = document.getElementById('stepper');

// Panel 1: Preview
const v1Rendered = document.getElementById('v1-rendered');
const v1Raw = document.getElementById('v1-raw');
const toggleRaw = document.getElementById('toggle-raw');

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
        targetEl.innerHTML = '<div class="muted">Tidak ada evidence.</div>';
        return;
    }
    sources.forEach((s, idx) => {
        const item = document.createElement('div');
        item.className = 'evidence-item';
        const scorePct = Math.round(((s && typeof s.score === 'number') ? s.score : 0) * 100);
        const meta = s && s.metadata ? s.metadata : {};
        const metaPairs = Object.entries(meta).slice(0, 6).map(([k,v]) => `${escapeHtml(String(k))}: ${escapeHtml(String(v))}`);
        item.innerHTML = `
            <div class="evidence-head">
                <span class="evidence-rank">#${idx+1}</span>
                <span class="evidence-score">Skor: ${scorePct}%</span>
            </div>
            <div class="evidence-snippet">${escapeHtml(s.snippet || '')}</div>
            <div class="evidence-meta">${escapeHtml(metaPairs.join(' | '))}</div>
        `;
        targetEl.appendChild(item);
    });
}

fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        fileNameDiv.textContent = `File terpilih: ${file.name}`;
        uploadFile(file);
    }
});

async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    uploadContainer.style.display = 'none';
    loadingContainer.style.display = 'block';

    try {
        const response = await fetch(`${API_BASE_URL}/upload-document/`, {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Gagal mengunggah file');
        }
        currentDocumentId = data.document_id;
        markdownV1 = data.markdown_content || '';
        renderV1Preview();
        loadingContainer.style.display = 'none';
        mainLayout.style.display = 'flex';
        setStepperPhase('enhance');
        setStatus('Siap', 'ready');
        showMessage('Dokumen berhasil diproses!', 'success');
    } catch (error) {
        showMessage(`Terjadi kesalahan: ${error.message}`);
        loadingContainer.style.display = 'none';
        uploadContainer.style.display = 'block';
    }
}

function renderV1Preview() {
    try {
        if (window.marked) {
            v1Rendered.innerHTML = marked.parse(markdownV1 || '');
        } else {
            v1Rendered.textContent = markdownV1 || '';
        }
        v1Raw.textContent = markdownV1 || '';
    } catch (e) {
        v1Rendered.textContent = markdownV1 || '';
        v1Raw.textContent = markdownV1 || '';
    }
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
    setStatus('Berjalan...', 'running');
    setStepperPhase('enhance');
    try {
        const resp = await fetch(`${API_BASE_URL}/start-enhancement/${currentDocumentId}`, { method: 'POST' });
        if (!resp.ok) {
            const d = await resp.json().catch(()=>({detail:'Gagal memulai peningkatan'}));
            throw new Error(d.detail || 'Gagal memulai peningkatan');
        }
        if (pollTimer) clearTimeout(pollTimer);
        if (progressTimer) clearTimeout(progressTimer);
        pollAttempts = 0;
        pollIntervalMs = 3000;
        progressIntervalMs = 2000;
        scheduleNextPoll();
        scheduleProgressPoll();
    } catch (err) {
        showMessage(err.message || 'Gagal memulai peningkatan');
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

function renderSuggestions() {
    suggestionsEmpty.style.display = suggestionsState.length ? 'none' : 'block';
    suggestionsList.innerHTML = '';
    suggestionsState.forEach((s, idx) => {
        const card = document.createElement('div');
        card.className = 'suggestion-card';
        card.innerHTML = `
            <div class="suggestion-head">
               <span class="suggestion-type ${s.type}">${labelForType(s.type)}</span>
               <span class="suggestion-conf">Keyakinan: ${(s.confidence_score ?? 0).toFixed(2)}</span>
            </div>
            <div class="suggestion-context">${escapeHtml(s.original_context || '')}</div>
            <textarea class="suggestion-editor" data-idx="${idx}">${s.generated_content || ''}</textarea>
            <div class="suggestion-actions">
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
            if (action === 'approve') suggestionsState[i].status = 'approved';
            if (action === 'reject') suggestionsState[i].status = 'rejected';
            // visual cue
            const card = e.target.closest('.suggestion-card');
            card.dataset.state = suggestionsState[i].status;
            updateBulkButtonsState();
        });
    });

    updateBulkButtonsState();
}

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
        case 'term_to_define': return 'Definisi';
        case 'concept_to_simplify': return 'Penyederhanaan';
        default: return 'Pengayaan';
    }
}
