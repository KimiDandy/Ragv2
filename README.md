# Genesis-RAG: Mesin Peningkat dan Pembanding Dokumen

Genesis-RAG adalah sebuah aplikasi web canggih yang dirancang untuk meningkatkan (enrich) dokumen PDF dan membandingkan pemahaman antara versi asli dengan versi yang telah ditingkatkan secara cerdas. Dibangun dengan FastAPI, Google Gemini, dan ChromaDB, aplikasi ini menyediakan solusi lengkap untuk analisis dokumen mendalam melalui arsitektur Retrieval-Augmented Generation (RAG).

## Fitur Utama

- **Unggah Dokumen PDF**: Unggah dokumen PDF apa pun untuk diproses oleh pipeline.
- **Peningkatan Dokumen Otomatis**: Dokumen secara otomatis dianalisis untuk mendefinisikan istilah kunci, menyederhanakan konsep kompleks, dan mendeskripsikan gambar.
- **Kueri Bahasa Alami**: Ajukan pertanyaan dalam bahasa alami terhadap dokumen.
- **Perbandingan Berdampingan**: Dapatkan jawaban dari dokumen asli dan versi yang telah ditingkatkan secara bersamaan untuk melihat perbedaan pemahaman secara langsung.
- **Antarmuka Web Intuitif**: UI yang bersih dan responsif untuk interaksi yang mudah.

## Arsitektur

Genesis-RAG menggunakan arsitektur modular yang terdiri dari beberapa komponen utama:

1.  **Frontend**: Antarmuka web statis (`index.html`, `CSS`, `JavaScript`) yang memungkinkan pengguna berinteraksi dengan sistem.
2.  **Backend (FastAPI)**: Server yang menangani permintaan API, logika bisnis, dan menjalankan pipeline pemrosesan dokumen.
3.  **Pipeline Peningkatan Dokumen**: Serangkaian skrip Python yang melakukan:
    -   **Fase 0 (Ekstraksi)**: Mengekstrak teks dan gambar dari PDF menjadi format Markdown.
    -   **Fase 1 (Perencanaan)**: Menganalisis Markdown untuk membuat rencana peningkatan (misalnya, istilah apa yang perlu didefinisikan).
    -   **Fase 2 (Generasi)**: Menggunakan Google Gemini untuk menghasilkan konten yang dibutuhkan sesuai rencana.
    -   **Fase 3 (Sintesis)**: Menggabungkan konten asli dengan konten yang baru dibuat menjadi versi dokumen final yang telah ditingkatkan (`markdown_v2.md`).
    -   **Fase 4 (Vektorisasi)**: Mengubah kedua versi dokumen (asli dan ditingkatkan) menjadi vektor dan menyimpannya di ChromaDB.
4.  **Vector Store (ChromaDB)**: Database vektor yang berjalan dalam mode *embedded* untuk menyimpan dan mengambil data dokumen secara efisien.
5.  **Model AI (Google Gemini)**: Digunakan untuk pembuatan embedding (vektorisasi) dan menjawab pertanyaan pengguna (chat).

## Instalasi

Ikuti langkah-langkah berikut untuk menjalankan proyek ini secara lokal:

1.  **Clone Repositori**

    ```bash
    git clone <URL_REPOSITORI_ANDA>
    cd RAGv2
    ```

2.  **Buat dan Aktifkan Virtual Environment**

    ```bash
    # Windows
    python -m venv venv
    .\venv\Scripts\activate
    ```

3.  **Instal Dependensi**

    ```bash
    pip install -r requirements.txt
    ```

4.  **Konfigurasi Environment Variable**

    -   Salin file `.env_example` menjadi `.env`.
    -   Buka file `.env` dan masukkan `GOOGLE_API_KEY` Anda.

    ```
    GOOGLE_API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    ```

## Cara Penggunaan

1.  **Jalankan Server FastAPI**

    Dari direktori root proyek, jalankan perintah berikut:

    ```bash
    uvicorn src.main:app --reload
    ```

2.  **Buka Aplikasi di Browser**

    Buka browser Anda dan akses alamat `http://127.0.0.1:8000`.

3.  **Gunakan Aplikasi**
    -   Klik tombol "Pilih File PDF" untuk mengunggah dokumen.
    -   Tunggu hingga proses peningkatan selesai (Anda akan melihat notifikasi sukses).
    -   Masukkan pertanyaan Anda di kotak teks dan klik "Kirim Pertanyaan".
    -   Lihat perbandingan jawaban dari dokumen asli dan dokumen yang telah ditingkatkan.

## Struktur Proyek

```
/RAGv2
|-- .env                  # File environment variable (JANGAN di-commit)
|-- .env_example          # Contoh file environment
|-- chroma_db/            # Direktori database ChromaDB (embedded)
|-- index.html            # File utama frontend
|-- pipeline_artefacts/   # Output dari setiap fase pipeline (dokumen yang diproses)
|-- requirements.txt      # Daftar dependensi Python
|-- README.md             # Dokumentasi proyek
|-- src/
|   |-- api/              # Modul API endpoints
|   |-- core/             # Konfigurasi dan logika inti
|   |-- pipeline/         # Skrip untuk setiap fase pemrosesan
|   |-- main.py           # Titik masuk aplikasi FastAPI
|-- static/
|   |-- script.js         # Logika JavaScript frontend
|   |-- style.css         # Styling untuk frontend
|-- venv/                 # Direktori virtual environment
```