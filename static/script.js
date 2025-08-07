let currentDocumentId = null;
const API_BASE_URL = window.location.origin;

function displayMessage(message, type) {
    const messageDiv = document.getElementById('message');
    if (!messageDiv) {
        console.error('Message div not found');
        return;
    }
    messageDiv.innerText = message;
    messageDiv.className = `message ${type}`;
    messageDiv.style.display = 'block';
    setTimeout(() => {
        messageDiv.style.display = 'none';
    }, 5000);
}

document.getElementById('file-input').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (file) {
        document.getElementById('file-name').textContent = `File dipilih: ${file.name}`;
        uploadFile(file);
    }
});

async function uploadFile(file) {
    if (!file) {
        displayMessage('Please select a file first.', 'error');
        return;
    }

    document.getElementById('upload-container').style.display = 'none';
    document.getElementById('loading-container').style.display = 'block';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE_URL}/upload-document/`, {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            const data = await response.json();
            currentDocumentId = data.document_id;
            document.getElementById('loading-container').style.display = 'none';
            document.getElementById('query-container').style.display = 'block';
        } else {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Upload failed');
        }
    } catch (error) {
        displayMessage('Error: ' + error.message, 'error');
        document.getElementById('loading-container').style.display = 'none';
        document.getElementById('upload-container').style.display = 'block';
    }
}

async function askQuestion() {
    const prompt = document.getElementById('query-input').value.trim();
    if (!prompt) {
        displayMessage('Silakan masukkan pertanyaan', 'error');
        return;
    }

    if (!currentDocumentId) {
        displayMessage('Silakan unggah dokumen terlebih dahulu', 'error');
        return;
    }

    document.getElementById('unenriched-result').innerHTML = '<span class="loading-text">Memproses...</span>';
    document.getElementById('enriched-result').innerHTML = '<span class="loading-text">Memproses...</span>';
    document.getElementById('results-container').style.display = 'block';
    document.getElementById('query-display').textContent = `Pertanyaan: "${prompt}"`;

    try {
        const response = await fetch(`${API_BASE_URL}/ask/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                document_id: currentDocumentId,
                prompt: prompt
            })
        });

        if (response.ok) {
            const data = await response.json();
            document.getElementById('unenriched-result').textContent = data.unenriched_answer;
            document.getElementById('enriched-result').textContent = data.enriched_answer;
        } else {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Query failed');
        }
    } catch (error) {
        displayMessage('Error: ' + error.message, 'error');
        document.getElementById('unenriched-result').textContent = 'Error: ' + error.message;
        document.getElementById('enriched-result').textContent = 'Error: ' + error.message;
    }
}

document.getElementById('query-input').addEventListener('keypress', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        askQuestion();
    }
});
