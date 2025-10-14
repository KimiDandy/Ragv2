"""
RAG Chain Builder menggunakan LangChain LCEL create_retrieval_chain.
Memberikan evidence yang tepat dari dokumen yang benar-benar digunakan LLM.
Menggunakan Pinecone sebagai vector database.
"""

from typing import Dict, List, Any, Tuple
from langchain_pinecone import PineconeVectorStore
from langchain_core.prompts import PromptTemplate
from langchain_core.documents import Document
try:
    from langchain.chains.combine_documents.stuff import create_stuff_documents_chain
    from langchain.chains.retrieval import create_retrieval_chain
except ImportError:
    try:
        from langchain.chains import create_retrieval_chain
        from langchain_core.runnables import RunnablePassthrough
        from langchain_core.output_parsers import StrOutputParser
        create_stuff_documents_chain = None  # Will handle manually below
    except ImportError:
        # Fallback for older versions
        from langchain.chains import RetrievalQA
        create_retrieval_chain = None
        create_stuff_documents_chain = None
from langchain_openai import ChatOpenAI
from langchain_community.callbacks.manager import get_openai_callback
from loguru import logger

from ..observability.token_ledger import log_tokens
from ..observability.token_counter import count_tokens


def build_rag_chain(retriever, model: str = "gpt-4.1", temperature: float = 0.2):
    """
    Membangun RAG chain menggunakan create_retrieval_chain atau fallback ke RetrievalQA.
    
    Args:
        retriever: LangChain retriever object
        model: Model LLM yang akan digunakan
        temperature: Temperature untuk LLM
    
    Returns:
        RAG chain yang siap digunakan
    """
    # Initialize LLM dengan streaming dan usage tracking
    llm = ChatOpenAI(
        model=model,
        temperature=temperature,
        streaming=True,
        stream_usage=True  # Penting untuk mendapatkan token usage
    )
    
    # Prompt template untuk RAG - Comprehensive Indonesian prompt
    prompt_template = PromptTemplate.from_template(
        """Anda adalah asisten AI profesional untuk analisis dokumen yang bertugas membantu pengguna memahami dan mengekstrak informasi dari SEMUA dokumen yang tersedia dalam sistem.

**Prinsip Utama (WAJIB DIPATUHI):**

1. **Berbasis Multi-Dokumen & Faktual**
   - Jawab berdasarkan informasi yang tersedia dalam SEMUA KONTEKS DOKUMEN di bawah
   - Konteks bisa berasal dari BERBAGAI DOKUMEN yang berbeda - sebutkan sumber dokumennya
   - TIDAK BOLEH menambahkan informasi dari pengetahuan umum atau asumsi
   - TIDAK BOLEH melakukan spekulasi atau tebakan
   - Jika informasi tidak ada di dokumen manapun, katakan dengan jelas

2. **Terstruktur & Mudah Dipahami**
   - Untuk data numerik: Sebutkan angka dengan jelas dan konteksnya
   - Untuk tanggal: Sebutkan tanggal dengan tepat dan lengkap (dari semua dokumen yang relevan)
   - Untuk list/daftar: Gunakan bullet points atau numbering yang rapi
   - Berikan jawaban yang terstruktur dan mudah dibaca
   - Kelompokkan informasi berdasarkan dokumen sumber jika relevan

3. **Transparan & Jujur**
   - Jika dokumen tidak memiliki informasi yang ditanyakan, akui dengan jelas
   - Jangan membuat asumsi atau spekulasi
   - Sebutkan dari dokumen mana informasi berasal (jika konteks mencantumkan sumbernya)

📄 KONTEKS DOKUMEN (dari berbagai sumber):
{context}

❓ PERTANYAAN PENGGUNA:
{input}

📋 INSTRUKSI:
• Jawab berdasarkan SEMUA konteks dokumen yang disediakan di atas
• Jika pertanyaan menyangkut data yang bisa ada di multiple documents, pastikan menggabungkan semua informasi relevan
• Jika ada data numerik, tabel, atau statistik - jelaskan dengan detail dan jelas
• Jika ada tanggal, nama, atau informasi spesifik - sebutkan dengan tepat dari SEMUA dokumen yang relevan
• Berikan jawaban yang terstruktur menggunakan bullet points atau numbering jika perlu
• Jika informasi yang ditanyakan tidak ada di dokumen manapun, katakan: "Informasi tersebut tidak tersedia dalam dokumen yang ada"
• Gunakan bahasa Indonesia yang profesional namun mudah dipahami

💡 JAWABAN:"""
    )
    
    # Try modern approach first
    if create_retrieval_chain is not None and create_stuff_documents_chain is not None:
        # Create document chain
        combine_docs_chain = create_stuff_documents_chain(
            llm=llm,
            prompt=prompt_template
        )
        
        # Create retrieval chain
        rag_chain = create_retrieval_chain(
            retriever=retriever,
            combine_docs_chain=combine_docs_chain
        )
        
        return rag_chain
    
    else:
        # Fallback to RetrievalQA for compatibility
        from langchain.chains import RetrievalQA
        
        rag_chain = RetrievalQA.from_chain_type(
            llm=llm,
            chain_type="stuff",
            retriever=retriever,
            return_source_documents=True,  # Enable untuk mendapatkan sources
            chain_type_kwargs={"prompt": prompt_template}
        )
        
        return rag_chain


def answer_with_sources(
    rag_chain, 
    question: str, 
    model: str = "gpt-4.1",
    trace_id: str = None
) -> Dict[str, Any]:
    """
    Menjalankan RAG chain dan mengembalikan answer + sources yang tepat.
    
    Args:
        rag_chain: RAG chain dari build_rag_chain
        question: Pertanyaan user
        model: Model name untuk token tracking
        trace_id: ID untuk tracing (opsional)
    
    Returns:
        Dict dengan 'answer' dan 'sources' 
    """
    try:
        # Track token usage dengan callback
        with get_openai_callback() as cb:
            # Invoke RAG chain - handle both modern and fallback approaches
            if create_retrieval_chain is not None and hasattr(rag_chain, 'invoke'):
                # Modern approach with create_retrieval_chain
                result = rag_chain.invoke({"input": question})
                answer = result.get("answer", "Tidak ditemukan jawaban yang relevan.")
                context_docs = result.get("context", [])
                
                # EMERGENCY DEBUG
                logger.warning(f"RETRIEVAL DEBUG: Retrieved {len(context_docs)} documents")
                for i, doc in enumerate(context_docs[:3]):  # Log first 3
                    metadata = doc.metadata if hasattr(doc, 'metadata') else {}
                    logger.warning(f"  Doc {i}: source_document={metadata.get('source_document')}, version={metadata.get('version')}, text_preview={doc.page_content[:100]}")
            else:
                # Fallback approach with RetrievalQA
                result = rag_chain.invoke({"query": question})
                answer = result.get("result", "Tidak ditemukan jawaban yang relevan.")
                context_docs = result.get("source_documents", [])
                
                # EMERGENCY DEBUG
                logger.warning(f"RETRIEVAL DEBUG (fallback): Retrieved {len(context_docs)} documents")
        
        # Log token usage
        input_tokens = getattr(cb, 'prompt_tokens', 0)
        output_tokens = getattr(cb, 'completion_tokens', 0)
        
        # Fallback jika callback tidak memberikan token count
        if input_tokens == 0 and output_tokens == 0:
            # Estimasi dengan tiktoken
            input_text = question + "\n".join([doc.page_content for doc in context_docs])
            input_tokens = count_tokens(input_text)
            output_tokens = count_tokens(answer)
        
        # Log ke token ledger
        log_tokens(
            step="chat",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            trace_id=trace_id,
            question_length=len(question),
            num_context_docs=len(context_docs)
        )
        
        # Convert context documents ke sources format
        sources = []
        for i, doc in enumerate(context_docs):
            metadata = doc.metadata or {}
            
            # Extract/normalize similarity score from metadata if available
            score = metadata.get("relevance_score")
            if score is None:
                score = metadata.get("score")
            if score is None:
                dist = metadata.get("distance")
                try:
                    if dist is not None:
                        score = max(0.0, min(1.0, 1.0 - float(dist)))
                except Exception:
                    score = None

            # Extract relevant metadata
            source_info = {
                "id": f"doc_{i}",
                "score": score,
                "snippet": _build_snippet(doc.page_content, max_len=400),
                "metadata": {
                    "source_document": metadata.get("source_document", ""),
                    "version": metadata.get("version", ""),
                    "page": metadata.get("page"),
                    "chunk_id": metadata.get("chunk_id", ""),
                    "char_start": metadata.get("char_start"),
                    "char_end": metadata.get("char_end"),
                }
            }
            sources.append(source_info)
        
        logger.info(f"RAG query completed: {input_tokens}+{output_tokens} tokens, {len(sources)} sources")
        
        return {
            "answer": answer.strip(),
            "sources": sources,
            "token_usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens
            }
        }
        
    except Exception as e:
        logger.error(f"Error in answer_with_sources: {e}")
        
        # Log error event
        log_tokens(
            step="chat",
            model=model,
            input_tokens=count_tokens(question),
            output_tokens=0,
            trace_id=trace_id,
            error=str(e)
        )
        
        return {
            "answer": f"Terjadi kesalahan teknis: {str(e)}",
            "sources": [],
            "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        }


def _build_snippet(text: str, max_len: int = 400) -> str:
    """Build snippet yang dipotong pada word boundary."""
    if not text:
        return ""
    
    text = text.strip()
    if len(text) <= max_len:
        return text
    
    # Potong pada word boundary
    truncated = text[:max_len]
    last_space = truncated.rfind(' ')
    if last_space > max_len * 0.8:  # Jika space tidak terlalu jauh
        truncated = truncated[:last_space]
    
    return truncated + "..."


def create_filtered_retriever(vector_store: PineconeVectorStore, doc_id: str, version: str, k: int = 5):
    """
    Membuat retriever dengan filter untuk dokumen dan versi tertentu.
    
    CRITICAL FIX: Test shows namespace parameter works in __init__ but retrieval still fails.
    Problem likely: retriever wrapper not passing namespace to query operations.
    Solution: Return retriever without filter, let chain handle it.
    
    Args:
        vector_store: Pinecone vector store (already has namespace set)
        doc_id: Document ID untuk filter
        version: Version untuk filter (v1/v2)
        k: Jumlah dokumen yang diambil
    
    Returns:
        Retriever yang sudah difilter
    """
    logger.info(f"Creating retriever for doc_id={doc_id}, version={version}, k={k}")
    logger.info(f"VectorStore namespace: {vector_store._namespace}")
    
    # Try with simple filter - Pinecone native syntax (no $eq)
    retrieval_filter = {
        "source_document": doc_id,
        "version": version
    }
    
    logger.info(f"Filter: {retrieval_filter}")
    
    # Create retriever with filter
    retriever = vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={
            'k': k,
            'filter': retrieval_filter
        }
    )
    
    logger.info(f"Retriever created with filter")
    return retriever
