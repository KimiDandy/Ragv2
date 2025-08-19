import fitz  # PyMuPDF
import uuid
import os
from pathlib import Path
from loguru import logger

from ..core.config import PIPELINE_ARTEFACTS_DIR

def process_pdf_local(pdf_path: str, output_base_dir: str = PIPELINE_ARTEFACTS_DIR) -> dict:
    """
    Memproses file PDF secara lokal untuk mengekstrak teks markdown dan gambar, lalu menyisipkan placeholder.

    Fungsi ini adalah Fase 0 dari proyek Genesis-RAG. Proses ini mengimplementasikan beberapa langkah utama:
    1. Mengekstrak semua gambar unik dari PDF menggunakan PyMuPDF dan menyimpannya.
    2. Mengambil semua blok teks dan blok gambar dari setiap halaman.
    3. Mengurutkan blok-blok tersebut berdasarkan posisi vertikalnya untuk merekonstruksi alur dokumen.
    4. Menghasilkan file markdown (markdown_v1.md) yang berisi teks dan placeholder untuk gambar.

    Args:
        pdf_path (str): Path menuju file PDF yang akan diproses.
        output_base_dir (str): Direktori dasar untuk menyimpan artefak yang diproses.

    Returns:
        dict: Dictionary yang berisi document_id dan path ke artefak yang dihasilkan.
    """
    doc_id = str(uuid.uuid4())
    doc_output_dir = Path(output_base_dir) / doc_id
    assets_dir = doc_output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    image_xrefs = set()
    final_markdown_content = ""

    for page_num, page in enumerate(doc):
        image_list = page.get_images(full=True)
        for img_index, img in enumerate(image_list):
            xref = img[0]
            if xref in image_xrefs:
                continue
            image_xrefs.add(xref)
            
            base_image = doc.extract_image(xref)
            image_bytes = base_image["image"]
            image_ext = base_image["ext"]
            image_filename = f"image_p{page_num+1}_{xref}.{image_ext}"
            
            with open(assets_dir / image_filename, "wb") as img_file:
                img_file.write(image_bytes)

        blocks = page.get_text("blocks")
        image_blocks = []
        for img in image_list:
            xref = img[0]
            bbox = img[1:5]
            image_filename = f"image_p{page_num+1}_{xref}.{doc.extract_image(xref)['ext']}"
            image_blocks.append((bbox[1], "image", image_filename, bbox))

        text_blocks = []
        for b in blocks:
            text_blocks.append((b[1], "text", b[4].strip(), b[:4]))

        all_blocks = sorted(image_blocks + text_blocks, key=lambda x: x[0])

        for _, block_type, content, _ in all_blocks:
            if block_type == "text" and content:
                final_markdown_content += content + "\n\n"
            elif block_type == "image":
                final_markdown_content += f"[IMAGE_PLACEHOLDER: {content}]\n\n"

    md_output_path = doc_output_dir / "markdown_v1.md"
    with open(md_output_path, "w", encoding="utf-8") as f:
        f.write(final_markdown_content)

    doc.close()

    result = {
        "document_id": doc_id,
        "markdown_path": str(md_output_path),
        "assets_path": str(assets_dir),
        "output_dir": str(doc_output_dir)
    }

    logger.info(f"Fase 0 selesai untuk dokumen: {pdf_path}")
    logger.info(f"Artefak disimpan di: {doc_output_dir}")
    
    return result

if __name__ == '__main__':
    if not os.path.exists("dummy.pdf"):
        doc = fitz.open() 
        page = doc.new_page()
        page.insert_text((50, 72), "This is the first paragraph.")
        try:
            rect = fitz.Rect(72, 100, 200, 200)
            page.draw_rect(rect, color=(0,0,1), fill=(0,1,0))
            page.insert_text((50, 250), "This is the second paragraph, after the image.")
        except Exception as e:
            logger.error(f"Could not add image to dummy PDF: {e}")
        doc.save("dummy.pdf")
        logger.info("Created dummy.pdf for testing.")

    if not os.path.exists(PIPELINE_ARTEFACTS_DIR):
        os.makedirs(PIPELINE_ARTEFACTS_DIR)

    process_pdf_local("dummy.pdf")
