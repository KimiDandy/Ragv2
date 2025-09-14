import os
import io
import json
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

import fitz  # PyMuPDF
from loguru import logger

# Optional/soft deps
try:
    import camelot  # type: ignore
except Exception:
    camelot = None

try:
    import pytesseract  # type: ignore
    from PIL import Image
except Exception:
    pytesseract = None
    Image = None

try:
    from openai import OpenAI  # type: ignore
except Exception:
    OpenAI = None  # type: ignore

from ..core.config import PIPELINE_ARTEFACTS_DIR


@dataclass
class UnitMetadata:
    doc_id: str
    page: int
    unit_type: str  # paragraph | table | table_row | table_cell | figure
    section: str
    bbox: Tuple[float, float, float, float]
    panel: Optional[str] = None
    row_label: Optional[str] = None
    col_label: Optional[str] = None
    unit: Optional[str] = None
    row_idx: Optional[int] = None
    col_idx: Optional[int] = None


def _save_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _render_page_png(page: fitz.Page, out_path: Path, zoom: float = 2.0):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    pix.save(out_path)


def _detect_headings_from_spans(spans: List[Dict[str, Any]]) -> bool:
    # Very light heuristic: large font size or bold caps
    try:
        sizes = [s.get("size", 0) for s in spans if isinstance(s, dict)]
        max_size = max(sizes) if sizes else 0
        text = " ".join([s.get("text", "") for s in spans])
        is_caps = text.strip() and text.strip().upper() == text.strip() and len(text.strip()) < 120
        return max_size >= 14 or is_caps
    except Exception:
        return False


def _bbox_of_block(block: Dict[str, Any]) -> Tuple[float, float, float, float]:
    b = block.get("bbox") or [0, 0, 0, 0]
    return float(b[0]), float(b[1]), float(b[2]), float(b[3])


def _norm_spaces(text: str) -> str:
    return "\n".join([" ".join(line.split()) for line in (text or "").splitlines()]).strip()


def _markdown_table_from_df(df) -> str:
    # df is a pandas DataFrame from camelot
    import pandas as pd  # local import to avoid hard dep at import time
    if not isinstance(df, pd.DataFrame) or df.empty:
        return ""
    # Ensure strings
    df = df.fillna("")
    # Align numbers right by adding spaces; in Markdown this is limited, but okay.
    headers = [str(h) for h in df.columns.tolist()]
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    aligns = [":-" if not str(h).strip() else "-:" if any(ch.isdigit() for ch in str(h)) else ":-:" for h in headers]
    # Simplified alignment row
    lines.append("| " + " | ".join(["---" for _ in headers]) + " |")
    for _, row in df.iterrows():
        vals = [str(v) for v in row.tolist()]
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def _read_tables_with_camelot(pdf_path: str, page_no: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if camelot is None:
        return results
    page_str = str(page_no)
    flavors = ["lattice", "stream"]
    for flavor in flavors:
        try:
            tables = camelot.read_pdf(pdf_path, pages=page_str, flavor=flavor, strip_text="\n")
        except Exception:
            continue
        for idx, t in enumerate(getattr(tables, "_tables", []) or tables):
            try:
                df = t.df
                if getattr(df, "empty", True):
                    continue
                # Heuristic validity: at least 2 columns and 2 rows
                if df.shape[0] < 2 or df.shape[1] < 2:
                    continue
                md = _markdown_table_from_df(df)
                if not md.strip():
                    continue
                results.append({
                    "flavor": flavor,
                    "index": idx,
                    "df": df,  # will not be JSON-serializable; drop later
                    "markdown": md,
                })
            except Exception:
                continue
        if results:
            break
    return results


def _ocr_image_to_text(img_path: Path) -> str:
    if pytesseract is None or Image is None:
        return ""
    try:
        # Try to fix Windows Tesseract PATH issue
        import shutil
        if not shutil.which("tesseract"):
            # Common Windows Tesseract locations
            possible_paths = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                r"C:\Users\{}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe".format(os.environ.get("USERNAME", "")),
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    pytesseract.pytesseract.tesseract_cmd = path
                    logger.info(f"[PDF++] Found Tesseract at: {path}")
                    break
        
        img = Image.open(img_path)
        # Light pre-processing could be added here
        text = pytesseract.image_to_string(img, lang="eng+ind")
        return _norm_spaces(text)
    except Exception as e:
        logger.warning(f"[PDF++] Tesseract OCR failed: {e}")
        return ""


def _gpt4o_describe_image(img_path: Path, api_key: str) -> str:
    """GPT-4o mini vision: comprehensive detailed description for figures/charts."""
    if not api_key:
        logger.warning("[PDF++] GPT-4o: No API key provided")
        return ""
    
    logger.info(f"[PDF++] GPT-4o: Starting analysis of {img_path}")
    try:
        import base64
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        
        with open(img_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")
        
        logger.info(f"[PDF++] GPT-4o: Image encoded, size: {len(img_data)} chars")
        
        try:
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": """Analyze this image/chart/diagram comprehensively and provide a detailed textual description. Include:

1. Type of visualization (chart, graph, table, diagram, etc.)
2. All visible text, labels, titles, and legends
3. Data structure and categories shown
4. Trends, patterns, or relationships visible
5. All numerical values that are clearly readable
6. Axis labels, units, scales if present
7. Colors, symbols, or visual elements that convey meaning
8. Any annotations or callouts

Write in Indonesian. Be thorough and precise - this description will be used by an AI system to understand the content when the original image cannot be processed. Do not fabricate or guess numbers that aren't clearly visible, but describe everything that IS visible in detail.""",
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{img_data}"},
                            },
                        ],
                    }
                ],
                max_tokens=800,
                temperature=0.1,
            )
            result = (resp.choices[0].message.content or "").strip()
            logger.info(f"[PDF++] GPT-4o: Success, response length: {len(result)}")
            return result
        except Exception as e:
            logger.error(f"[PDF++] GPT-4o API call failed: {e}")
            return ""
    except Exception as e:
        logger.error(f"[PDF++] GPT-4o setup failed: {e}")
        return ""


def process_pdf_markdownpp(pdf_path: str, mode: str = "basic", doc_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Convert a mixed-content PDF into tidy Markdown with structure:
    - Text: PyMuPDF native, rebuild paragraphs, detect headings
    - Tables: Camelot lattice->stream, fallback OCR (light) per image area (omitted unless detected)
    - Figures: basic OCR label; if mode == 'smart', optional GPT-4o-mini narrative (no numbers fabricated)

    Returns artefact paths and writes markdown_v1.md for downstream compatibility.
    """
    started = time.time()
    doc_id = doc_id or str(uuid.uuid4())
    doc_dir = Path(PIPELINE_ARTEFACTS_DIR) / doc_id
    pages_dir = doc_dir / "pages"
    tables_dir = doc_dir / "tables"
    figures_dir = doc_dir / "figures"
    meta_dir = doc_dir / "meta"
    for d in (pages_dir, tables_dir, figures_dir, meta_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Load API key if needed for smart mode
    api_key = os.environ.get("OPENAI_API_KEY") if mode == "smart" else None
    
    logger.info(f"[PDF++] Start conversion: {pdf_path} -> {doc_dir}")
    logger.info(f"[PDF++] Mode: {mode}")
    logger.info(f"[PDF++] Tesseract available: {pytesseract is not None}")
    logger.info(f"[PDF++] API key loaded: {'Yes' if api_key else 'No'}")
    if mode == "smart" and not api_key:
        logger.warning("[PDF++] Smart mode requested but no API key available")

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        raise RuntimeError(f"Tidak bisa membuka PDF: {e}")

    units_meta: List[Dict[str, Any]] = []
    parts: List[str] = []

    # Progress artefact
    progress_path = doc_dir / "conversion_progress.json"
    _save_json(progress_path, {"status": "running", "percent": 0.05, "message": "Mulai konversi"})

    # Render pages for debugging
    for i in range(doc.page_count):
        try:
            _render_page_png(doc.load_page(i), pages_dir / f"page_{i+1:03d}.png", zoom=2.0)
        except Exception:
            pass

    # Document-level heading path stack
    heading_stack: List[str] = []

    for i in range(doc.page_count):
        page = doc.load_page(i)
        page_no = i + 1
        _save_json(progress_path, {"status": "running", "percent": round((i / max(1, doc.page_count)) * 0.7, 2), "message": f"Memproses halaman {page_no}"})

        raw = page.get_text("rawdict") or {}
        blocks = raw.get("blocks", [])
        blocks = sorted(blocks, key=lambda b: (b.get("bbox", [0, 0, 0, 0])[1], b.get("bbox", [0, 0, 0, 0])[0]))

        # Extract text blocks and figures
        for b in blocks:
            btype = b.get("type", 0)  # 0=text, 1=image
            bbox = _bbox_of_block(b)
            if btype == 0:
                lines = b.get("lines", [])
                spans_all: List[Dict[str, Any]] = []
                for ln in lines:
                    spans = ln.get("spans", [])
                    spans_all.extend(spans)
                text = _norm_spaces(" ".join([s.get("text", "") for s in spans_all]))
                if not text:
                    continue
                # Heading heuristic
                if _detect_headings_from_spans(spans_all):
                    heading_stack.append(text)
                    parts.append(f"\n\n## {text}\n\n")
                    units_meta.append(asdict(UnitMetadata(doc_id, page_no, "paragraph", text, bbox, panel=text)))
                else:
                    parts.append(text + "\n\n")
                    section = heading_stack[-1] if heading_stack else ""
                    units_meta.append(asdict(UnitMetadata(doc_id, page_no, "paragraph", section, bbox, panel=section)))

        figure_count = 0
        for block_no, block in enumerate(blocks):
            block_type = block.get('type', 'unknown')
            logger.info(f"[PDF++] Page {page_no} Block {block_no}: type={block_type}, bbox={block.get('bbox', 'no-bbox')}")
            
            if "type" in block and block["type"] == 1:  # Image block
                figure_count += 1
                bbox = fitz.Rect(block["bbox"])
                logger.info(f"[PDF++] Found image block on page {page_no}: bbox={bbox}")
                try:
                    pix = page.get_pixmap(clip=bbox, colorspace=fitz.csRGB)
                    img_path = figures_dir / f"figure_p{page_no}_{int(bbox.x0)}_{int(bbox.y0)}.png"
                    pix.save(str(img_path))
                    logger.info(f"[PDF++] Saved image: {img_path}")
                    
                    # Basic OCR label
                    alt_text = ""
                    if pytesseract:
                        logger.info(f"[PDF++] Running Tesseract OCR on {img_path}")
                        alt_text = _ocr_image_to_text(img_path)
                        logger.info(f"[PDF++] Tesseract result: {alt_text[:100]}..." if alt_text else "[PDF++] Tesseract: no text found")
                    else:
                        logger.warning("[PDF++] Tesseract not available")
                    
                    narrative = ""
                    if mode == "smart":
                        logger.info(f"[PDF++] Running Smart OCR (GPT-4o) on {img_path}")
                        narrative = _gpt4o_describe_image(img_path, api_key)
                        logger.info(f"[PDF++] GPT-4o result: {narrative[:100]}..." if narrative else "[PDF++] GPT-4o: no result")
                    
                    # Compose markdown for figure
                    rel = img_path.relative_to(doc_dir).as_posix()
                    web_path = f"/artefacts/{doc_id}/{rel}"
                    parts.append(f"![Gambar halaman {page_no}]({web_path})\n\n")
                    if narrative:
                        parts.append(f"**[Smart OCR]** {narrative}\n\n")
                    elif alt_text:
                        parts.append(f"**[Tesseract OCR]** _{alt_text}_\n\n")
                    section = heading_stack[-1] if heading_stack else ""
                    units_meta.append(asdict(UnitMetadata(doc_id, page_no, "figure", section, bbox)))
                except Exception as e:
                    logger.error(f"[PDF++] Error processing figure: {e}")
                    continue
        
        # Method 2: Alternative image detection using get_images()
        try:
            image_list = page.get_images()
            logger.info(f"[PDF++] Page {page_no}: get_images() found {len(image_list)} images")
            
            for img_index, img in enumerate(image_list):
                if figure_count >= 10:  # Limit to avoid too many images
                    break
                try:
                    # Extract image
                    xref = img[0]
                    pix = fitz.Pixmap(doc, xref)
                    
                    if pix.n - pix.alpha < 4:  # GRAY or RGB
                        img_path = figures_dir / f"figure_p{page_no}_img{img_index}.png"
                        pix.save(str(img_path))
                        logger.info(f"[PDF++] Extracted image via get_images(): {img_path}")
                        
                        # OCR processing
                        alt_text = ""
                        if pytesseract:
                            logger.info(f"[PDF++] Running Tesseract OCR on {img_path}")
                            alt_text = _ocr_image_to_text(img_path)
                            logger.info(f"[PDF++] Tesseract result: {alt_text[:100]}..." if alt_text else "[PDF++] Tesseract: no text found")
                        
                        narrative = ""
                        if mode == "smart":
                            logger.info(f"[PDF++] Running Smart OCR (GPT-4o) on {img_path}")
                            narrative = _gpt4o_describe_image(img_path, api_key)
                            logger.info(f"[PDF++] GPT-4o result: {narrative[:100]}..." if narrative else "[PDF++] GPT-4o: no result")
                        
                        # Add to markdown
                        rel = img_path.relative_to(doc_dir).as_posix()
                        web_path = f"/artefacts/{doc_id}/{rel}"
                        parts.append(f"![Gambar halaman {page_no} #{img_index}]({web_path})\n\n")
                        if narrative:
                            parts.append(f"**[Smart OCR]** {narrative}\n\n")
                        elif alt_text:
                            parts.append(f"**[Tesseract OCR]** _{alt_text}_\n\n")
                        
                        figure_count += 1
                        section = heading_stack[-1] if heading_stack else ""
                        # Use approximate bbox since get_images doesn't provide it
                        bbox = [0, 0, 100, 100]  
                        units_meta.append(asdict(UnitMetadata(doc_id, page_no, "figure", section, bbox)))
                    
                    pix = None  # Release memory
                except Exception as e:
                    logger.error(f"[PDF++] Error extracting image {img_index}: {e}")
                    continue
        except Exception as e:
            logger.error(f"[PDF++] Error in get_images() method: {e}")
        
        logger.info(f"[PDF++] Page {page_no}: Total images processed: {figure_count}")

        # Tables via Camelot on this page
        tables = _read_tables_with_camelot(pdf_path, page_no)
        for t_idx, t in enumerate(tables):
            md_table = t.get("markdown", "").strip()
            if not md_table:
                continue
            # Heuristic title: try to use nearest heading
            section = heading_stack[-1] if heading_stack else f"Tabel Halaman {page_no}"
            parts.append(f"### {section} — Tabel {t_idx+1}\n\n")
            parts.append(md_table + "\n\n")
            # Basic metadata for table (no bbox available from Camelot in this simple form)
            units_meta.append(asdict(UnitMetadata(doc_id, page_no, "table", section, (0, 0, 0, 0), panel=section)))

    # Finish
    markdown = "".join(parts).strip() + "\n"
    md_path = doc_dir / "markdown_v1.md"  # keep same name for downstream
    md_path.write_text(markdown, encoding="utf-8")

    # Save units metadata
    meta_path = meta_dir / "units_metadata.json"
    _save_json(meta_path, units_meta)

    ended = time.time()
    _save_json(progress_path, {"status": "complete", "percent": 1.0, "message": "Selesai"})

    doc.close()

    # Build artefact list (relative paths)
    artefacts = []
    for p in [*pages_dir.glob("*.png"), *figures_dir.glob("*.png")]:
        artefacts.append(str(p.relative_to(doc_dir)))
    result = {
        "document_id": doc_id,
        "output_dir": str(doc_dir),
        "markdown_path": str(md_path),
        "metadata_path": str(meta_path),
        "artefacts": artefacts,
    }
    logger.info(f"[PDF++] Completed for {doc_id}")
    return result


def run_conversion_and_persist(input_pdf_path: str, mode: str = "basic", doc_id: Optional[str] = None) -> Dict[str, Any]:
    """Entry point for API: runs conversion and returns summary dict."""
    return process_pdf_markdownpp(input_pdf_path, mode=mode, doc_id=doc_id)
