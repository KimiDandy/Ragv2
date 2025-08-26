from pathlib import Path
import json
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_chroma import Chroma
import chromadb
from loguru import logger

from ..core.config import (
    EMBEDDING_MODEL,
    CHROMA_COLLECTION,
    PIPELINE_ARTEFACTS_DIR,
    CHROMA_DB_PATH
)



def vectorize_and_store(
    doc_output_dir: str,
    client: chromadb.Client,
    markdown_file: str,
    version: str,
    embeddings: OpenAIEmbeddings | None = None,
) -> bool:
    """
    Melakukan vektorisasi pada file markdown dan menyimpannya ke dalam ChromaDB.

    Fungsi ini adalah Fase 4 dari proyek Genesis-RAG.
    Fungsi ini membaca file markdown (v1 atau v2), membaginya menjadi beberapa bagian (chunks),
    membuat embedding untuk setiap chunk menggunakan OpenAI Embeddings, dan menyimpannya
    ke dalam koleksi ChromaDB dengan metadata yang sesuai.

    Args:
        doc_output_dir (str): Direktori output yang berisi file markdown.
        client (chromadb.Client): Klien ChromaDB yang sudah diinisialisasi.
        markdown_file (str): Nama file markdown yang akan diproses (misalnya, 'markdown_v2.md').
        version (str): Versi dokumen ('v1' atau 'v2') untuk metadata.
        embeddings (OpenAIEmbeddings | None): Fungsi embedding yang sudah diinisialisasi.

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

    chunk_size = 1000
    chunk_overlap = 150
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""],
    )
    # Older langchain versions do not support add_start_index on create_documents.
    # Use split_text then compute spans manually with a forward cursor.
    chunks = text_splitter.split_text(final_content)
    if not chunks:
        logger.warning("Tidak ada potongan teks yang dihasilkan dari dokumen. Tidak ada yang perlu divektorisasi.")
        return True

    if embeddings is None:
        logger.info("Menginisialisasi fungsi embedding OpenAI...")
        embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)

    # Gunakan nama koleksi yang spesifik ke model embedding untuk menghindari mismatch dimensi
    collection_name = f"{CHROMA_COLLECTION}__{EMBEDDING_MODEL.replace(':','_').replace('-','_')}"

    vector_store = Chroma(
        client=client,
        collection_name=collection_name,
        embedding_function=embeddings,
    )

    # Hapus data lama untuk dokumen+versi sebelum upsert (hygiene)
    try:
        vector_store.delete(where={
            "$and": [
                {"source_document": {"$eq": doc_id}},
                {"version": {"$eq": version}},
            ]
        })
        logger.info(f"Membersihkan entri lama untuk doc={doc_id}, version={version} di koleksi {collection_name}")
    except Exception as e:
        logger.warning(f"Gagal menghapus entri lama untuk doc={doc_id}, version={version}: {e}")

    # Prepare rich metadata per chunk. For v1, enrich with header_path/page by intersecting Phase-0 segments.
    segments = []
    seg_path = Path(doc_output_dir) / "segments.json"
    if seg_path.exists():
        try:
            segments = json.loads(seg_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Gagal membaca segments.json untuk metadata: {e}")

    def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
        return a_start < b_end and b_start < a_end

    def _compute_spans(text: str, parts: list[str], approx_overlap: int = 150) -> list[tuple[int, int]]:
        """Best-effort mapping of chunk texts back to original char spans.
        Uses a forward-search cursor and small backoff to account for overlaps.
        """
        spans: list[tuple[int, int]] = []
        cursor = 0
        for ch in parts:
            # Search starting slightly before the previous end to handle overlaps
            search_start = max(0, cursor - approx_overlap - 50)
            idx = text.find(ch, search_start)
            if idx == -1:
                # Fallback: global search
                idx = text.find(ch)
                if idx == -1:
                    # Last resort: assume contiguous from cursor
                    idx = cursor
            start = idx
            end = start + len(ch)
            spans.append((start, end))
            cursor = end
        return spans

    def _sanitize_metadata(md: dict) -> dict:
        """Ensure Chroma-compatible metadata: only scalars or None.
        Lists/dicts will be JSON-serialized strings.
        """
        cleaned = {}
        for k, v in md.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                cleaned[k] = v
            else:
                try:
                    cleaned[k] = json.dumps(v, ensure_ascii=False)
                except Exception:
                    cleaned[k] = str(v)
        return cleaned

    texts: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []

    spans = _compute_spans(final_content, chunks, approx_overlap=chunk_overlap)
    for i, (chunk, (start, end)) in enumerate(zip(chunks, spans)):
        chunk = chunk or ""

        md = {"source_document": doc_id, "version": version, "char_start": start, "char_end": end}

        if version == "v1" and segments:
            overlaps = [
                s for s in segments
                if _overlap(start, end, int(s.get("char_start", 0)), int(s.get("char_end", 0)))
            ]
            if overlaps:
                # collect pages, header_paths, segment_ids
                pages = sorted({int(s.get("page", 0)) for s in overlaps if s.get("page") is not None})
                header_paths = [s.get("header_path") for s in overlaps if s.get("header_path")]
                seg_ids = [s.get("segment_id") for s in overlaps if s.get("segment_id")]
                md.update({
                    "pages": pages,
                    "header_paths": header_paths,
                    "segment_ids": seg_ids,
                })

        texts.append(chunk)
        metadatas.append(_sanitize_metadata(md))
        ids.append(f"{doc_id}_{version}_{i}")

    logger.info(f"Menambahkan {len(texts)} potongan teks ke vector store Chroma (versi: {version})...")
    vector_store.add_texts(texts=texts, metadatas=metadatas, ids=ids)

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

            ok_v1 = False
            ok_v2 = False
            v1_path = latest_doc_dir / "markdown_v1.md"
            if v1_path.exists():
                ok_v1 = vectorize_and_store(
                    doc_output_dir=str(latest_doc_dir),
                    client=standalone_client,
                    markdown_file="markdown_v1.md",
                    version="v1",
                )
            else:
                logger.warning("markdown_v1.md tidak ditemukan untuk dokumen terbaru.")

            v2_path = latest_doc_dir / "markdown_v2.md"
            if v2_path.exists():
                ok_v2 = vectorize_and_store(
                    doc_output_dir=str(latest_doc_dir),
                    client=standalone_client,
                    markdown_file="markdown_v2.md",
                    version="v2",
                )
            else:
                logger.warning("markdown_v2.md tidak ditemukan untuk dokumen terbaru (mungkin belum disintesis).")

            if ok_v1 or ok_v2:
                logger.info("Standalone vectorization completed (setidaknya satu versi berhasil).")
            else:
                logger.error("Standalone vectorization failed for both versions.")
