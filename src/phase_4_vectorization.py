import os
from pathlib import Path
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
import chromadb



def vectorize_and_store(doc_output_dir: str, client: chromadb.Client) -> bool:
    """
    Vectorizes the final markdown document and stores it in ChromaDB.

    This function corresponds to Phase 4 of the Genesis-RAG project.
    It chunks the markdown_v2.md file, generates embeddings for each chunk,
    and stores them in a persistent ChromaDB collection.

    Args:
        doc_output_dir (str): The document's artefact directory.
        chroma_db_path (str): The path to the ChromaDB persistence directory.

    Returns:
        bool: True if the process was successful, False otherwise.
    """
    doc_path = Path(doc_output_dir)
    doc_id = doc_path.name
    markdown_path = doc_path / "markdown_v2.md"

    # Load the final markdown document
    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            final_content = f.read()
    except FileNotFoundError:
        print(f"Error: Final markdown file not found at {markdown_path}")
        return False

    # 1. Chunk the document
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", " ", ""]
    )
    chunks = text_splitter.split_text(final_content)

    if not chunks:
        print("Warning: No text chunks were generated from the document. Nothing to vectorize.")
        return True  # Technically successful as there's nothing to do

    # 2. Initialize Google Generative AI embedding function via Langchain wrapper
    print("Initializing Google Generative AI embedding function...")
    # Ensure the GOOGLE_API_KEY is loaded from .env
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("Error: GOOGLE_API_KEY not found in environment variables.")
        return False


    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/embedding-001", google_api_key=api_key
    )

    # 4. Create a Chroma vector store instance using the provided client
    vector_store = Chroma(
        client=client,
        collection_name="genesis_rag_collection",
        embedding_function=embeddings,
    )

    # 5. Add the chunked texts to the vector store
    print(f"Adding {len(chunks)} chunks to the Chroma vector store...")
    vector_store.add_texts(
        texts=chunks,
        metadatas=[{"source_document": doc_id} for _ in chunks],
        ids=[f"{doc_id}_{i}" for i in range(len(chunks))]
    )

    print(f"Phase 4 completed. Document {doc_id} has been vectorized and stored.")
    return True

if __name__ == '__main__':
    import chromadb
    
    # This block is for standalone testing of this script.
    # It is not used when running the FastAPI application.
    base_artefacts_dir = Path("pipeline_artefacts")
    if not base_artefacts_dir.exists():
        print("Error: 'pipeline_artefacts' directory not found.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            print("No document artefact directories found in 'pipeline_artefacts'.")
        else:
            print("Running in standalone mode. Connecting to ChromaDB server...")
            # Gunakan HttpClient untuk konsistensi dengan main.py
            standalone_client = chromadb.HttpClient(host="localhost", port=8001)
            
            latest_doc_dir = max(all_doc_dirs, key=lambda p: p.stat().st_mtime)
            print(f"Processing the most recent document: {latest_doc_dir.name}")
            success = vectorize_and_store(str(latest_doc_dir), standalone_client)
            if success:
                print("Standalone vectorization test completed successfully.")
            else:
                print("Standalone vectorization test failed.")
