import os
from pathlib import Path
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_chroma import Chroma
import chromadb



def vectorize_and_store(doc_output_dir: str, client: chromadb.Client, markdown_file: str, version: str) -> bool:
    doc_path = Path(doc_output_dir)
    doc_id = doc_path.name
    markdown_path = Path(doc_output_dir) / markdown_file

    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            final_content = f.read()
    except FileNotFoundError:
        print(f"Error: Final markdown file not found at {markdown_path}")
        return False

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=150,
        separators=["\n\n", "\n", " ", ""]
    )
    chunks = text_splitter.split_text(final_content)

    if not chunks:
        print("Warning: No text chunks were generated from the document. Nothing to vectorize.")
        return True  

    print("Initializing Google Generative AI embedding function...")
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("Error: GOOGLE_API_KEY not found in environment variables.")
        return False


    embeddings = GoogleGenerativeAIEmbeddings(
        model="models/embedding-001", google_api_key=api_key
    )

    vector_store = Chroma(
        client=client,
        collection_name="genesis_rag_collection",
        embedding_function=embeddings,
    )

    print(f"Adding {len(chunks)} chunks to the Chroma vector store (version: {version})...")
    vector_store.add_texts(
        texts=chunks,
        metadatas=[{"source_document": doc_id, "version": version} for _ in chunks],
        ids=[f"{doc_id}_{version}_{i}" for i in range(len(chunks))]
    )

    print(f"Phase 4 completed. Document {doc_id} (version: {version}) has been vectorized and stored.")
    return True

if __name__ == '__main__':
    import chromadb
    
    base_artefacts_dir = Path("pipeline_artefacts")
    if not base_artefacts_dir.exists():
        print("Error: 'pipeline_artefacts' directory not found.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            print("No document artefact directories found in 'pipeline_artefacts'.")
        else:
            print("Running in standalone mode. Connecting to ChromaDB server...")
            standalone_client = chromadb.HttpClient(host="localhost", port=8001)
            
            latest_doc_dir = max(all_doc_dirs, key=lambda p: p.stat().st_mtime)
            print(f"Processing the most recent document: {latest_doc_dir.name}")
            success = vectorize_and_store(str(latest_doc_dir), standalone_client)
            if success:
                print("Standalone vectorization test completed successfully.")
            else:
                print("Standalone vectorization test failed.")
