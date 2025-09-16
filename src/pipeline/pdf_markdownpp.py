import os
import io
import json
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import re
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
    
    # Configure Tesseract path for Windows installation
    if os.name == 'nt':  # Windows
        import platform
        possible_paths = [
            r'C:\Program Files\Tesseract-OCR\tesseract.exe',
            r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
            r'C:\Users\{}\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'.format(os.getenv('USERNAME', '')),
        ]
        
        for path in possible_paths:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                logger.info(f"[PDF++] Found Tesseract at: {path}")
                break
        else:
            # Try to find tesseract in PATH
            try:
                pytesseract.get_tesseract_version()
                logger.info("[PDF++] Using Tesseract from system PATH")
            except Exception:
                logger.warning("[PDF++] Tesseract not found in standard locations or PATH")
                
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
    return " ".join(text.split())


def _ocr_image_to_text(image_path: str) -> str:
    """Extract text from image using Tesseract OCR."""
    try:
        if not pytesseract or not Image:
            logger.debug(f"[PDF++] OCR unavailable (pytesseract: {pytesseract is not None}, PIL: {Image is not None})")
            return ""
        
        # Test if Tesseract executable is available
        try:
            pytesseract.get_tesseract_version()
        except Exception as exe_error:
            logger.warning(f"[PDF++] Tesseract executable not found: {exe_error}")
            logger.info("[PDF++] Install Tesseract to enable OCR: https://github.com/tesseract-ocr/tesseract")
            return ""
        
        # Open and process the image
        with Image.open(image_path) as img:
            # Convert to RGB if needed (handles various formats)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Try multiple OCR configurations
            configs = [
                r'--oem 3 --psm 6 -l eng',  # English only first
                r'--oem 3 --psm 6',         # Default language
                r'--oem 3 --psm 8',         # Single word mode
                r'--oem 3 --psm 11'         # Sparse text mode
            ]
            
            for config in configs:
                try:
                    text = pytesseract.image_to_string(img, config=config)
                    if text and text.strip():
                        # Clean up the text
                        text = text.strip()
                        # Remove excessive whitespace
                        text = re.sub(r'\s+', ' ', text)
                        # Remove common OCR artifacts
                        text = re.sub(r'[|\\/_]+', '', text)
                        logger.debug(f"[PDF++] OCR success with config: {config}")
                        return text
                except Exception:
                    continue
            
            return ""
            
    except Exception as e:
        logger.warning(f"[PDF++] OCR error for {image_path}: {e}")
        return ""


def _classify_block_content(block: Dict[str, Any]) -> str:
    """Classify block as 'paragraph', 'table-candidate', or 'figure'."""
    try:
        # Debug: Log block structure for diagnosis
        block_type = block.get("type", -1)
        
        # Only log debug for first few blocks to avoid spam
        if hasattr(_classify_block_content, '_debug_count'):
            _classify_block_content._debug_count += 1
        else:
            _classify_block_content._debug_count = 1
            
        if _classify_block_content._debug_count <= 5:
            logger.debug(f"Block type: {block_type}, keys: {list(block.keys())}")
        
        # Image blocks
        if block_type == 1:
            return "figure"
        
        # Extract ALL text from block regardless of structure
        text_parts = []
        
        # Method 1: Standard PyMuPDF structure (lines -> spans -> text)
        if "lines" in block:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if text:
                        text_parts.append(text)
        
        # Method 2: Direct text field
        elif "text" in block:
            text = block.get("text", "")
            if text:
                text_parts.append(text)
        
        # Method 3: Check for other text fields
        else:
            for key in block.keys():
                if isinstance(block[key], str) and block[key].strip():
                    text_parts.append(block[key])
        
        # Join all found text
        full_text = " ".join(text_parts).strip()
        
        # Debug: Log extracted text (limit debug output)
        if _classify_block_content._debug_count <= 10:  # More debug samples
            if full_text:
                logger.info(f"[PDF++] Block text found: '{full_text[:100]}...' (len={len(full_text)}, words={len(full_text.split())})")
            else:
                logger.info(f"[PDF++] No text in block with keys: {list(block.keys())}")
                # Debug: Log the structure to see what's inside
                if "lines" in block:
                    logger.info(f"[PDF++] Block has {len(block['lines'])} lines")
                    for i, line in enumerate(block.get('lines', [])[:2]):  # First 2 lines
                        spans = line.get('spans', [])
                        logger.info(f"[PDF++] Line {i}: {len(spans)} spans")
                        for j, span in enumerate(spans[:2]):  # First 2 spans per line
                            logger.info(f"[PDF++] Span {j}: '{span.get('text', 'NO_TEXT')}'")
        
        # If no text found, return unknown
        if not full_text:
            return "unknown"
        
        # Improved classification logic
        
        # Very short text (likely metadata or noise)
        if len(full_text) < 3:
            return "unknown"
        
        # Basic text classification
        words = full_text.split()
        word_count = len(words)
        
        # Calculate character ratios
        total_chars = len(full_text)
        digit_chars = sum(1 for c in full_text if c.isdigit())
        alpha_chars = sum(1 for c in full_text if c.isalpha())
        space_chars = sum(1 for c in full_text if c.isspace())
        punct_chars = sum(1 for c in full_text if c in '.,;:!?()[]{}"\'-')
        
        digit_ratio = digit_chars / total_chars if total_chars > 0 else 0
        alpha_ratio = alpha_chars / total_chars if total_chars > 0 else 0
        
        # Enhanced table detection:
        # 1. High digit ratio with few words (numeric data)
        if digit_ratio > 0.4 and word_count <= 3 and total_chars < 20:
            return "table-candidate"
        
        # 2. Very high digit ratio (financial/numeric tables)
        if digit_ratio > 0.6:
            return "table-candidate"
        
        # 3. Common table patterns (currency, percentages, dates)
        table_patterns = [r'\d+[.,]\d+%', r'\d+[.,]\d+', r'\$\d+', r'Rp\d+', r'\d{1,2}/\d{1,2}/\d{2,4}']
        table_match_count = sum(1 for pattern in table_patterns if re.search(pattern, full_text))
        if table_match_count >= 2 or (table_match_count >= 1 and word_count <= 5):
            return "table-candidate"
        
        # 4. Short text with mostly symbols/numbers (table headers/values)
        if word_count <= 2 and (digit_ratio > 0.3 or punct_chars > alpha_chars):
            return "table-candidate"
        
        # Otherwise, if we have reasonable text content, classify as paragraph
        # Be much more generous with paragraph classification
        if word_count >= 1 and alpha_ratio > 0.2:  # Lowered thresholds
            return "paragraph"
        
        # Fallback for edge cases - any substantial text
        if len(full_text) >= 5:  # Lowered from 10
            return "paragraph"
        
        # Even more generous: if we extracted any text, it's probably meaningful
        if full_text and len(full_text.strip()) > 0:
            return "paragraph"
        
        return "unknown"
    
    except Exception as e:
        logger.error(f"Classification error for block: {e}")
        logger.error(f"Block structure: {block}")
        return "unknown"


def _join_text_spans_properly(block: Dict[str, Any]) -> str:
    """Join spans from a text block, fixing character separation issues."""
    all_lines = []
    
    for line in block.get("lines", []):
        spans = line.get("spans", [])
        if not spans:
            continue
        
        # Collect all span texts for this line
        line_texts = []
        for span in spans:
            text = span.get("text", "")
            if text:  # Keep even whitespace-only text
                line_texts.append(text)
        
        if line_texts:
            # Join spans intelligently
            line_text = ""
            for i, text in enumerate(line_texts):
                if i == 0:
                    line_text = text
                else:
                    # Check if we need a space between spans
                    # Don't add space if previous ends with space or current starts with space
                    if line_text and not line_text[-1].isspace() and text and not text[0].isspace():
                        # Check for artificially separated characters
                        # Single char to single char usually means separated
                        if (len(line_text.strip()) == 1 and len(text.strip()) == 1 and 
                            line_text.strip().isalnum() and text.strip().isalnum()):
                            line_text += text  # No space
                        else:
                            line_text += " " + text
                    else:
                        line_text += text
            
            all_lines.append(line_text.strip())
    
    # Join all lines
    result = " ".join(all_lines)
    
    # Fix common separation patterns
    # Fix separated single letters: "K a m i s" -> "Kamis"
    result = re.sub(r'\b([A-Z])\s+(?=[a-z])', r'\1', result)
    
    # Fix separated date components: "1 3" -> "13", "2 0 2 5" -> "2025"
    result = re.sub(r'\b(\d)\s+(\d)\s+(\d)\s+(\d)\b', r'\1\2\3\4', result)
    result = re.sub(r'\b(\d)\s+(\d)\b', r'\1\2', result)
    
    # Fix month names that might be separated
    months = ['Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni', 
              'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember']
    for month in months:
        # Create pattern for separated month name
        separated = ' '.join(month)
        if separated in result:
            result = result.replace(separated, month)
    
    # Fix common Indonesian words that might be separated
    common_words = ['Kamis', 'Senin', 'Selasa', 'Rabu', 'Jumat', 'Sabtu', 'Minggu']
    for word in common_words:
        separated = ' '.join(word)
        if separated in result:
            result = result.replace(separated, word)
    
    return _norm_spaces(result)


def _is_bbox_overlap(bbox1: List[float], bbox2: List[float], threshold: float = 0.1) -> bool:
    """Check if two bounding boxes overlap significantly."""
    x1_min, y1_min, x1_max, y1_max = bbox1
    x2_min, y2_min, x2_max, y2_max = bbox2
    
    # Calculate intersection
    x_overlap = max(0, min(x1_max, x2_max) - max(x1_min, x2_min))
    y_overlap = max(0, min(y1_max, y2_max) - max(y1_min, y2_min))
    
    if x_overlap == 0 or y_overlap == 0:
        return False
    
    intersection_area = x_overlap * y_overlap
    area1 = (x1_max - x1_min) * (y1_max - y1_min)
    area2 = (x2_max - x2_min) * (y2_max - y2_min)
    
    min_area = min(area1, area2)
    overlap_ratio = intersection_area / min_area if min_area > 0 else 0
    
    return overlap_ratio > threshold


def _is_plausible_table_bbox(page_rect: "fitz.Rect", bbox: List[float],
                             min_area_ratio: float = 0.01,
                             max_area_ratio: float = 0.60) -> bool:
    """Heuristic check to ensure a table bbox is not implausibly large or tiny.

    Prevents masking entire pages when Camelot returns overly large boxes.
    """
    try:
        px0, py0, px1, py1 = page_rect.x0, page_rect.y0, page_rect.x1, page_rect.y1
        pw = max(1.0, px1 - px0)
        ph = max(1.0, py1 - py0)
        page_area = pw * ph
        bx0, by0, bx1, by1 = bbox
        bw = max(0.0, bx1 - bx0)
        bh = max(0.0, by1 - by0)
        box_area = bw * bh
        if box_area <= 0:
            return False
        ratio = box_area / page_area
        return (min_area_ratio <= ratio <= max_area_ratio)
    except Exception:
        return False


def _filter_plausible_table_bboxes(page_rect: "fitz.Rect",
                                   bboxes: List[List[float]]) -> List[List[float]]:
    """Filter Camelot bboxes by plausibility to avoid over-masking text."""
    return [bb for bb in bboxes if _is_plausible_table_bbox(page_rect, bb)]


def _split_merged_table_cells(text: str) -> str:
    """Split merged table cell values that should be in separate columns."""
    original = text
    
    # Pattern 1: Merged percentages like "3.00%0.50%" -> "3.00% | 0.50%"
    text = re.sub(r'(\d+\.\d+%)(\d+\.\d+%)', r'\1 | \2', text)
    
    # Pattern 2: Merged numbers with % like "3.000.50" -> "3.00 | 0.50"
    text = re.sub(r'(\d+\.\d{2})(\d+\.\d{2})', r'\1 | \2', text)
    
    # Pattern 3: Values with brackets like "44593.65 44368.5(0.50)" -> "44593.65 | 44368.5 | (0.50)"
    text = re.sub(r'(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\(([\d\.\-]+)\)', r'\1 | \2 | (\3)', text)
    
    # Pattern 4: Two numbers separated by space (likely different columns)
    if '|' not in text:  # Only if not already split
        # Check for pattern like "1636 1638" (two 4-digit numbers)
        text = re.sub(r'\b(\d{4,})\s+(\d{4,})\b', r'\1 | \2', text)
        
        # Check for decimal numbers separated by space
        text = re.sub(r'\b(\d+\.\d+)\s+(\d+\.\d+)\b', r'\1 | \2', text)
    
    # Pattern 5: Fix merged text+number like "TRIES(YoY)" -> "TRIES | (YoY)"
    text = re.sub(r'([A-Z]+)\(([^)]+)\)', r'\1 | (\2)', text)
    
    if text != original:
        logger.debug(f"Cell split: '{original}' -> '{text}'")
    
    return text

def _detect_panel_structure(df: Any) -> bool:
    """Detect if DataFrame represents a panel-style financial table."""
    try:
        # Check first row for panel indicators
        first_row = ' '.join(str(val) for val in df.iloc[0] if val)
        panel_keywords = ['INTEREST RATES', 'COUNTRIES', 'BONDS', 'INDEXES', 'FOREX', 'Economic Data']
        
        for keyword in panel_keywords:
            if keyword.upper() in first_row.upper():
                return True
        
        # Check for percentage and number patterns
        all_text = ' '.join(str(val) for row in df.values for val in row if val)
        has_percentages = '%' in all_text
        has_rates = bool(re.search(r'\b\d+\.\d{2,}\b', all_text))
        
        return has_percentages and has_rates
    except:
        return False

def _process_panel_table(df: Any) -> Any:
    """Process panel-style financial tables with special handling."""
    try:
        import pandas as pd
        
        # Try to identify and separate merged columns
        new_rows = []
        
        for _, row in df.iterrows():
            new_row = []
            for cell in row:
                cell_str = str(cell)
                # Apply aggressive splitting for panel data
                split_cell = _split_merged_table_cells(cell_str)
                
                # If cell was split, we might need to expand columns
                if '|' in split_cell:
                    parts = [p.strip() for p in split_cell.split('|')]
                    new_row.extend(parts)
                else:
                    new_row.append(cell_str)
            
            new_rows.append(new_row)
        
        # Create new DataFrame with expanded columns
        max_cols = max(len(row) for row in new_rows)
        
        # Pad rows to have same number of columns
        for row in new_rows:
            while len(row) < max_cols:
                row.append('')
        
        # Create new DataFrame
        new_df = pd.DataFrame(new_rows)
        
        # Clean up empty columns
        new_df = new_df.loc[:, (new_df != '').any(axis=0)]
        
        return new_df
    except Exception as e:
        logger.debug(f"Panel table processing failed: {e}")
        return df

def _markdown_table_from_df(df: Any) -> str:
    """Convert a DataFrame to Markdown table format with schema-aware recovery."""
    try:
        import pandas as pd
        
        if not isinstance(df, pd.DataFrame):
            return ""
        
        if df.empty:
            return ""
        
        # Clean and process the DataFrame
        df = df.fillna("")
        df = df.astype(str)
        
        # Schema-aware processing
        # First, try to detect if this is a panel-style table
        is_panel = _detect_panel_structure(df)
        
        if is_panel:
            df = _process_panel_table(df)
        else:
            # Apply general cell splitting for all cells
            for i in range(len(df)):
                for j in range(len(df.columns)):
                    cell_value = str(df.iloc[i, j])
                    split_values = _split_merged_table_cells(cell_value)
                    if '|' in split_values:  # Multiple values detected
                        # For now, keep first value in cell, log the issue
                        parts = split_values.split('|')
                        df.iloc[i, j] = parts[0]
                        if len(parts) > 1:
                            logger.debug(f"Split cell at ({i},{j}): {cell_value} -> {parts}")
        
        # Convert to markdown
        md_table = df.to_markdown(index=False)
        return md_table if md_table else ""
    except Exception as e:
        logger.debug(f"DataFrame to markdown conversion failed: {e}")
        return ""


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
                # Extract actual bbox from Camelot table object
                bbox = [0, 0, 100, 100]  # Default fallback
                try:
                    if hasattr(t, '_bbox'):
                        bbox = list(t._bbox)
                    elif hasattr(t, 'bbox'):
                        bbox = list(t.bbox)
                    elif hasattr(t, 'cells') and t.cells:
                        # Calculate from cell coordinates
                        all_x = [cell.x1 for cell in t.cells] + [cell.x2 for cell in t.cells]
                        all_y = [cell.y1 for cell in t.cells] + [cell.y2 for cell in t.cells]
                        if all_x and all_y:
                            bbox = [min(all_x), min(all_y), max(all_x), max(all_y)]
                except Exception:
                    pass
                
                results.append({
                    "flavor": flavor,
                    "index": idx,
                    "df": df,  # will not be JSON-serializable; drop later
                    "markdown": md,
                    "bbox": bbox,
                })
            except Exception:
                continue
        if results:
            break
    return results


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
    all_content = []

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

    # STAGED SYNTHESIS: Collect content by type first, then synthesize
    all_paragraphs = []
    all_tables = []
    all_figures = []
    
    for i in range(doc.page_count):
        page = doc.load_page(i)
        page_no = i + 1
        _save_json(progress_path, {"status": "running", "percent": round((i / max(1, doc.page_count)) * 0.7, 2), "message": f"Memproses halaman {page_no}"})

        raw = page.get_text("rawdict") or {}
        blocks = raw.get("blocks", [])
        blocks = sorted(blocks, key=lambda b: (b.get("bbox", [0, 0, 0, 0])[1], b.get("bbox", [0, 0, 0, 0])[0]))

        # STEP 1: Extract tables first to get their bboxes for masking
        logger.info(f"[PDF++] Page {page_no}: Extracting tables first...")
        page_tables = _read_tables_with_camelot(pdf_path, page_no)
        table_bboxes = []
        
        for t_idx, table_data in enumerate(page_tables):
            bbox = table_data.get("bbox", [0, 0, 100, 100])
            table_bboxes.append(bbox)
            
            # Process table with schema-aware recovery
            df = table_data.get("df")
            if df is not None:
                md_table = _markdown_table_from_df(df)
                if md_table.strip():
                    section = heading_stack[-1] if heading_stack else f"Tabel Halaman {page_no}"
                    all_tables.append({
                        "page": page_no,
                        "title": f"{section} — Tabel {t_idx+1}",
                        "content": md_table,
                        "bbox": bbox,
                        "section": section
                    })
                    units_meta.append(asdict(UnitMetadata(doc_id, page_no, "table", section, bbox)))

        # Filter table bboxes by plausibility to avoid masking entire pages
        table_bboxes = _filter_plausible_table_bboxes(page.rect, table_bboxes)
        logger.info(f"[PDF++] Page {page_no}: Found {len(page_tables)} tables; using {len(table_bboxes)} plausible bboxes for masking")

        # STEP 2: TEXT-FIRST APPROACH - Extract ALL text blocks first
        paragraph_count = 0
        table_candidate_count = 0
        figure_count = 0
        
        # Debug: Enable detailed logging for first few blocks (without changing logger level)
        debug_blocks = min(3, len(blocks))
        logger.info(f"[PDF++] Page {page_no}: Processing {len(blocks)} blocks (debugging first {debug_blocks})")
        
        added_paragraphs_this_page = 0
        for block_no, block in enumerate(blocks):
            block_type = _classify_block_content(block)
            bbox = list(_bbox_of_block(block))
            if block_no < debug_blocks:
                try:
                    logger.debug(f"[PDF++] DEBUG Page {page_no} Block {block_no} keys: {list(block.keys())}")
                except Exception:
                    pass
            
            # Count block types for logging
            if block_type == "paragraph":
                paragraph_count += 1
            elif block_type == "table-candidate":
                table_candidate_count += 1
            elif block_type == "figure":
                figure_count += 1
            
            # Log all classifications for debugging
            logger.info(f"[PDF++] Page {page_no} Block {block_no}: '{block_type}' (bbox: {bbox})")
            
            # TEXT-FIRST: Process all text blocks regardless of table overlap
            # Table masking disabled to ensure all text is captured
            
            if block_type == "paragraph":
                # Process as clean paragraph
                text = _join_text_spans_properly(block)
                if text and len(text.strip()) > 10:  # Minimum length for meaningful paragraph
                    # Check for heading
                    spans_all = []
                    for line in block.get("lines", []):
                        spans_all.extend(line.get("spans", []))
                    
                    is_heading = _detect_headings_from_spans(spans_all)
                    
                    if is_heading:
                        heading_stack.append(text)
                        all_paragraphs.append({
                            "page": page_no,
                            "content": f"## {text}",
                            "bbox": bbox,
                            "is_heading": True
                        })
                    else:
                        all_paragraphs.append({
                            "page": page_no,
                            "content": text,
                            "bbox": bbox,
                            "is_heading": False
                        })
                    
                    # Add to metadata
                    section = heading_stack[-1] if heading_stack else "Main"
                    units_meta.append(asdict(UnitMetadata(
                        doc_id, page_no, "paragraph", section, bbox
                    )))
            
            elif block_type == "table-candidate" and not overlaps_table:
                # These are small text fragments that look like table data
                # but are outside the main table areas - might be labels or isolated values
                text = _join_text_spans_properly(block)
                if text and len(text.strip()) > 1:
                    # Check if it's actually a small heading or label
                    if text.strip().isalpha() or (len(text.split()) <= 2 and any(c.isalpha() for c in text)):
                        # Treat as a small paragraph/label
                        all_paragraphs.append({
                            "page": page_no,
                            "content": text,
                            "bbox": bbox,
                            "is_heading": False
                        })
                    # Otherwise ignore - it's likely a stray table fragment
            
            elif block_type == "figure":
                # Process as figure
                all_figures.append({
                    "page": page_no,
                    "block": block,
                    "bbox": bbox
                })
                
                # Add to metadata
                section = heading_stack[-1] if heading_stack else "Main"
                units_meta.append(asdict(UnitMetadata(
                    doc_id, page_no, "figure", section, bbox
                )))
        
        # Fallback: If still no paragraphs, force extract from unknown blocks
        if added_paragraphs_this_page == 0:
            logger.warning(f"[PDF++] Page {page_no}: 0 paragraphs found — forcing extraction from unknown blocks")
            for block_no, block in enumerate(blocks):
                # Try to extract text from ANY block, regardless of classification
                text = _join_text_spans_properly(block)
                if text and len(text.strip()) > 5:
                    bbox = list(_bbox_of_block(block))
                    logger.info(f"[PDF++] Force-extracted text: '{text[:50]}...'")
                    
                    all_paragraphs.append({
                        "page": page_no,
                        "content": text,
                        "bbox": bbox,
                        "is_heading": False
                    })
                    
                    section = heading_stack[-1] if heading_stack else "Main"
                    units_meta.append(asdict(UnitMetadata(
                        doc_id, page_no, "paragraph", section, bbox
                    )))
                    added_paragraphs_this_page += 1

        # Final page-level fallback: use PyMuPDF basic text extraction if still nothing
        if added_paragraphs_this_page == 0:
            try:
                basic_text = page.get_text("text") or ""
            except Exception:
                basic_text = ""
            if basic_text.strip():
                bbox_full = [float(page.rect.x0), float(page.rect.y0), float(page.rect.x1), float(page.rect.y1)]
                normalized = _norm_spaces(basic_text)
                all_paragraphs.append({
                    "page": page_no,
                    "content": normalized,
                    "bbox": bbox_full,
                    "is_heading": False
                })
                section = heading_stack[-1] if heading_stack else "Main"
                units_meta.append(asdict(UnitMetadata(
                    doc_id, page_no, "paragraph", section, bbox_full
                )))
                added_paragraphs_this_page += 1
                logger.warning(f"[PDF++] Page {page_no}: used basic text fallback (PyMuPDF)")

        # Log summary for this page
        logger.info(f"[PDF++] Page {page_no} block classification summary: "
                   f"{paragraph_count} paragraphs, {table_candidate_count} table-candidates, "
                   f"{figure_count} figures")
        
        # Process extracted figures (from PyMuPDF image blocks)
        # Create a copy to avoid modification during iteration
        figures_to_process = [fig for fig in all_figures if fig["page"] == page_no]
        
        for fig_data in figures_to_process:
            # Safely get block reference
            block = fig_data.get("block")
            if not block:
                logger.warning(f"[PDF++] Figure missing block reference: {fig_data}")
                continue
            
            figure_bbox = fitz.Rect(fig_data["bbox"])
            
            try:
                pix = page.get_pixmap(clip=figure_bbox, colorspace=fitz.csRGB)
                img_path = figures_dir / f"figure_p{page_no}_{int(figure_bbox.x0)}_{int(figure_bbox.y0)}.png"
                pix.save(str(img_path))
                logger.info(f"[PDF++] Saved image: {img_path}")
                
                # OCR processing (graceful degradation)
                alt_text = ""
                if pytesseract:
                    logger.info(f"[PDF++] Running Tesseract OCR on {img_path}")
                    alt_text = _ocr_image_to_text(img_path)
                    if alt_text:
                        logger.info(f"[PDF++] Tesseract result: {alt_text[:100]}...")
                    else:
                        logger.debug("[PDF++] Tesseract: no text found")
                else:
                    logger.debug("[PDF++] OCR skipped (Tesseract unavailable)")
                
                narrative = ""
                if mode == "smart":
                    logger.info(f"[PDF++] Running Smart OCR (GPT-4o) on {img_path}")
                    narrative = _gpt4o_describe_image(img_path, api_key)
                    logger.info(f"[PDF++] GPT-4o result: {narrative[:100]}..." if narrative else "[PDF++] GPT-4o: no result")
                
                # Store figure data
                rel = img_path.relative_to(doc_dir).as_posix()
                web_path = f"/artefacts/{doc_id}/{rel}"
                section = heading_stack[-1] if heading_stack else ""
                
                description = ""
                if narrative:
                    description = f"**[Smart OCR]** {narrative}"
                elif alt_text:
                    description = f"**[Tesseract OCR]** _{alt_text}_"
                
                # Add to content for markdown output
                all_content.append(f"![Figure]({web_path})")
                if description:
                    all_content.append(f"_{description}_")
                all_content.append("")  # Empty line
                
                units_meta.append(asdict(UnitMetadata(doc_id, page_no, "figure", section, list(figure_bbox))))
                
            except Exception as e:
                logger.error(f"[PDF++] Error processing figure: {e}")
                continue
            

        # Log page statistics
        page_para_count = len([p for p in all_paragraphs if p.get('page') == page_no])
        page_table_count = len([t for t in all_tables if t.get('page') == page_no])
        page_fig_count = len([f for f in all_figures if f.get('page') == page_no])
        logger.info(f"[PDF++] Page {page_no}: Processed {page_para_count} paragraphs, {page_table_count} tables, {page_fig_count} figures")

    # STEP 3: STAGED SYNTHESIS - Assemble final markdown in logical order
    logger.info("[PDF++] Starting staged synthesis...")
    
    # Sort content by page and reading order
    all_paragraphs.sort(key=lambda x: (x["page"], x["bbox"][1], x["bbox"][0]))
    all_tables.sort(key=lambda x: (x["page"], x["bbox"][1], x["bbox"][0]))  
    all_figures.sort(key=lambda x: (x["page"], x["bbox"][1], x["bbox"][0]))
    
    # Initialize parts list for markdown assembly
    parts = []
    
    # Output: Paragraphs first (narrative flow)
    for para in all_paragraphs:
        if para.get("is_heading", False):
            parts.append(f"\n\n{para['content']}\n\n")
        else:
            parts.append(f"{para['content']}\n\n")
    
    # Output: Tables second (structured data)
    for table in all_tables:
        parts.append(f"### {table['title']}\n\n")
        parts.append(f"{table['content']}\n\n")
    
    # Output: Figures third (visual content)
    for figure in all_figures:
        if 'path' in figure:
            parts.append(f"![Gambar halaman {figure['page']}]({figure['path']})\n\n")
            if figure.get("description"):
                parts.append(f"{figure['description']}\n\n")

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
