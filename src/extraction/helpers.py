# Helper methods for extractor_v2.py
"""Additional methods to complete the PDF extraction functionality"""

import re
import io
import pandas as pd
from typing import List, Tuple, Dict, Optional, Any


def _postprocess_table(self, df: pd.DataFrame) -> pd.DataFrame:
    """Post-process table to fix merged cells and normalize"""
    # Fix merged numerics
    df = self._split_merged_numerics(df)
    
    # Normalize headers
    df = self._normalize_headers(df)
    
    # Remove empty columns
    df = df.loc[:, (df != '').any(axis=0)]
    
    # Trim whitespace
    df = df.applymap(lambda x: str(x).strip() if pd.notna(x) else '')
    
    return df

def _split_merged_numerics(self, df: pd.DataFrame) -> pd.DataFrame:
    """Split merged numeric values in cells"""
    merged_count = 0
    
    def split_cell(cell):
        nonlocal merged_count
        if not isinstance(cell, str):
            return cell
        
        # Pattern: two percentages merged like "3.00%0.50%"
        pattern1 = r'(\d+\.?\d*%)(\d+\.?\d*%)'
        match = re.findall(pattern1, cell)
        if match:
            merged_count += 1
            return ' | '.join(match[0])
        
        # Pattern: two numbers merged like "44593.65 44368.5"
        if re.match(r'^[\d\.\s]+$', cell):
            parts = cell.split()
            if len(parts) == 2 and all(re.match(r'^\d+\.?\d*$', p) for p in parts):
                merged_count += 1
                return ' | '.join(parts)
        
        # Pattern: number with parentheses (negative) like "(0.50)"
        cell = re.sub(r'\((\d+\.?\d*)\)', r'-\1', cell)
        
        # Pattern: merged date-number like "11-Feb12-Feb"
        pattern2 = r'(\d{1,2}-\w{3})(\d{1,2}-\w{3})'
        match = re.findall(pattern2, cell)
        if match:
            merged_count += 1
            return ' | '.join(match[0])
        
        return cell
    
    # Apply splitting to all cells
    for col in df.columns:
        df[col] = df[col].apply(split_cell)
    
    self.metrics['merged_tokens'] = merged_count
    return df

def _normalize_headers(self, df: pd.DataFrame) -> pd.DataFrame:
    """Normalize table headers"""
    # Clean headers
    df.columns = [str(col).strip().replace('\n', ' ') for col in df.columns]
    
    # Remove empty header names
    df.columns = ['Col' + str(i) if not col else col for i, col in enumerate(df.columns)]
    
    return df

def _table_to_markdown(self, df: pd.DataFrame) -> str:
    """Convert DataFrame to markdown table"""
    try:
        # Use pandas to_markdown if available (requires tabulate)
        try:
            import tabulate
            return df.to_markdown(index=False)
        except ImportError:
            pass
    except:
        pass
    
    # Manual markdown generation as fallback
    lines = []
    
    # Headers
    headers = '| ' + ' | '.join(str(h) for h in df.columns) + ' |'
    lines.append(headers)
    
    # Separator
    sep = '|' + '|'.join(['-' * max(len(str(h)) + 2, 3) for h in df.columns]) + '|'
    lines.append(sep)
    
    # Rows
    for _, row in df.iterrows():
        row_str = '| ' + ' | '.join(str(val) for val in row) + ' |'
        lines.append(row_str)
    
    return '\n'.join(lines)

def _is_in_exclusion_zone(self, bbox: Tuple, exclusion_zones: List[Tuple]) -> bool:
    """Check if bbox overlaps with any exclusion zone"""
    for zone in exclusion_zones:
        if self._bbox_overlap(bbox, zone) > 0.1:  # 10% overlap threshold
            return True
    return False

def _bbox_overlap(self, bbox1: Tuple, bbox2: Tuple) -> float:
    """Calculate overlap ratio between two bboxes"""
    x0 = max(bbox1[0], bbox2[0])
    y0 = max(bbox1[1], bbox2[1])
    x1 = min(bbox1[2], bbox2[2])
    y1 = min(bbox1[3], bbox2[3])
    
    if x0 >= x1 or y0 >= y1:
        return 0.0
    
    intersection = (x1 - x0) * (y1 - y0)
    area1 = (bbox1[2] - bbox1[0]) * (bbox1[3] - bbox1[1])
    
    if area1 == 0:
        return 0.0
    
    return intersection / area1

def _merge_adjacent_blocks(self, blocks: List[Dict]) -> List[Tuple[str, Tuple]]:
    """Merge adjacent text blocks into paragraphs"""
    if not blocks:
        return []
    
    merged = []
    current_text = []
    current_bbox = None
    
    for block in blocks:
        if current_bbox is None:
            current_text = [block['text']]
            current_bbox = list(block['bbox'])
        else:
            # Check if blocks are adjacent (within 20 points vertically)
            y_gap = block['bbox'][1] - current_bbox[3]
            
            if y_gap < 20:  # Merge if close
                current_text.append(block['text'])
                # Expand bbox
                current_bbox[0] = min(current_bbox[0], block['bbox'][0])
                current_bbox[1] = min(current_bbox[1], block['bbox'][1])
                current_bbox[2] = max(current_bbox[2], block['bbox'][2])
                current_bbox[3] = max(current_bbox[3], block['bbox'][3])
            else:
                # Save current paragraph
                merged.append((' '.join(current_text), tuple(current_bbox)))
                current_text = [block['text']]
                current_bbox = list(block['bbox'])
    
    # Don't forget last paragraph
    if current_text:
        merged.append((' '.join(current_text), tuple(current_bbox)))
    
    return merged

def _process_figure(self, fig_block: Dict, page, page_num: int,
                   doc_id: str, artefacts_dir, 
                   exclusion_zones: List[Tuple]) -> Optional[Dict]:
    """Process a figure block (text-only pipeline - no OCR)"""
    # Since this is a text-only pipeline, we skip figure processing
    return None

def _ocr_full_page(self, page, page_num: int, 
                  artefacts_dir) -> Optional[str]:
    """OCR full page when no text is found"""
    if not hasattr(self, 'TESSERACT_AVAILABLE') or not self.TESSERACT_AVAILABLE:
        return None
    
    try:
        import pytesseract
        from PIL import Image
        import fitz
        
        # Get page image
        mat = fitz.Matrix(self.dpi_fullpage / 72.0, self.dpi_fullpage / 72.0)
        pix = page.get_pixmap(matrix=mat)
        
        # Convert to PIL Image
        img_data = pix.pil_tobytes(format="PNG")
        img = Image.open(io.BytesIO(img_data))
        
        # Run OCR with primary PSM
        config = f'--oem 3 --psm {self.ocr_primary_psm} -l {self.ocr_lang}'
        text = pytesseract.image_to_string(img, config=config)
        
        # Try fallback PSMs if text is too short
        if len(text.strip()) < 50:
            for psm in self.ocr_fallback_psm:
                config = f'--oem 3 --psm {psm} -l {self.ocr_lang}'
                alt_text = pytesseract.image_to_string(img, config=config)
                if len(alt_text.strip()) > len(text.strip()):
                    text = alt_text
        
        self.metrics['ocr_full_pages'] = self.metrics.get('ocr_full_pages', 0) + 1
        return text.strip() if text else None
        
    except Exception as e:
        print(f"OCR failed on page {page_num}: {e}")
        return None
