from pathlib import Path
import json
import uuid
from typing import List, Dict, Any
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from pinecone import Pinecone
from loguru import logger

from ..core.config import (
    EMBEDDING_MODEL,
    PINECONE_API_KEY,
    PINECONE_INDEX_NAME,
    PIPELINE_ARTEFACTS_DIR,
)
from ..observability.token_ledger import log_tokens
from ..observability.token_counter import count_tokens
from ..shared.document_meta import get_markdown_relative_path


def vectorize_and_store(
    doc_output_dir: str,
    pinecone_index,
    markdown_file: str,
    version: str,
    embeddings: OpenAIEmbeddings | None = None,
) -> bool:
    """
    Melakukan vektorisasi pada file markdown dan menyimpannya ke dalam Pinecone.

    Fungsi ini adalah Fase 4 dari proyek Genesis-RAG.
    Fungsi ini membaca file markdown (v1 atau v2), membaginya menjadi beberapa bagian (chunks),
    membuat embedding untuk setiap chunk menggunakan OpenAI Embeddings, dan menyimpannya
    ke dalam index Pinecone dengan metadata yang sesuai.

    Args:
        doc_output_dir (str): Direktori output yang berisi file markdown.
        pinecone_index: Index Pinecone yang sudah diinisialisasi.
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
    chunks = text_splitter.split_text(final_content)
    if not chunks:
        logger.warning("Tidak ada potongan teks yang dihasilkan dari dokumen. Tidak ada yang perlu divektorisasi.")
        return True

    if embeddings is None:
        logger.info("Menginisialisasi fungsi embedding OpenAI...")
        embeddings = OpenAIEmbeddings(model=EMBEDDING_MODEL)

    # Clean up old entries for this document and version
    try:
        # Delete by metadata filter (more efficient for Pinecone v3+)
        pinecone_index.delete(
            filter={
                "source_document": doc_id,
                "version": version
            }
        )
        logger.info(f"Membersihkan entri lama untuk doc={doc_id}, version={version}")
    except Exception as e:
        logger.warning(f"Gagal menghapus entri lama untuk doc={doc_id}, version={version}: {e}")

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
            search_start = max(0, cursor - approx_overlap - 50)
            idx = text.find(ch, search_start)
            if idx == -1:
                idx = text.find(ch)
                if idx == -1:
                    idx = cursor
            start = idx
            end = start + len(ch)
            spans.append((start, end))
            cursor = end
        return spans

    def _sanitize_metadata(md: dict) -> dict:
        """Ensure Pinecone-compatible metadata: only scalars, lists, or None.
        Pinecone supports string, number, boolean, and list of strings.
        """
        cleaned = {}
        for k, v in md.items():
            if isinstance(v, (str, int, float, bool)) or v is None:
                cleaned[k] = v
            elif isinstance(v, list):
                # Convert list items to strings if needed
                if all(isinstance(item, (str, int, float, bool)) for item in v):
                    cleaned[k] = [str(item) for item in v]
                else:
                    cleaned[k] = json.dumps(v, ensure_ascii=False)
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

    logger.info(f"Menambahkan {len(texts)} potongan teks ke Pinecone index (versi: {version})...")
    
    # Track token usage untuk embeddings
    total_text = " ".join(texts)
    input_tokens = count_tokens(total_text)
    
    # Generate embeddings
    embeddings_list = embeddings.embed_documents(texts)
    
    # Prepare vectors for Pinecone
    vectors_to_upsert = []
    for i, (text, metadata, embedding) in enumerate(zip(texts, metadatas, embeddings_list)):
        vector_id = f"{doc_id}_{version}_{i}"
        vectors_to_upsert.append({
            "id": vector_id,
            "values": embedding,
            "metadata": {
                **metadata,
                "text": text  # Store the actual text content
            }
        })
    
    # Upsert to Pinecone in batches
    batch_size = 100
    for i in range(0, len(vectors_to_upsert), batch_size):
        batch = vectors_to_upsert[i:i + batch_size]
        pinecone_index.upsert(vectors=batch)
        logger.info(f"Uploaded batch {i//batch_size + 1}/{(len(vectors_to_upsert) + batch_size - 1)//batch_size}")
    
    # Log embedding tokens
    log_tokens(
        step="embed",
        model=EMBEDDING_MODEL,
        input_tokens=input_tokens,
        output_tokens=0,  # Embeddings tidak memiliki output tokens
        phase="phase_4",
        doc_id=doc_id,
        version=version,
        num_chunks=len(texts),
        total_chars=len(total_text)
    )

    logger.info(f"Fase 4 selesai. Dokumen {doc_id} (versi: {version}) telah divektorisasi dan disimpan ke Pinecone.")
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
            logger.info(f"Running in standalone mode. Initializing Pinecone client...")
            pc = Pinecone(api_key=PINECONE_API_KEY)
            pinecone_index = pc.Index(PINECONE_INDEX_NAME)

            latest_doc_dir = max(all_doc_dirs, key=lambda p: p.stat().st_mtime)
            doc_id = latest_doc_dir.name
            logger.info(f"Processing the most recent document: {doc_id}")

            ok_v1 = False
            ok_v2 = False
            v1_rel = get_markdown_relative_path(latest_doc_dir, "v1")
            v1_path = latest_doc_dir / v1_rel
            if v1_path.exists():
                ok_v1 = vectorize_and_store(
                    doc_output_dir=str(latest_doc_dir),
                    pinecone_index=pinecone_index,
                    markdown_file=v1_rel,
                    version="v1",
                )
            else:
                logger.warning(f"Markdown v1 ({v1_rel}) tidak ditemukan untuk dokumen terbaru.")

            v2_rel = get_markdown_relative_path(latest_doc_dir, "v2")
            v2_path = latest_doc_dir / v2_rel
            if v2_path.exists():
                ok_v2 = vectorize_and_store(
                    doc_output_dir=str(latest_doc_dir),
                    pinecone_index=pinecone_index,
                    markdown_file=v2_rel,
                    version="v2",
                )
            else:
                logger.warning(f"Markdown v2 ({v2_rel}) tidak ditemukan untuk dokumen terbaru (mungkin belum disintesis).")

            if ok_v1 or ok_v2:
                logger.info("Standalone vectorization completed (setidaknya satu versi berhasil).")
            else:
                logger.error("Standalone vectorization failed for both versions.")