from pathlib import Path
import re
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings
import chromadb
from loguru import logger
import time
import random

from ..core.config import (
    CHROMA_COLLECTION,
    PIPELINE_ARTEFACTS_DIR,
    CHROMA_DB_PATH,
    EMBEDDING_LOCAL_MODEL,
)



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

    # Batch insertion with quota-aware retry to handle 429 ResourceExhausted from Gemini embeddings
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

if __name__ == '__main__':
    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"'{PIPELINE_ARTEFACTS_DIR}' directory not found.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error(f"No document artefact directories found in '{PIPELINE_ARTEFACTS_DIR}'.")
        else:
            logger.info(f"Running in standalone mode. Initializing ChromaDB client from path: {CHROMA_DB_PATH}...")
            standalone_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
            
            latest_doc_dir = max(all_doc_dirs, key=lambda p: p.stat().st_mtime)
            doc_id = latest_doc_dir.name
            logger.info(f"Processing the most recent document: {doc_id}")
            

            # In standalone mode, gunakan embedding lokal untuk menghindari ketergantungan API
            local_embedder = HuggingFaceEmbeddings(model_name=EMBEDDING_LOCAL_MODEL)
            try:
                probe = local_embedder.embed_query("probe")
                dim = len(probe) if hasattr(probe, "__len__") else None
            except Exception:
                dim = None
            suffix = f"local_{dim}d" if dim else "local"
            collection_name = f"{CHROMA_COLLECTION}_{suffix}"

            success = vectorize_and_store(
                doc_output_dir=str(latest_doc_dir), 
                client=standalone_client, 
                markdown_file="final_markdown.md", 
                version=doc_id,
                embedding_function=local_embedder,
                collection_name=collection_name,
            )

            if success:
                logger.info("Standalone vectorization test completed successfully.")
            else:
                logger.error("Standalone vectorization test failed.")
