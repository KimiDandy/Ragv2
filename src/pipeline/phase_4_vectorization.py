from pathlib import Path
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
import chromadb
from loguru import logger

from ..core.config import (
    GOOGLE_API_KEY,
    EMBEDDING_MODEL,
    CHROMA_COLLECTION,
    PIPELINE_ARTEFACTS_DIR,
    CHROMA_DB_PATH
)



def vectorize_and_store(doc_output_dir: str, client: chromadb.Client, markdown_file: str, version: str) -> bool:
    """
    Melakukan vektorisasi pada file markdown dan menyimpannya ke dalam ChromaDB.

    Fungsi ini adalah Fase 4 dari proyek Genesis-RAG.
    Fungsi ini membaca file markdown (v1 atau v2), membaginya menjadi beberapa bagian (chunks),
    membuat embedding untuk setiap chunk menggunakan model Google AI, dan menyimpannya
    ke dalam koleksi ChromaDB dengan metadata yang sesuai.

    Args:
        doc_output_dir (str): Direktori output yang berisi file markdown.
        client (chromadb.Client): Klien ChromaDB yang sudah diinisialisasi.
        markdown_file (str): Nama file markdown yang akan diproses (misalnya, 'markdown_v2.md').
        version (str): Versi dokumen ('v1' atau 'v2') untuk metadata.

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

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", " ", ""]
    )
    chunks = text_splitter.split_text(final_content)

    if not chunks:
        logger.warning("Tidak ada potongan teks yang dihasilkan dari dokumen. Tidak ada yang perlu divektorisasi.")
        return True  

    logger.info("Menginisialisasi fungsi embedding Google Generative AI...")
    if not GOOGLE_API_KEY:
        logger.error("GOOGLE_API_KEY tidak ditemukan di variabel lingkungan atau konfigurasi.")
        return False

    embeddings = GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL, google_api_key=GOOGLE_API_KEY
    )

    vector_store = Chroma(
        client=client,
        collection_name=CHROMA_COLLECTION,
        embedding_function=embeddings,
    )

    logger.info(f"Menambahkan {len(chunks)} potongan teks ke vector store Chroma (versi: {version})...")
    vector_store.add_texts(
        texts=chunks,
        metadatas=[{"source_document": doc_id, "version": version} for _ in chunks],
        ids=[f"{doc_id}_{version}_{i}" for i in range(len(chunks))]
    )

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
            

            success = vectorize_and_store(
                doc_output_dir=str(latest_doc_dir), 
                client=standalone_client, 
                markdown_file="final_markdown.md", 
                version=doc_id
            )

            if success:
                logger.info("Standalone vectorization test completed successfully.")
            else:
                logger.error("Standalone vectorization test failed.")
