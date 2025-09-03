from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
import chromadb

from ..core.config import EMBEDDING_MODEL, CHROMA_COLLECTION
from .chunker import Chunk


def _collection_name() -> str:
    return f"{CHROMA_COLLECTION}__{EMBEDDING_MODEL.replace(':','_').replace('-','_')}"


def _sanitize_metadata(md: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for k, v in (md or {}).items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k] = v
        else:
            try:
                cleaned[k] = json.dumps(v, ensure_ascii=False)
            except Exception:
                cleaned[k] = str(v)
    return cleaned


def delete_before_upsert(client: chromadb.Client, doc_id: str, version: str) -> None:
    col = _collection_name()
    try:
        vs = Chroma(client=client, collection_name=col)
        vs.delete(where={
            "$and": [
                {"source_document": {"$eq": doc_id}},
                {"version": {"$eq": version}},
            ]
        })
        # Also delete entries using 'doc_id' key for robustness
        vs.delete(where={
            "$and": [
                {"doc_id": {"$eq": doc_id}},
                {"version": {"$eq": version}},
            ]
        })
        logger.info(f"[VectorStore] Deleted old entries for doc={doc_id} version={version} in {col}")
    except Exception as e:
        logger.warning(f"[VectorStore] Failed delete for doc={doc_id} version={version}: {e}")


def upsert_chunks(
    client: chromadb.Client,
    embeddings: OpenAIEmbeddings,
    doc_id: str,
    version: str,
    chunks: List[Chunk],
    batch_size: int = 128,
) -> int:
    col = _collection_name()
    vs = Chroma(client=client, collection_name=col, embedding_function=embeddings)

    texts: List[str] = []
    metas: List[Dict[str, Any]] = []
    ids: List[str] = []

    total = 0
    for ch in chunks:
        md = ch.metadata()
        # Ensure backward-compatible keys for existing filters
        md["source_document"] = doc_id
        md["version"] = version
        texts.append(ch.text)
        metas.append(_sanitize_metadata(md))
        ids.append(ch.chunk_id)

        # flush in batches
        if len(texts) >= batch_size:
            vs.add_texts(texts=texts, metadatas=metas, ids=ids)
            total += len(texts)
            texts.clear(); metas.clear(); ids.clear()

    if texts:
        vs.add_texts(texts=texts, metadatas=metas, ids=ids)
        total += len(texts)

    logger.info(f"[VectorStore] Upserted {total} chunks for doc={doc_id} version={version} into {col}")
    return total
