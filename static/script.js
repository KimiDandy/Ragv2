let currentDocumentId = null;
const API_BASE_URL = window.location.origin;

const uploadContainer = document.getElementById('upload-container');
const loadingContainer = document.getElementById('loading-container');
const queryContainer = document.getElementById('query-container');
const queryInput = document.getElementById('query-input');
const queryBtn = document.querySelector('.query-btn');
const fileInput = document.getElementById('file-input');
const fileNameDiv = document.getElementById('file-name');
const resultsContainer = document.getElementById('results-container');
const unenrichedResultDiv = document.getElementById('unenriched-result');
const enrichedResultDiv = document.getElementById('enriched-result');

// Create element for error/success notifications
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

fileInput.addEventListener('change', (e) => {
    const file = e.target.files[0];
    if (file) {
        fileNameDiv.textContent = `File selected: ${file.name}`;
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
            throw new Error(data.detail || 'Upload failed');
        }
        currentDocumentId = data.document_id;
        loadingContainer.style.display = 'none';
        queryContainer.style.display = 'block';
        showMessage('Document processed successfully!', 'success');
    } catch (error) {
        showMessage(`Error: ${error.message}`);
        loadingContainer.style.display = 'none';
        uploadContainer.style.display = 'block';
    }
}

async function askQuestion() {
    const prompt = queryInput.value.trim();
    if (!prompt || !currentDocumentId) {
        showMessage('Please enter a question and upload a document first.');
        return;
    }

    queryBtn.disabled = true;
    queryBtn.textContent = "Processing...";
    unenrichedResultDiv.innerHTML = '<span class="loading-text">Processing...</span>';
    enrichedResultDiv.innerHTML = '<span class="loading-text">Processing...</span>';
    resultsContainer.style.display = 'block';
    document.getElementById('query-display').textContent = `Question: "${prompt}"`;

    try {
        const response = await fetch(`${API_BASE_URL}/ask/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ document_id: currentDocumentId, prompt: prompt })
        });
        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.detail || 'Query failed');
        }
        // Use "marked" library to render answers if the response is markdown
        unenrichedResultDiv.innerHTML = data.unenriched_answer; // Replace with marked.parse(data.unenriched_answer) if you add marked.js
        enrichedResultDiv.innerHTML = data.enriched_answer;
    } catch (error) {
        showMessage(`Error: ${error.message}`);
        unenrichedResultDiv.textContent = `Error: ${error.message}`;
        enrichedResultDiv.textContent = `Error: ${error.message}`;
    } finally {
        queryBtn.disabled = false;
        queryBtn.textContent = "Ask Question";
    }
}

queryBtn.addEventListener('click', askQuestion);
queryInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        askQuestion();
    }
});
