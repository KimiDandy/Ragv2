import os
import tempfile
import logging
from pathlib import Path
from typing import List

# Environment and configuration
from dotenv import load_dotenv
import streamlit as st

# Updated LangChain imports for v0.1+
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.schema import Document
from langchain_community.document_loaders import TextLoader, UnstructuredPDFLoader
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_pinecone import PineconeVectorStore
from langchain_core.messages import HumanMessage

# Pinecone
from pinecone import Pinecone as PineconeClient, ServerlessSpec

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TMP_DIR = Path(__file__).resolve().parent.joinpath('data', 'tmp')

st.set_page_config(page_title="RAG")
st.title("Retrieval Augmented Generation Engine")

# Application configuration defaults
DEFAULT_MODEL = "gpt-4.1"
MODEL_OPTIONS = {
    "gpt-4.1": "GPT-4.1 (default)",
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini",
    "gpt-4-turbo-preview": "GPT-4 Turbo Preview",
}

DEFAULT_CHUNK_SIZE = 1000
CHUNK_SIZE_RANGE = (300, 4000)
DEFAULT_CHUNK_OVERLAP = 150
CHUNK_OVERLAP_RANGE = (0, 600)

DEFAULT_MAX_TOKENS = 4096
MAX_TOKENS_RANGE = (512, 4096)

DEFAULT_TEMPERATURE = 0.7

DEFAULT_SYSTEM_PROMPT = (
    "Anda adalah analis dokumen profesional yang ahli dalam mengekstrak dan merangkum informasi dari berbagai jenis dokumen bisnis dan ekonomi. "
    "Fokus pada data faktual, angka, tren, dan analisis utama. Abaikan teks boilerplate, disclaimer, atau informasi legal kecuali secara eksplisit ditanyakan. "
    "Berikan jawaban yang komprehensif, terstruktur, dan mudah dipahami dalam bahasa Indonesia."
)


def initialize_session_state():
    """Seed default configuration values into Streamlit session state."""
    if "app_initialized" not in st.session_state:
        st.session_state.app_initialized = True
        st.session_state.model_name = DEFAULT_MODEL
        st.session_state.chunk_size = DEFAULT_CHUNK_SIZE
        st.session_state.chunk_overlap = DEFAULT_CHUNK_OVERLAP
        st.session_state.max_tokens = DEFAULT_MAX_TOKENS
        st.session_state.temperature = DEFAULT_TEMPERATURE
        st.session_state.system_prompt = DEFAULT_SYSTEM_PROMPT
        st.session_state.upload_detected = False
        st.session_state.documents_ready = False
        st.session_state.retriever = None
        st.session_state.last_processing_stats = None
        st.session_state.last_answer_sources = []
        st.session_state.source_docs = None
    
    # Always ensure chat_history is a clean list 
    if "chat_history" not in st.session_state or not isinstance(st.session_state.chat_history, list):
        st.session_state.chat_history = []


def load_credentials():
    """Populate API credentials from secrets or environment variables."""
    if not st.session_state.get("openai_api_key"):
        if "openai" in st.secrets and "api_key" in st.secrets.openai:
            st.session_state.openai_api_key = st.secrets.openai.api_key
        elif os.getenv("OPENAI_API_KEY"):
            st.session_state.openai_api_key = os.getenv("OPENAI_API_KEY")

    if not st.session_state.get("pinecone_api_key"):
        if "pinecone" in st.secrets and "api_key" in st.secrets.pinecone:
            st.session_state.pinecone_api_key = st.secrets.pinecone.api_key
        elif os.getenv("PINECONE_API_KEY"):
            st.session_state.pinecone_api_key = os.getenv("PINECONE_API_KEY")

    if not st.session_state.get("pinecone_index"):
        if "pinecone" in st.secrets and "index_name" in st.secrets.pinecone:
            st.session_state.pinecone_index = st.secrets.pinecone.index_name
        elif os.getenv("PINECONE_INDEX_NAME"):
            st.session_state.pinecone_index = os.getenv("PINECONE_INDEX_NAME")


def check_system_readiness() -> tuple[bool, List[str]]:
    """Return readiness flag and any missing configuration labels."""
    missing = []
    if not st.session_state.get("openai_api_key"):
        missing.append("Kunci OpenAI")
    if not st.session_state.get("pinecone_api_key"):
        missing.append("Kunci Pinecone")
    if not st.session_state.get("pinecone_index"):
        missing.append("Nama indeks Pinecone")
    return (len(missing) == 0, missing)


def load_documents() -> List[Document]:
    """Load markdown and PDF documents from the temporary directory."""
    try:
        all_documents = []
        
        # Load markdown files using TextLoader (simpler approach)
        for md_file in TMP_DIR.glob('**/*.md'):
            try:
                loader = TextLoader(str(md_file), encoding='utf-8')
                docs = loader.load()
                # Add metadata about file type
                for doc in docs:
                    doc.metadata['file_type'] = 'markdown'
                    doc.metadata['source'] = str(md_file.name)
                all_documents.extend(docs)
                logger.info(f"Loaded markdown file: {md_file.name}")
            except Exception as e:
                logger.warning(f"Failed to load markdown file {md_file.name}: {e}")
        
        # Load PDF files using UnstructuredPDFLoader
        for pdf_file in TMP_DIR.glob('**/*.pdf'):
            try:
                loader = UnstructuredPDFLoader(str(pdf_file))
                docs = loader.load()
                # Add metadata about file type
                for doc in docs:
                    doc.metadata['file_type'] = 'pdf'
                    doc.metadata['source'] = str(pdf_file.name)
                all_documents.extend(docs)
                logger.info(f"Loaded PDF file: {pdf_file.name}")
            except Exception as e:
                logger.warning(f"Failed to load PDF file {pdf_file.name}: {e}")
        
        logger.info(f"Successfully loaded {len(all_documents)} documents total")
        return all_documents
    except Exception as e:
        logger.error(f"Error loading documents: {e}")
        st.error(f"Error loading documents: {e}")
        return []

def split_documents(documents: List[Document]) -> List[Document]:
    """Split documents into smaller chunks for better processing."""
    try:
        chunk_size = int(st.session_state.get('chunk_size', DEFAULT_CHUNK_SIZE))
        chunk_overlap = int(st.session_state.get('chunk_overlap', DEFAULT_CHUNK_OVERLAP))

        # Guard against invalid overlap values
        if chunk_overlap >= chunk_size:
            chunk_overlap = max(0, chunk_size // 4)
            st.session_state.chunk_overlap = chunk_overlap

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=["\n\n", "\n", "|", ".", "!", "?", " ", ""],
            keep_separator=True,
            add_start_index=True
        )
        texts = text_splitter.split_documents(documents)
        logger.info(
            "Split %s documents into %s chunks (size=%s, overlap=%s)",
            len(documents),
            len(texts),
            chunk_size,
            chunk_overlap,
        )
        return texts
    except Exception as e:
        logger.error(f"Error splitting documents: {e}")
        st.error(f"Error splitting documents: {e}")
        return []

def embeddings_on_pinecone(texts: List[Document]):
    """Create embeddings using Pinecone vector store."""
    try:
        # Get API keys from session state or environment
        openai_api_key = st.session_state.get('openai_api_key') or os.getenv('OPENAI_API_KEY')
        pinecone_api_key = st.session_state.get('pinecone_api_key') or os.getenv('PINECONE_API_KEY')
        index_name = st.session_state.get('pinecone_index') or os.getenv('PINECONE_INDEX_NAME')
        
        # Initialize Pinecone client (new API)
        pc = PineconeClient(api_key=pinecone_api_key)
        
        # Check if index exists, create if not
        if index_name not in pc.list_indexes().names():
            pc.create_index(
                name=index_name,
                dimension=1536,  # OpenAI embedding dimension
                metric="cosine",
                spec=ServerlessSpec(
                    cloud="aws",
                    region="us-east-1"
                )
            )
            logger.info(f"Created new Pinecone index: {index_name}")
        
        # Create embeddings
        embeddings = OpenAIEmbeddings(
            openai_api_key=openai_api_key,
            model="text-embedding-ada-002"
        )
        
        # Create vector store
        vectorstore = PineconeVectorStore(
            index_name=index_name,
            embedding=embeddings
        )
        
        # Add documents to the vector store
        vectorstore.add_documents(texts)
        
        retriever = vectorstore.as_retriever(search_kwargs={'k': 15})
        logger.info(f"Created Pinecone vector store with {len(texts)} documents")
        return retriever
    except Exception as e:
        logger.error(f"Error creating Pinecone vector store: {e}")
        st.error(f"Error creating Pinecone vector store: {e}")
        return None

def query_llm(retriever, query: str) -> str:
    """Query the configured LLM with retrieval augmented generation using a simpler approach."""
    try:
        # Get configuration from session state
        openai_api_key = st.session_state.get('openai_api_key') or os.getenv('OPENAI_API_KEY')
        model_name = st.session_state.get('model_name', DEFAULT_MODEL)
        max_tokens = int(st.session_state.get('max_tokens', DEFAULT_MAX_TOKENS))
        temperature = float(st.session_state.get('temperature', DEFAULT_TEMPERATURE))
        system_prompt = st.session_state.get('system_prompt', DEFAULT_SYSTEM_PROMPT)

        # Initialize ChatOpenAI
        llm = ChatOpenAI(
            openai_api_key=openai_api_key,
            model_name=model_name,
            max_tokens=max_tokens,
            temperature=temperature
        )

        # Get relevant documents with enhanced retrieval
        relevant_docs = retriever.get_relevant_documents(query)
        
        # Deduplicate and diversify documents
        unique_docs = []
        seen_content = set()
        
        for doc in relevant_docs:
            # Create a hash of the first 100 characters to check for duplicates
            content_hash = hash(doc.page_content[:100].strip())
            if content_hash not in seen_content:
                seen_content.add(content_hash)
                unique_docs.append(doc)
        
        # Sort by relevance and take top chunks, ensuring diversity
        final_docs = unique_docs[:10]  # Limit to top 10 unique chunks
        
        # Combine document content with better formatting
        context_parts = []
        for i, doc in enumerate(final_docs, 1):
            content = doc.page_content.strip()
            if content:  # Only add non-empty content
                context_parts.append(f"[Bagian {i}]\n{content}")
        
        context = "\n\n".join(context_parts)
        
        # Get chat history for context
        chat_history_text = ""
        raw_history = st.session_state.get('chat_history', [])
        
        if raw_history and isinstance(raw_history, list):
            try:
                for item in raw_history[-3:]:  # Last 3 conversations for context
                    if isinstance(item, (tuple, list)) and len(item) == 2:
                        human_msg, ai_msg = item
                        if isinstance(human_msg, str) and isinstance(ai_msg, str):
                            chat_history_text += f"\nUser: {human_msg}\nAssistant: {ai_msg}\n"
            except Exception as hist_error:
                logger.warning(f"Error processing chat history: {hist_error}")
                chat_history_text = ""

        # Enhanced prompt for better comprehension
        full_prompt = f"""{system_prompt}

Anda akan menganalisis dokumen yang berisi berbagai jenis informasi seperti data ekonomi, tabel, angka, dan teks naratif. Berikan jawaban yang komprehensif dan terstruktur.

KONTEKS DOKUMEN:
{context}

{f"RIWAYAT PERCAKAPAN:{chat_history_text}" if chat_history_text else ""}

PERTANYAAN: {query}

INSTRUKSI KHUSUS:
- Jika ditanya tentang isi dokumen secara umum, berikan ringkasan yang mencakup SEMUA aspek utama: data, tabel, analisis, dan kesimpulan
- Prioritaskan informasi faktual dan data numerik daripada disclaimer atau teks boilerplate
- Strukturkan jawaban dengan bullet points atau numbering jika relevan
- Jangan hanya fokus pada satu bagian dokumen saja

Jawaban:"""

        # Get response from LLM
        messages = [HumanMessage(content=full_prompt)]
        response = llm.invoke(messages)
        answer = response.content

        # Update chat history
        if 'chat_history' not in st.session_state:
            st.session_state.chat_history = []
        st.session_state.chat_history.append((query, answer))
        
        # Store unique source documents for display
        st.session_state.last_answer_sources = final_docs

        logger.info("Generated answer for query: %s", query[:80])
        return answer

    except Exception as e:
        logger.error(f"Error querying LLM: {e}")
        st.error(f"Error querying LLM: {e}")
        return "Maaf, terjadi kesalahan saat memproses pertanyaan Anda."

def render_sidebar():
    """Render sidebar with system status and advanced controls."""
    initialize_session_state()
    load_credentials()
    ready, missing = check_system_readiness()

    with st.sidebar:
        st.header("Status Sistem")
        if ready:
            st.success("Sistem siap digunakan ✅")
        else:
            st.error("Sistem belum siap")
            for item in missing:
                st.warning(f"• {item} belum tersedia")

        if st.session_state.last_processing_stats:
            st.divider()
            stats = st.session_state.last_processing_stats
            st.subheader("Ringkasan Pemrosesan")
            st.metric("Jumlah Dokumen", stats["document_count"])
            st.metric("Total Chunk", stats["chunk_count"])
            st.caption(
                f"Model: {stats['model']} • Chunk: {stats['chunk_size']} | Overlap: {stats['chunk_overlap']} | "
                f"Max Tokens: {stats['max_tokens']} | Temperatur: {stats['temperature']}"
            )

        if st.session_state.source_docs and ready:
            st.divider()
            st.subheader("Pengaturan Lanjutan")

            # Model selection
            model_labels = [MODEL_OPTIONS[key] for key in MODEL_OPTIONS]
            model_index = list(MODEL_OPTIONS.keys()).index(st.session_state.model_name)
            selected_label = st.selectbox(
                "Model OpenAI",
                options=model_labels,
                index=model_index,
                help="Pilih model GPT yang ingin digunakan"
            )
            for key, label in MODEL_OPTIONS.items():
                if label == selected_label:
                    st.session_state.model_name = key
                    break
            st.caption("Model menentukan kecanggihan analisis dan biaya pemanggilan API. Gunakan model default kecuali ada kebutuhan khusus.")

            st.session_state.chunk_size = st.slider(
                "Chunk size",
                min_value=CHUNK_SIZE_RANGE[0],
                max_value=CHUNK_SIZE_RANGE[1],
                step=50,
                value=int(st.session_state.chunk_size),
                help="Ukuran teks (dalam karakter) yang dimasukkan ke embedding per potongan."
            )
            st.caption("Chunk size menentukan panjang setiap potongan teks. Nilai besar membuat konteks lebih lengkap namun lebih berat diproses.")

            st.session_state.chunk_overlap = st.slider(
                "Chunk overlap",
                min_value=CHUNK_OVERLAP_RANGE[0],
                max_value=min(CHUNK_OVERLAP_RANGE[1], st.session_state.chunk_size - 1),
                step=25,
                value=int(min(st.session_state.chunk_overlap, st.session_state.chunk_size - 1)),
                help="Bagian teks yang diulang antar chunk agar konteks tidak terputus."
            )
            st.caption("Chunk overlap menjaga kesinambungan antar potongan. Nilai tinggi meningkatkan akurasi, tetapi menambah duplikasi data.")

            st.session_state.max_tokens = st.number_input(
                "Max tokens per jawaban",
                min_value=MAX_TOKENS_RANGE[0],
                max_value=MAX_TOKENS_RANGE[1],
                step=128,
                value=int(st.session_state.max_tokens),
                help="Jumlah token maksimum yang boleh digunakan model untuk menyusun jawaban."
            )
            st.caption("Makin besar nilai ini, makin panjang jawaban yang diizinkan. Sesuaikan dengan kebutuhan ringkasan atau detail.")

            st.session_state.temperature = st.slider(
                "Temperatur",
                min_value=0.0,
                max_value=1.0,
                step=0.05,
                value=float(st.session_state.temperature),
                help="Mengatur tingkat kreativitas jawaban."
            )
            st.caption("Temperatur rendah → jawaban lebih konsisten dan faktual. Temperatur tinggi → jawaban lebih variatif atau kreatif.")

            st.session_state.system_prompt = st.text_area(
                "Instruksi sistem",
                value=st.session_state.system_prompt,
                height=160,
                help="Atur gaya jawaban, prioritas informasi, atau batasan khusus untuk AI."
            )
            st.caption("Ubah instruksi sistem bila ingin mengarahkan gaya bicara atau fokus analisis AI.")

            st.caption("Pengaturan ini akan berlaku pada pemrosesan dan percakapan selanjutnya.")


def process_documents():
    """Process uploaded documents and create vector embeddings."""
    ready, missing_items = check_system_readiness()
    if not ready:
        st.warning(
            "Sistem belum siap. Mohon lengkapi konfigurasi berikut: " + ", ".join(missing_items)
        )
        return

    if not st.session_state.get('source_docs'):
        st.warning("Silakan unggah dokumen terlebih dahulu.")
        return

    try:
        st.session_state.documents_ready = False
        st.session_state.retriever = None
        st.session_state.last_processing_stats = None
        st.session_state.chat_history = []  # Reset to empty list
        st.session_state.last_answer_sources = []

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        for old_file in TMP_DIR.iterdir():
            if old_file.is_file():
                try:
                    old_file.unlink()
                    logger.info(f"Cleaned up old file: {old_file.name}")
                except Exception as e:
                    logger.warning(f"Could not remove old file {old_file.name}: {e}")

        with st.spinner("Memproses dokumen..."):
            progress_bar = st.progress(0)
            uploaded_files = st.session_state.source_docs

            for idx, source_doc in enumerate(uploaded_files):
                progress_bar.progress(((idx + 1) / max(len(uploaded_files), 1)) * 0.3)

                if source_doc.name.endswith(('.md', '.markdown')):
                    file_extension = '.md'
                elif source_doc.name.endswith('.pdf'):
                    file_extension = '.pdf'
                else:
                    file_extension = '.md' if 'markdown' in source_doc.type else '.pdf'

                try:
                    source_doc.seek(0)
                except Exception:
                    pass

                with tempfile.NamedTemporaryFile(
                    delete=False,
                    dir=TMP_DIR.as_posix(),
                    suffix=file_extension
                ) as tmp_file:
                    tmp_file.write(source_doc.read())

            progress_bar.progress(0.45)
            documents = load_documents()
            if not documents:
                st.error("Tidak ada dokumen yang berhasil dibaca. Periksa kembali berkas Anda.")
                return

            progress_bar.progress(0.6)
            texts = split_documents(documents)
            if not texts:
                st.error("Dokumen tidak dapat dipecah menjadi chunk. Periksa pengaturan chunk size.")
                return

            progress_bar.progress(0.8)
            st.session_state.retriever = embeddings_on_pinecone(texts)

            progress_bar.progress(1.0)

            for _file in TMP_DIR.iterdir():
                if _file.is_file():
                    try:
                        _file.unlink()
                        logger.info(f"Cleaned up processed file: {_file.name}")
                    except Exception as cleanup_error:
                        logger.warning(f"Could not cleanup file {_file.name}: {cleanup_error}")

            if st.session_state.retriever:
                st.success(
                    f"✅ {len(documents)} dokumen berhasil diproses menjadi {len(texts)} chunk."
                )
                st.info("🎯 Anda bisa mulai bertanya mengenai dokumen di bawah ini.")
                st.session_state.documents_ready = True
                st.session_state.last_processing_stats = {
                    "document_count": len(documents),
                    "chunk_count": len(texts),
                    "chunk_size": st.session_state.get('chunk_size', DEFAULT_CHUNK_SIZE),
                    "chunk_overlap": st.session_state.get('chunk_overlap', DEFAULT_CHUNK_OVERLAP),
                    "model": st.session_state.get('model_name', DEFAULT_MODEL),
                    "temperature": st.session_state.get('temperature', DEFAULT_TEMPERATURE),
                    "max_tokens": st.session_state.get('max_tokens', DEFAULT_MAX_TOKENS),
                }
            else:
                st.error("Gagal membuat vector store dari dokumen.")

    except Exception as e:
        logger.error(f"Error processing documents: {e}")
        st.error(f"Terjadi kesalahan saat memproses dokumen: {e}")
        try:
            for _file in TMP_DIR.iterdir():
                if _file.is_file():
                    _file.unlink()
        except Exception:
            pass
        st.session_state.documents_ready = False
        st.session_state.retriever = None
        st.session_state.last_processing_stats = None


def boot():
    """Main entry point for the Streamlit app."""
    try:
        render_sidebar()

        st.subheader("📁 Upload Dokumen")
        st.caption("Unggah file markdown atau PDF untuk dianalisis.")

        uploaded_files = st.file_uploader(
            "Pilih dokumen",
            type=["md", "markdown", "pdf"],
            accept_multiple_files=True,
            help="Unggah beberapa dokumen sekaligus untuk diproses."
        )

        if uploaded_files:
            st.session_state.source_docs = uploaded_files
            st.session_state.upload_detected = True
        elif not st.session_state.documents_ready:
            st.session_state.source_docs = None
            st.session_state.upload_detected = False

        if st.session_state.source_docs:
            st.success(f"{len(st.session_state.source_docs)} dokumen siap diproses.")
            with st.expander("Daftar dokumen", expanded=False):
                for doc in st.session_state.source_docs:
                    st.write(f"• {doc.name}")

        ready, _ = check_system_readiness()
        st.button(
            "🚀 Proses Dokumen",
            on_click=process_documents,
            type="primary",
            use_container_width=True,
            disabled=not (ready and st.session_state.source_docs)
        )

        st.divider()
        st.subheader("💬 Tanya Dokumen")

        if st.session_state.chat_history:
            for user_msg, bot_msg in st.session_state.chat_history:
                with st.chat_message("human"):
                    st.write(user_msg)
                with st.chat_message("assistant"):
                    st.write(bot_msg)

        if query := st.chat_input("Tulis pertanyaan Anda di sini..."):
            if not st.session_state.retriever:
                st.error("Mohon proses dokumen terlebih dahulu sebelum bertanya.")
            else:
                with st.chat_message("human"):
                    st.write(query)
                with st.chat_message("assistant"):
                    with st.spinner("Menganalisis dokumen..."):
                        response = query_llm(st.session_state.retriever, query)
                    st.write(response)

                if st.session_state.last_answer_sources:
                    with st.expander("Sumber jawaban"):
                        for idx, doc in enumerate(st.session_state.last_answer_sources, start=1):
                            st.markdown(f"**Bagian {idx}** — {doc.metadata.get('source', 'unknown')}")
                            st.write(doc.page_content[:500] + ("..." if len(doc.page_content) > 500 else ""))

    except Exception as e:
        logger.error(f"Error in boot function: {e}")
        st.error(f"Application error: {e}")


if __name__ == '__main__':
    boot()