let currentDocumentId = null;

document.getElementById('file-input').addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (file) {
        document.getElementById('file-name').textContent = `File dipilih: ${file.name}`;
        uploadFile(file);
    }
});

async function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);

    document.getElementById('upload-container').style.display = 'none';
    document.getElementById('loading-container').style.display = 'block';

    try {
        const response = await fetch('http://localhost:8000/upload-document/', {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            const data = await response.json();
            currentDocumentId = data.document_id;
            
            document.getElementById('loading-container').style.display = 'none';
            document.getElementById('query-container').style.display = 'block';
        } else {
            throw new Error('Upload gagal');
        }
    } catch (error) {
        alert('Error: ' + error.message);
        document.getElementById('loading-container').style.display = 'none';
        document.getElementById('upload-container').style.display = 'block';
    }
}

async function askQuestion() {
    const prompt = document.getElementById('query-input').value.trim();
    if (!prompt) {
        alert('Silakan masukkan pertanyaan');
        return;
    }

    if (!currentDocumentId) {
        alert('Silakan unggah dokumen terlebih dahulu');
        return;
    }

    document.getElementById('unenriched-result').innerHTML = '<span class="loading-text">Memproses...</span>';
    document.getElementById('enriched-result').innerHTML = '<span class="loading-text">Memproses...</span>';
    document.getElementById('results-container').style.display = 'block';
    document.getElementById('query-display').textContent = `Pertanyaan: "${prompt}"`;

    try {
        const response = await fetch('http://localhost:8000/ask/', {
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
            throw new Error('Query gagal');
        }
    } catch (error) {
        alert('Error: ' + error.message);
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
