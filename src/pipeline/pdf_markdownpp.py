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
    import pdfplumber  # type: ignore
except Exception:
    pdfplumber = None

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
    column: Optional[str] = None  # 'left', 'right', 'center', 'full'
    unit_id: Optional[str] = None  # stable unit identifier for RAG
    panel: Optional[str] = None
    row_label: Optional[str] = None
    col_label: Optional[str] = None
    unit: Optional[str] = None
    row_idx: Optional[int] = None
    col_idx: Optional[int] = None
    source: Optional[str] = None  # 'pymupdf', 'camelot', 'tesseract'


def _save_json(path: Path, obj: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _render_page_png(page: fitz.Page, out_path: Path, zoom: float = 2.0):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    pix.save(out_path)


def _detect_column_layout(page_dict: Dict[str, Any], page_rect: fitz.Rect) -> Dict[str, Any]:
    """Analyze page layout to detect column structure using PyMuPDF dict."""
    try:
        page_width = page_rect.width
        blocks = page_dict.get("blocks", [])
        
        # Collect text blocks with their X positions
        text_blocks = []
        for block in blocks:
            if block.get("type") == 0:  # Text block
                bbox = block.get("bbox", [0, 0, 0, 0])
                x0, y0, x1, y1 = bbox
                text_blocks.append({
                    "bbox": bbox,
                    "x_center": (x0 + x1) / 2,
                    "x0": x0,
                    "x1": x1,
                    "block": block
                })
        
        if not text_blocks:
            return {"layout_type": "single", "columns": {}}
        
        # Analyze X positions to detect columns
        x_centers = [b["x_center"] for b in text_blocks]
        page_mid = page_width / 2
        
        # Simple two-column detection
        left_blocks = [b for b in text_blocks if b["x_center"] < page_mid]
        right_blocks = [b for b in text_blocks if b["x_center"] >= page_mid]
        
        # Determine layout type
        if len(left_blocks) > 0 and len(right_blocks) > 0:
            # Check if right blocks are mainly in right half
            right_x_avg = sum(b["x_center"] for b in right_blocks) / len(right_blocks)
            if right_x_avg > page_width * 0.6:
                layout_type = "two_column"
            else:
                layout_type = "single"
        else:
            layout_type = "single"
        
        return {
            "layout_type": layout_type,
            "page_width": page_width,
            "page_mid": page_mid,
            "left_blocks": left_blocks,
            "right_blocks": right_blocks,
            "columns": {
                "left": {"x_range": [0, page_mid], "count": len(left_blocks)},
                "right": {"x_range": [page_mid, page_width], "count": len(right_blocks)}
            }
        }
    except Exception as e:
        logger.debug(f"Column layout detection failed: {e}")
        return {"layout_type": "single", "columns": {}}


def _get_block_column(block_bbox: Tuple[float, float, float, float], layout_info: Dict[str, Any]) -> str:
    """Determine which column a block belongs to based on layout analysis."""
    try:
        if layout_info.get("layout_type") != "two_column":
            return "full"
        
        x0, y0, x1, y1 = block_bbox
        x_center = (x0 + x1) / 2
        page_mid = layout_info.get("page_mid", 0)
        
        if x_center < page_mid:
            return "left"
        else:
            return "right"
    except Exception:
        return "full"


def _detect_headings_from_spans(spans: List[Dict[str, Any]]) -> Tuple[bool, int]:
    """Enhanced heading detection with level information."""
    try:
        sizes = [s.get("size", 0) for s in spans if isinstance(s, dict)]
        flags = [s.get("flags", 0) for s in spans if isinstance(s, dict)]
        max_size = max(sizes) if sizes else 0
        
        text = " ".join([s.get("text", "") for s in spans])
        text_clean = text.strip()
        
        # Various heading indicators
        is_caps = text_clean and text_clean.upper() == text_clean and len(text_clean) < 120
        is_large = max_size >= 14
        is_bold = any(flag & 2**4 for flag in flags)  # Bold flag
        
        # Determine heading level
        if max_size >= 18 or (is_caps and is_bold):
            return True, 1  # H1
        elif max_size >= 14 or is_caps:
            return True, 2  # H2
        elif is_bold and max_size >= 12:
            return True, 3  # H3
        
        return False, 0
    except Exception:
        return False, 0


def _bbox_of_block(block: Dict[str, Any]) -> Tuple[float, float, float, float]:
    b = block.get("bbox") or [0, 0, 0, 0]
    return float(b[0]), float(b[1]), float(b[2]), float(b[3])


def _norm_spaces(text: str) -> str:
    return " ".join(text.split())


def _detect_page_content_type(page_dict: Dict[str, Any]) -> str:
    """Detect if page is full-image, mixed content, or text-only."""
    try:
        blocks = page_dict.get("blocks", [])
        text_blocks = [b for b in blocks if b.get("type") == 0]  # Text blocks
        image_blocks = [b for b in blocks if b.get("type") == 1]  # Image blocks
        
        total_text_area = 0
        total_image_area = 0
        
        for block in text_blocks:
            bbox = block.get("bbox", [0, 0, 0, 0])
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            total_text_area += area
        
        for block in image_blocks:
            bbox = block.get("bbox", [0, 0, 0, 0])
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            total_image_area += area
        
        total_area = total_text_area + total_image_area
        
        if total_area == 0:
            return "empty"
        elif total_text_area == 0:
            return "full_image"
        elif total_image_area / total_area > 0.7:
            return "image_heavy"
        elif total_text_area / total_area > 0.8:
            return "text_heavy"
        else:
            return "mixed"
    except Exception:
        return "unknown"


def _ocr_image_to_text(image_path: str, page_type: str = "mixed", bbox_size: Optional[Tuple[float, float]] = None) -> str:
    """Enhanced OCR with adaptive configuration based on content type."""
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
        
        with Image.open(image_path) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Adaptive OCR configurations based on content type and size
            configs = []
            
            if page_type == "full_image":
                # Full page image - use document layout analysis
                configs = [
                    r'--oem 3 --psm 3 -l ind+eng',   # Auto page layout with Indonesian+English
                    r'--oem 3 --psm 1 -l ind+eng',   # Full page with OSD
                    r'--oem 3 --psm 6 -l ind+eng',   # Uniform text block
                ]
            elif bbox_size and (bbox_size[0] * bbox_size[1]) < 10000:  # Small figures
                # Small image - likely single element
                configs = [
                    r'--oem 3 --psm 8 -l ind+eng',   # Single word
                    r'--oem 3 --psm 7 -l ind+eng',   # Single text line
                    r'--oem 3 --psm 6 -l ind+eng',   # Uniform block
                ]
            else:
                # Mixed or medium content
                configs = [
                    r'--oem 3 --psm 6 -l ind+eng',   # Uniform text block (primary)
                    r'--oem 3 --psm 3 -l ind+eng',   # Auto page layout
                    r'--oem 3 --psm 11 -l ind+eng',  # Sparse text (fallback)
                ]
            
            # Try configurations in order of preference
            for config in configs:
                try:
                    text = pytesseract.image_to_string(img, config=config)
                    if text and text.strip():
                        # Clean up the text
                        text = text.strip()
                        text = re.sub(r'\s+', ' ', text)  # Normalize whitespace
                        
                        # Filter out noise (very short or mostly symbols)
                        if len(text) >= 3 and any(c.isalnum() for c in text):
                            logger.info(f"[PDF++] OCR success with config: {config}")
                            return text
                except Exception as config_error:
                    logger.debug(f"[PDF++] OCR config failed ({config}): {config_error}")
                    continue
            
            logger.debug(f"[PDF++] All OCR configs failed for: {image_path}")
            return ""
    except Exception as e:
        logger.error(f"[PDF++] OCR processing failed: {e}")
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


def _evaluate_table_quality(df: Any, source: str = "unknown") -> float:
    """Evaluate table quality based on parsing metrics."""
    try:
        import pandas as pd
        if not isinstance(df, pd.DataFrame) or df.empty:
            return 0.0
        
        # Basic structure score
        rows, cols = df.shape
        if rows < 2 or cols < 2:
            return 0.0
        
        # Content density score
        non_empty_cells = df.astype(str).apply(lambda x: x.str.strip() != '').sum().sum()
        total_cells = rows * cols
        density = non_empty_cells / total_cells if total_cells > 0 else 0
        
        # Whitespace penalty (high whitespace = poor extraction)
        all_text = ' '.join(df.astype(str).values.flatten())
        whitespace_ratio = (all_text.count(' ') + all_text.count('\n')) / max(len(all_text), 1)
        whitespace_penalty = max(0, whitespace_ratio - 0.3)  # Penalize if >30% whitespace
        
        # Final score
        base_score = density * 0.7 + (1 - whitespace_penalty) * 0.3
        
        # Source bonus/penalty
        if source == "camelot_lattice":
            base_score *= 1.1  # Slight preference for lattice
        elif source == "pdfplumber":
            base_score *= 0.9   # Slight penalty as fallback
        
        return min(1.0, base_score)
    except Exception:
        return 0.0


def _read_tables_with_pdfplumber(pdf_path: str, page_no: int) -> List[Dict[str, Any]]:
    """Fallback table extraction using pdfplumber."""
    results: List[Dict[str, Any]] = []
    if pdfplumber is None:
        return results
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_no <= len(pdf.pages):
                page = pdf.pages[page_no - 1]  # pdfplumber uses 0-based indexing
                
                # Extract tables
                tables = page.extract_tables()
                for idx, table_data in enumerate(tables):
                    if not table_data or len(table_data) < 2:
                        continue
                    
                    # Convert to DataFrame
                    import pandas as pd
                    df = pd.DataFrame(table_data[1:], columns=table_data[0])  # First row as header
                    
                    if df.empty or df.shape[0] < 1 or df.shape[1] < 2:
                        continue
                    
                    # Get table bbox (approximate)
                    page_height = page.height
                    page_width = page.width
                    bbox = [0, 0, page_width, page_height]  # Default to full page
                    
                    # Try to get more precise bbox if available
                    try:
                        table_bbox = page.find_tables()[idx].bbox if page.find_tables() else None
                        if table_bbox:
                            bbox = list(table_bbox)
                    except (IndexError, AttributeError):
                        pass
                    
                    results.append({
                        "source": "pdfplumber",
                        "index": idx,
                        "df": df,
                        "bbox": bbox,
                    })
    except Exception as e:
        logger.debug(f"pdfplumber table extraction failed for page {page_no}: {e}")
    
    return results


def _read_tables_enhanced(pdf_path: str, page_no: int) -> List[Dict[str, Any]]:
    """Enhanced table extraction combining Camelot and pdfplumber with quality evaluation."""
    results: List[Dict[str, Any]] = []
    page_str = str(page_no)
    
    # Step 1: Try Camelot with both flavors
    camelot_results = []
    if camelot is not None:
        flavors = ["lattice", "stream"]
        for flavor in flavors:
            try:
                tables = camelot.read_pdf(pdf_path, pages=page_str, flavor=flavor, strip_text="\n")
                for idx, t in enumerate(getattr(tables, "_tables", []) or tables):
                    try:
                        df = t.df
                        if getattr(df, "empty", True) or df.shape[0] < 2 or df.shape[1] < 2:
                            continue
                        
                        # Extract bbox
                        bbox = [0, 0, 100, 100]  # Default fallback
                        try:
                            if hasattr(t, '_bbox'):
                                bbox = list(t._bbox)
                            elif hasattr(t, 'bbox'):
                                bbox = list(t.bbox)
                            elif hasattr(t, 'cells') and t.cells:
                                all_x = [cell.x1 for cell in t.cells] + [cell.x2 for cell in t.cells]
                                all_y = [cell.y1 for cell in t.cells] + [cell.y2 for cell in t.cells]
                                if all_x and all_y:
                                    bbox = [min(all_x), min(all_y), max(all_x), max(all_y)]
                        except Exception:
                            pass
                        
                        # Evaluate quality
                        quality = _evaluate_table_quality(df, f"camelot_{flavor}")
                        
                        camelot_results.append({
                            "source": f"camelot_{flavor}",
                            "flavor": flavor,
                            "index": idx,
                            "df": df,
                            "bbox": bbox,
                            "quality": quality,
                        })
                    except Exception:
                        continue
            except Exception:
                continue
    
    # Step 2: Try pdfplumber as fallback if Camelot results are poor
    pdfplumber_results = []
    if not camelot_results or max([r.get("quality", 0) for r in camelot_results]) < 0.3:
        logger.info(f"[PDF++] Page {page_no}: Camelot results poor, trying pdfplumber fallback")
        pdfplumber_results = _read_tables_with_pdfplumber(pdf_path, page_no)
        
        # Evaluate pdfplumber results
        for result in pdfplumber_results:
            result["quality"] = _evaluate_table_quality(result["df"], "pdfplumber")
    
    # Step 3: Select best results
    all_candidates = camelot_results + pdfplumber_results
    
    # Group by approximate position and select best quality
    position_groups = {}
    for candidate in all_candidates:
        bbox = candidate["bbox"]
        # Create position key based on approximate bbox
        pos_key = (round(bbox[0] / 50) * 50, round(bbox[1] / 50) * 50)  # Group by 50pt grid
        
        if pos_key not in position_groups or candidate.get("quality", 0) > position_groups[pos_key].get("quality", 0):
            position_groups[pos_key] = candidate
    
    # Process best candidates
    for candidate in position_groups.values():
        if candidate.get("quality", 0) < 0.1:  # Skip very poor quality
            continue
        
        df = candidate["df"]
        md = _markdown_table_from_df(df)
        if md.strip():
            results.append({
                "source": candidate["source"],
                "flavor": candidate.get("flavor", "unknown"),
                "index": candidate["index"],
                "df": df,
                "markdown": md,
                "bbox": candidate["bbox"],
                "quality": candidate.get("quality", 0)
            })
    
    logger.info(f"[PDF++] Page {page_no}: Found {len(results)} quality tables from {len(all_candidates)} candidates")
    return results


def _merge_adjacent_paragraphs(paragraphs: List[Dict[str, Any]], page_width: float) -> List[Dict[str, Any]]:
    """Merge paragraphs that are likely part of the same logical unit."""
    if not paragraphs:
        return paragraphs
    
    merged = []
    current_group = [paragraphs[0]]
    
    for i in range(1, len(paragraphs)):
        prev = paragraphs[i-1]
        curr = paragraphs[i]
        
        # Calculate vertical distance
        prev_bbox = prev["bbox"]
        curr_bbox = curr["bbox"]
        
        vertical_gap = curr_bbox[1] - prev_bbox[3]  # Top of current - bottom of previous
        
        # Check if they're in the same column (similar X positions)
        prev_x_center = (prev_bbox[0] + prev_bbox[2]) / 2
        curr_x_center = (curr_bbox[0] + curr_bbox[2]) / 2
        horizontal_similarity = abs(prev_x_center - curr_x_center) < page_width * 0.1
        
        # Merge conditions
        should_merge = (
            vertical_gap < 15 and  # Close vertically
            horizontal_similarity and  # Same column
            not prev.get("is_heading", False) and  # Previous is not heading
            not curr.get("is_heading", False) and  # Current is not heading
            len(prev["content"]) < 200  # Previous paragraph not too long
        )
        
        if should_merge:
            current_group.append(curr)
        else:
            # Finish current group
            if len(current_group) > 1:
                # Merge the group
                merged_content = " ".join([p["content"] for p in current_group])
                merged_bbox = [
                    min(p["bbox"][0] for p in current_group),  # leftmost
                    min(p["bbox"][1] for p in current_group),  # topmost
                    max(p["bbox"][2] for p in current_group),  # rightmost
                    max(p["bbox"][3] for p in current_group),  # bottommost
                ]
                merged.append({
                    "page": current_group[0]["page"],
                    "content": merged_content,
                    "bbox": merged_bbox,
                    "is_heading": False,
                    "merged_from": len(current_group)
                })
            else:
                merged.append(current_group[0])
            
            current_group = [curr]
    
    # Handle last group
    if len(current_group) > 1:
        merged_content = " ".join([p["content"] for p in current_group])
        merged_bbox = [
            min(p["bbox"][0] for p in current_group),
            min(p["bbox"][1] for p in current_group),
            max(p["bbox"][2] for p in current_group),
            max(p["bbox"][3] for p in current_group),
        ]
        merged.append({
            "page": current_group[0]["page"],
            "content": merged_content,
            "bbox": merged_bbox,
            "is_heading": False,
            "merged_from": len(current_group)
        })
    else:
        merged.append(current_group[0])
    
    logger.info(f"[PDF++] Merged {len(paragraphs)} paragraphs into {len(merged)} units")
    return merged


def _filter_plausible_table_bboxes(page_rect: fitz.Rect, table_bboxes: List[List[float]]) -> List[List[float]]:
    """Filter table bboxes to avoid masking entire pages."""
    try:
        page_area = page_rect.width * page_rect.height
        filtered_bboxes = []
        
        for bbox in table_bboxes:
            if len(bbox) != 4:
                continue
            
            x0, y0, x1, y1 = bbox
            bbox_area = (x1 - x0) * (y1 - y0)
            
            # Skip bboxes that cover more than 80% of page (likely errors)
            if bbox_area > page_area * 0.8:
                continue
            
            # Skip very small bboxes (likely noise)
            if bbox_area < 100:  # Less than 10x10 points
                continue
            
            filtered_bboxes.append(bbox)
        
        return filtered_bboxes
    except Exception:
        return []


def _split_merged_table_cells(cell_text: str) -> str:
    """Split merged table cells like '3.00%0.50%' -> '3.00% | 0.50%'."""
    try:
        # Pattern for merged percentages
        pattern = r'(\d+\.\d+%)(?=\d+\.\d+%)'
        if re.search(pattern, cell_text):
            # Split merged percentages
            parts = re.split(r'(?=\d+\.\d+%)', cell_text)
            parts = [p for p in parts if p.strip()]  # Remove empty parts
            if len(parts) > 1:
                return ' | '.join(parts)
        
        # Pattern for merged currency values
        pattern = r'(Rp\s*\d+(?:[.,]\d+)*)(?=Rp)'
        if re.search(pattern, cell_text):
            parts = re.split(r'(?=Rp)', cell_text)
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) > 1:
                return ' | '.join(parts)
        
        # Pattern for merged numbers with spaces
        pattern = r'(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)'
        if re.search(pattern, cell_text) and len(cell_text.split()) <= 3:
            parts = re.split(r'\s+', cell_text.strip())
            if len(parts) > 1 and all(re.match(r'\d+(?:[.,]\d+)?', p) for p in parts):
                return ' | '.join(parts)
        
        return cell_text
    except Exception:
        return cell_text


def _detect_panel_structure(df: Any) -> bool:
    """Detect if DataFrame represents a financial panel (rows as items, cols as metrics)."""
    try:
        import pandas as pd
        if not isinstance(df, pd.DataFrame) or df.empty:
            return False
        
        # Convert to string for analysis
        text_df = df.astype(str)
        
        # Check if first column is mostly text (labels) and rest are numbers
        if df.shape[1] < 2:
            return False
        
        first_col = text_df.iloc[:, 0]
        other_cols = text_df.iloc[:, 1:]
        
        # First column should be mostly alphabetic (labels)
        first_col_alpha_ratio = sum(1 for val in first_col if val.replace(' ', '').isalpha()) / len(first_col)
        
        # Other columns should have high numeric content
        numeric_content = 0
        total_cells = 0
        for col in other_cols.columns:
            for val in other_cols[col]:
                total_cells += 1
                if re.search(r'\d', str(val)):
                    numeric_content += 1
        
        numeric_ratio = numeric_content / max(total_cells, 1)
        
        # Panel structure: first column mostly text, others mostly numeric
        return first_col_alpha_ratio > 0.6 and numeric_ratio > 0.5
    except Exception:
        return False


def _process_panel_table(df: Any) -> Any:
    """Process panel-style tables to handle merged cells."""
    try:
        import pandas as pd
        if not isinstance(df, pd.DataFrame):
            return df
        
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


def _join_text_spans_properly(block: Dict[str, Any]) -> str:
    """Join text spans from a block while preserving meaningful spacing."""
    try:
        result = []
        
        for line in block.get("lines", []):
            line_text = []
            prev_span_right = None
            
            for span in line.get("spans", []):
                text = span.get("text", "").strip()
                if not text:
                    continue
                
                # Check spacing between spans
                span_left = span.get("bbox", [0, 0, 0, 0])[0]
                
                if prev_span_right is not None:
                    gap = span_left - prev_span_right
                    if gap > 10:  # Significant gap - might be column separation
                        text = " " + text  # Ensure space
                
                line_text.append(text)
                prev_span_right = span.get("bbox", [0, 0, 0, 0])[2]
            
            if line_text:
                result.append(" ".join(line_text))
        
        # Join lines with single spaces, then normalize
        final_text = " ".join(result)
        return _norm_spaces(final_text)
    
    except Exception:
        # Fallback: try to get any text from the block
        return _norm_spaces(str(block.get("text", "")))


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

        # Enhanced page processing with layout analysis
        raw = page.get_text("rawdict") or {}
        blocks = raw.get("blocks", [])
        blocks = sorted(blocks, key=lambda b: (b.get("bbox", [0, 0, 0, 0])[1], b.get("bbox", [0, 0, 0, 0])[0]))
        
        # Analyze page layout for column detection
        layout_info = _detect_column_layout(raw, page.rect)
        page_content_type = _detect_page_content_type(raw)
        
        logger.info(f"[PDF++] Page {page_no}: Layout type: {layout_info.get('layout_type')}, Content type: {page_content_type}")

        # STEP 1: Extract tables first to get their bboxes for masking
        logger.info(f"[PDF++] Page {page_no}: Extracting tables first...")
        page_tables = _read_tables_enhanced(pdf_path, page_no)
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
                    
                    is_heading, heading_level = _detect_headings_from_spans(spans_all)
                    column = _get_block_column(bbox, layout_info)
                    
                    if is_heading:
                        heading_stack.append(text)
                        heading_prefix = "#" * min(heading_level, 3)  # Limit to H3
                        all_paragraphs.append({
                            "page": page_no,
                            "content": f"{heading_prefix} {text}",
                            "bbox": bbox,
                            "column": column,
                            "is_heading": True,
                            "heading_level": heading_level
                        })
                    else:
                        all_paragraphs.append({
                            "page": page_no,
                            "content": text,
                            "bbox": bbox,
                            "is_heading": False
                        })
                    
                    # Add to metadata with enhanced information
                    section = heading_stack[-1] if heading_stack else "Main"
                    unit_id = f"{doc_id}_p{page_no}_par_{len(units_meta)}"
                    units_meta.append(asdict(UnitMetadata(
                        doc_id, page_no, "paragraph", section, tuple(bbox),
                        column=column, unit_id=unit_id, source="pymupdf"
                    )))
            
            elif block_type == "table-candidate":
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
    
    # Apply paragraph merging before assembly
    logger.info(f"[PDF++] Pre-merge: {len(all_paragraphs)} paragraphs")
    if all_paragraphs:
        # Estimate average page width from first page with paragraphs
        first_page_paras = [p for p in all_paragraphs if p["page"] == all_paragraphs[0]["page"]]
        if first_page_paras:
            avg_page_width = max(p["bbox"][2] for p in first_page_paras)
            all_paragraphs = _merge_adjacent_paragraphs(all_paragraphs, avg_page_width)
    
    # POSITION-BASED CONTENT ASSEMBLY
    # Create unified content units for proper ordering
    all_content_units = []
    
    # Add paragraphs as content units
    for para in all_paragraphs:
        all_content_units.append({
            "type": "paragraph",
            "page": para["page"],
            "bbox": para["bbox"],
            "column": para.get("column", "full"),
            "content": para["content"],
            "is_heading": para.get("is_heading", False),
            "heading_level": para.get("heading_level", 0),
            "sort_key": (para["page"], para["bbox"][1], para["bbox"][0])  # page, Y, X
        })
    
    # Add tables as content units
    for table in all_tables:
        all_content_units.append({
            "type": "table",
            "page": table["page"],
            "bbox": table["bbox"],
            "column": "full",  # Tables typically span full width
            "content": table["content"],
            "title": table["title"],
            "sort_key": (table["page"], table["bbox"][1], table["bbox"][0]),
            "quality": table.get("quality", 0)
        })
    
    # Add figures as content units (simplified for text-only pipeline)
    for figure in all_figures:
        if figure.get("description"):  # Only include if OCR produced text
            all_content_units.append({
                "type": "figure",
                "page": figure["page"],
                "bbox": figure["bbox"],
                "column": "full",
                "content": figure["description"],
                "sort_key": (figure["page"], figure["bbox"][1], figure["bbox"][0])
            })
    
    # Sort all content by position (page, Y coordinate, X coordinate)
    all_content_units.sort(key=lambda x: x["sort_key"])
    
    logger.info(f"[PDF++] Position-based assembly: {len(all_content_units)} total units")
    
    # Assemble markdown in natural reading order
    parts = []
    current_page = 0
    
    for unit in all_content_units:
        # Add page break marker for debugging
        if unit["page"] != current_page:
            if current_page > 0:  # Don't add for first page
                parts.append(f"\n\n---\n\n")  # Page separator
            current_page = unit["page"]
        
        # Render based on content type
        if unit["type"] == "paragraph":
            if unit["is_heading"]:
                # Add proper spacing for headings
                heading_prefix = "#" * min(unit["heading_level"], 3)
                parts.append(f"\n{heading_prefix} {unit['content']}\n\n")
            else:
                parts.append(f"{unit['content']}\n\n")
        
        elif unit["type"] == "table":
            parts.append(f"### {unit['title']}\n\n")
            parts.append(f"{unit['content']}\n\n")
        
        elif unit["type"] == "figure":
            parts.append(f"_{unit['content']}_\n\n")  # Italicized description
    
    # Final markdown assembly
    markdown = "".join(parts).strip()
    
    # Clean up excessive newlines
    markdown = re.sub(r'\n{3,}', '\n\n', markdown)
    markdown += "\n"
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
    # Log assembly statistics
    total_paragraphs = sum(1 for u in all_content_units if u["type"] == "paragraph")
    total_tables = sum(1 for u in all_content_units if u["type"] == "table")
    total_figures = sum(1 for u in all_content_units if u["type"] == "figure")
    
    logger.info(f"[PDF++] Final assembly: {total_paragraphs} paragraphs, {total_tables} tables, {total_figures} figures")
    logger.info(f"[PDF++] Completed for {doc_id} in {ended - started:.2f}s")
    return result


def run_conversion_and_persist(input_pdf_path: str, mode: str = "basic", doc_id: Optional[str] = None) -> Dict[str, Any]:
    """Entry point for API: runs conversion and returns summary dict."""
    return process_pdf_markdownpp(input_pdf_path, mode=mode, doc_id=doc_id)
