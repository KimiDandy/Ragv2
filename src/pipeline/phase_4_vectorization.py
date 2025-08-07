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
    doc_path = Path(doc_output_dir)
    doc_id = doc_path.name
    markdown_path = Path(doc_output_dir) / markdown_file

    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            final_content = f.read()
    except FileNotFoundError:
        logger.error(f"Final markdown file not found at {markdown_path}")
        return False

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", " ", ""]
    )
    chunks = text_splitter.split_text(final_content)

    if not chunks:
        logger.warning("No text chunks were generated from the document. Nothing to vectorize.")
        return True  

    logger.info("Initializing Google Generative AI embedding function...")
    if not GOOGLE_API_KEY:
        logger.error("GOOGLE_API_KEY not found in environment variables or config.")
        return False

    embeddings = GoogleGenerativeAIEmbeddings(
        model=EMBEDDING_MODEL, google_api_key=GOOGLE_API_KEY
    )

    vector_store = Chroma(
        client=client,
        collection_name=CHROMA_COLLECTION,
        embedding_function=embeddings,
    )

    logger.info(f"Adding {len(chunks)} chunks to the Chroma vector store (version: {version})...")
    vector_store.add_texts(
        texts=chunks,
        metadatas=[{"source_document": doc_id, "version": version} for _ in chunks],
        ids=[f"{doc_id}_{version}_{i}" for i in range(len(chunks))]
    )

    logger.info(f"Phase 4 completed. Document {doc_id} (version: {version}) has been vectorized and stored.")
    return True

if __name__ == '__main__':
    # This block is for standalone testing of the vectorization phase.
    # It requires the pipeline to have been run at least once to generate artefacts.
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
            
            # Note: Assuming the standard final markdown file name and using doc_id as version.
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
