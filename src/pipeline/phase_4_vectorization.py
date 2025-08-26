from pathlib import Path
import re
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
import chromadb
from loguru import logger
import time
import random



def vectorize_and_store(doc_output_dir: str, client: chromadb.Client, markdown_file: str, version: str, embedding_function, collection_name: str) -> bool:
    """
    Melakukan vektorisasi pada file markdown dan menyimpannya ke dalam ChromaDB.

    Fungsi ini adalah Fase 4 dari proyek Genesis-RAG.
    Fungsi ini membaca file markdown (v1 atau v2), membaginya menjadi beberapa bagian (chunks),
    membuat embedding untuk setiap chunk menggunakan fungsi embedding yang diberikan, dan menyimpannya
    ke dalam koleksi ChromaDB dengan metadata yang sesuai.

    Args:
        doc_output_dir (str): Direktori output yang berisi file markdown.
        client (chromadb.Client): Klien ChromaDB yang sudah diinisialisasi.
        markdown_file (str): Nama file markdown yang akan diproses (misalnya, 'markdown_v2.md').
        version (str): Versi dokumen ('v1' atau 'v2') untuk metadata.
        embedding_function: Fungsi embedding yang kompatibel dengan LangChain (Google atau HuggingFace).
        collection_name (str): Nama koleksi Chroma untuk menyimpan embeddings (dinamis per backend/dimensi).

    Returns:
        bool: True jika berhasil, False jika terjadi kesalahan.
    """
    doc_path = Path(doc_output_dir)
    doc_id = doc_path.name
    markdown_path = Path(doc_output_dir) / markdown_file

    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            final_content = f.read()
    except FileNotFoundError:
        logger.error(f"File markdown final tidak ditemukan di {markdown_path}")
        return False

    # For v2, exclude the appended Glossary section from vectorization to prevent biasing retrieval
    content_for_vector = final_content
    if str(version).lower() == "v2":
        try:
            m = re.search(r"(?mi)^##\s*Glossary\s*$", content_for_vector)
            if m:
                content_for_vector = content_for_vector[:m.start()].rstrip()
        except Exception as _e:
            logger.warning(f"Gagal memotong bagian Glossary untuk v2: {_e}")

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", " ", ""]
    )
    chunks = text_splitter.split_text(content_for_vector)

    if not chunks:
        logger.warning("Tidak ada potongan teks yang dihasilkan dari dokumen. Tidak ada yang perlu divektorisasi.")
        return True  

    vector_store = Chroma(
        client=client,
        collection_name=collection_name,
        embedding_function=embedding_function,
    )

    logger.info(f"Menambahkan {len(chunks)} potongan teks ke vector store Chroma (versi: {version})...")
    metadatas = [{"source_document": doc_id, "version": version} for _ in chunks]
    ids = [f"{doc_id}_{version}_{i}" for i in range(len(chunks))]

    # Strong cleanup: delete all previous embeddings for this doc_id+version by metadata filter
    try:
        vector_store.delete(where={"source_document": doc_id, "version": version})
        logger.info("Membersihkan embeddings lama berdasarkan metadata (source_document+version).")
    except Exception as e:
        logger.warning(f"Gagal membersihkan berdasarkan metadata: {e}. Coba hapus berdasarkan IDs batch baru.")
        try:
            vector_store.delete(ids=ids)
            logger.info("Menghapus embeddings lama (berdasarkan IDs) untuk mencegah duplikasi.")
        except Exception as e2:
            logger.warning(f"Gagal menghapus embeddings lama berdasarkan IDs (mungkin tidak ada): {e2}")

    # Batch insertion with quota-aware retry to handle 429/rate limits from OpenAI embeddings
    batch_size = 3
    max_retries = 5
    base_delay = 5  # seconds

    total_inserted = 0
    for start in range(0, len(chunks), batch_size):
        end = min(start + batch_size, len(chunks))
        batch_texts = chunks[start:end]
        batch_metas = metadatas[start:end]
        batch_ids = ids[start:end]

        attempt = 0
        while True:
            try:
                vector_store.add_texts(
                    texts=batch_texts,
                    metadatas=batch_metas,
                    ids=batch_ids,
                )
                total_inserted += len(batch_texts)
                logger.info(f"Batch {(start // batch_size) + 1}: berhasil menambahkan {len(batch_texts)} potongan (total={total_inserted}/{len(chunks)}).")
                break
            except Exception as e:
                msg = str(e)
                attempt += 1
                # Retry only for quota/rate-limit type errors
                if ('429' in msg) or ('ResourceExhausted' in msg) or ('quota' in msg.lower()):
                    if attempt <= max_retries:
                        delay = min(60, base_delay * (2 ** (attempt - 1))) + random.uniform(0, 1.0)
                        logger.warning(
                            f"Rate limit/quota saat embedding (batch {start}-{end}). Menunggu {delay:.1f}s lalu coba lagi (percobaan {attempt}/{max_retries}). Detail: {msg}"
                        )
                        time.sleep(delay)
                        continue
                logger.error(f"Gagal menambahkan batch embeddings (batch {start}-{end}): {msg}")
                return False

    logger.info(f"Fase 4 selesai. Dokumen {doc_id} (versi: {version}) telah divektorisasi dan disimpan.")
    return True
