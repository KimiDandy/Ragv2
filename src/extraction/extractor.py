"""
Professional PDF Extraction System

Implements Map-Sort-Mine architecture with column awareness, 
table masking, and adaptive OCR for production-grade PDF extraction.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict, field
from typing import List, Tuple, Dict, Any, Optional, Union
import fitz  # PyMuPDF
import pandas as pd
import numpy as np
import json
import os
import re
import hashlib
import json
import os
import re
import time
import logging
from pathlib import Path
from collections import defaultdict

from ..shared.document_meta import (
    set_original_pdf_filename,
    get_base_name,
    default_markdown_filename,
    set_markdown_info,
)

# Optional imports with graceful degradation
try:
    import camelot
    CAMELOT_AVAILABLE = True
except ImportError:
    CAMELOT_AVAILABLE = False
    print("Warning: Camelot not installed. Table extraction will use fallback methods.")

try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False
    print("Warning: pdfplumber not installed. Advanced table extraction disabled.")

try:
    import pytesseract
    from PIL import Image
    
    # Auto-detect Tesseract on Windows
    if os.name == 'nt':  # Windows
        tesseract_paths = [
            r'C:\Program Files\Tesseract-OCR\tesseract.exe',
            r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
            r'C:\Users\AppData\Local\Tesseract-OCR\tesseract.exe',
        ]
        for path in tesseract_paths:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                break
    
    # Test if Tesseract works
    try:
        pytesseract.get_tesseract_version()
        TESSERACT_AVAILABLE = True
    except:
        TESSERACT_AVAILABLE = False
        print("Warning: Tesseract not configured. OCR disabled.")
except ImportError:
    TESSERACT_AVAILABLE = False
    print("Warning: pytesseract/PIL not installed. OCR disabled.")

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class Unit:
    """Represents a single content unit in the document"""
    unit_id: str
    doc_id: str
    page: int
    unit_type: str  # "paragraph" | "table" | "figure"
    column: str  # "left" | "right" | "single" | "full"
    bbox: Tuple[float, float, float, float]  # (x0, y0, x1, y1)
    y0: float
    source: str  # "pymupdf" | "camelot" | "pdfplumber" | "ocr"
    anchor: str  # For evidence linking
    content: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TableSchema:
    """Schema for extracted tables"""
    table_id: str
    page: int
    bbox: Tuple[float, float, float, float]
    headers: List[str]
    rows: List[List[str]]
    fixes: Dict[str, Any]
    provenance: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractionResult:
    """Result of PDF extraction"""
    doc_id: str
    markdown_path: str
    units_meta_path: str
    artefacts_dir: str
    metrics_path: str
    original_pdf_filename: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PDFExtractorV2:
    """Advanced PDF extraction with layout awareness and table masking"""
    
    def __init__(self, **kwargs):
        self.ocr_lang = kwargs.get('ocr_lang', 'ind+eng')
        self.ocr_dpi = kwargs.get('ocr_dpi', 300)  # Optimal balance speed/quality
        self.enable_ocr = kwargs.get('enable_ocr', True) and TESSERACT_AVAILABLE
        self.ocr_fast_mode = kwargs.get('ocr_fast_mode', True)  # Use optimized settings
        self.dpi_fullpage = kwargs.get('dpi_fullpage', 300)
        self.zoom_clip = kwargs.get('zoom_clip', 2.0)
        self.enable_pdfplumber_fallback = kwargs.get('enable_pdfplumber_fallback', True)
        self.min_table_area_ratio = kwargs.get('min_table_area_ratio', 0.01)
        self.header_footer_mode = kwargs.get('header_footer_mode', 'auto')
        self.header_margin_pct = kwargs.get('header_margin_pct', 0.05)
        self.footer_margin_pct = kwargs.get('footer_margin_pct', 0.05)
        self.max_ocr_crops_per_page = kwargs.get('max_ocr_crops_per_page', 12)
        self.figure_min_area_ratio = kwargs.get('figure_min_area_ratio', 0.003)
        self.column_split_strategy = kwargs.get('column_split_strategy', 'histogram')
        self.include_debug_anchors = kwargs.get('include_debug_anchors', False)
        
        self.ocr_cache = {}  # Cache OCR results by image hash
        self.metrics = defaultdict(int)
        self.units = []
        self.tables = []
        self.figures = []
    
    def normalize_bbox(self, bbox: Tuple, source: str, page_width: float, page_height: float, rotation: int = 0) -> Tuple:
        """Normalize bbox to PyMuPDF coordinate system (top-left origin, Y downward)"""
        x0, y0, x1, y1 = bbox
        
        # Convert pdfplumber/pdfminer coordinates (bottom-left origin) to PyMuPDF (top-left origin)
        if source in {'pdfplumber', 'pdfminer', 'camelot'}:
            y0_new = page_height - y1
            y1_new = page_height - y0
            y0, y1 = y0_new, y1_new
        # PyMuPDF coordinates are already in the correct system, just validate
        elif source == 'pymupdf':
            pass  # Already in correct coordinate system
        
        # Handle rotation (simplified - most PDFs are 0 degrees)
        if rotation == 90:
            x0, y0, x1, y1 = y0, page_width - x1, y1, page_width - x0
        elif rotation == 180:
            x0, y0, x1, y1 = page_width - x1, page_height - y1, page_width - x0, page_height - y0
        elif rotation == 270:
            x0, y0, x1, y1 = page_height - y1, x0, page_height - y0, x1
        
        # Clamp to page boundaries
        x0 = max(0, min(x0, page_width))
        y0 = max(0, min(y0, page_height))
        x1 = max(0, min(x1, page_width))
        y1 = max(0, min(y1, page_height))
        
        # Ensure valid bbox
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
            
        return (x0, y0, x1, y1)
    
    def calculate_overlap_metrics(self, bbox1: Tuple, bbox2: Tuple) -> Dict[str, float]:
        """Calculate comprehensive overlap metrics between two bboxes"""
        x0_1, y0_1, x1_1, y1_1 = bbox1
        x0_2, y0_2, x1_2, y1_2 = bbox2
        
        # Intersection
        x0_i = max(x0_1, x0_2)
        y0_i = max(y0_1, y0_2)
        x1_i = min(x1_1, x1_2)
        y1_i = min(y1_1, y1_2)
        
        if x0_i >= x1_i or y0_i >= y1_i:
            return {'iou': 0.0, 'overlap_ratio': 0.0, 'x_overlap_ratio': 0.0}
        
        intersection_area = (x1_i - x0_i) * (y1_i - y0_i)
        area1 = (x1_1 - x0_1) * (y1_1 - y0_1)
        area2 = (x1_2 - x0_2) * (y1_2 - y0_2)
        
        iou = intersection_area / (area1 + area2 - intersection_area) if (area1 + area2 - intersection_area) > 0 else 0
        overlap_ratio = intersection_area / area1 if area1 > 0 else 0
        
        # Horizontal overlap ratio
        x_overlap = max(0, x1_i - x0_i)
        x_width1 = x1_1 - x0_1
        x_overlap_ratio = x_overlap / x_width1 if x_width1 > 0 else 0
        
        return {
            'iou': iou,
            'overlap_ratio': overlap_ratio,
            'x_overlap_ratio': x_overlap_ratio
        }

    
    def extract(
        self,
        doc_id: str,
        pdf_path: str,
        out_dir: str,
        original_filename: Optional[str] = None,
    ) -> ExtractionResult:
        """Main extraction entry point"""
        start_time = time.time()
        
        # 0) Initialize folders and logging
        artefacts_dir = Path(out_dir) / doc_id
        pages_dir = artefacts_dir / "pages"
        crops_dir = artefacts_dir / "crops"
        logs_dir = artefacts_dir / "logs"
        meta_dir = artefacts_dir / "meta"
        
        for dir_path in [pages_dir, crops_dir, logs_dir, meta_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
        
        # Initialize progress file
        progress_path = artefacts_dir / "conversion_progress.json"
        self._update_progress(progress_path, "running", 0.1, "Memulai ekstraksi PDF...")
        
        # Persist original filename metadata if available
        if original_filename:
            set_original_pdf_filename(artefacts_dir, original_filename)

        base_name = get_base_name(artefacts_dir)

        # Set up logging
        log_path = logs_dir / "extract.log"
        file_handler = logging.FileHandler(log_path, mode='w')
        file_handler.setLevel(logging.INFO)
        logger.addHandler(file_handler)
        
        logger.info(f"Starting extraction for {doc_id}")
        
        # 1) Open PDF and render debug pages
        doc = fitz.open(pdf_path)
        total_pages = len(doc)
        logger.info(f"Document has {total_pages} pages")
        
        # Render debug pages
        for page_num in range(total_pages):
            page = doc[page_num]
            mat = fitz.Matrix(self.dpi_fullpage / 72.0, self.dpi_fullpage / 72.0)
            pix = page.get_pixmap(matrix=mat)
            pix.save(str(pages_dir / f"page-{page_num + 1}.png"))
        
        # Process each page
        all_units = []
        all_tables = []
        all_figures = []
        
        for page_num in range(total_pages):
            progress_pct = 0.2 + (0.6 * page_num / total_pages)
            self._update_progress(progress_path, "running", progress_pct, 
                                f"Memproses halaman {page_num + 1}/{total_pages}...")
            logger.info(f"Processing page {page_num + 1}/{total_pages}")
            page = doc[page_num]
            page_units, page_tables, page_figures = self._process_page(
                page, page_num + 1, doc_id, artefacts_dir
            )
            all_units.extend(page_units)
            all_tables.extend(page_tables)
            all_figures.extend(page_figures)
        
        doc.close()
        
        # 7) Assemble markdown by position
        self._update_progress(progress_path, "running", 0.8, "Menyusun markdown...")
        markdown_content = self._assemble_markdown(all_units)
        
        # 8) Persist outputs
        markdown_filename = default_markdown_filename("v1", base_name)
        markdown_path = artefacts_dir / markdown_filename
        markdown_path.write_text(markdown_content, encoding='utf-8')
        set_markdown_info(
            artefacts_dir,
            "v1",
            filename=markdown_filename,
            relative_path=markdown_filename,
        )
        
        # Save metadata in both locations for compatibility
        units_meta = [unit.to_dict() for unit in all_units]
        
        # Save in root directory (for backward compatibility)
        units_meta_path = artefacts_dir / "units_metadata.json"
        units_meta_path.write_text(json.dumps(units_meta, indent=2), encoding='utf-8')
        
        # Also save in meta subfolder
        meta_units_path = meta_dir / "units_metadata.json"
        meta_units_path.write_text(json.dumps(units_meta, indent=2), encoding='utf-8')
        
        if all_tables:
            tables_path = artefacts_dir / "tables.json"
            tables_data = [table.to_dict() for table in all_tables]
            tables_path.write_text(json.dumps(tables_data, indent=2), encoding='utf-8')
        
        if all_figures:
            figures_path = artefacts_dir / "figures.json"
            figures_path.write_text(json.dumps(all_figures, indent=2), encoding='utf-8')
        
        # Write metrics
        self.metrics['total_time'] = time.time() - start_time
        self.metrics['total_pages'] = total_pages
        self.metrics['total_units'] = len(all_units)
        self.metrics['total_tables'] = len(all_tables)
        self.metrics['total_figures'] = len(all_figures)
        
        metrics_path = artefacts_dir / "metrics.json"
        metrics_path.write_text(json.dumps(dict(self.metrics), indent=2), encoding='utf-8')
        
        # Update progress to complete
        self._update_progress(progress_path, "complete", 1.0, "Selesai")
        
        logger.info(f"Extraction complete in {self.metrics['total_time']:.2f}s")
        
        return ExtractionResult(
            doc_id=doc_id,
            markdown_path=str(markdown_path),
            units_meta_path=str(units_meta_path),
            artefacts_dir=str(artefacts_dir),
            metrics_path=str(metrics_path),
            original_pdf_filename=original_filename,
        )
    
    def _process_page(self, page: fitz.Page, page_num: int, doc_id: str, 
                      artefacts_dir: Path) -> Tuple[List[Unit], List[TableSchema], List[Dict]]:
        """Process a single page"""
        page_units = []
        page_tables = []
        page_figures = []
        
        # 2) Build layout map
        layout = self._build_layout_map(page)
        
        # Detect and remove headers/footers
        layout = self._remove_headers_footers(layout, page.rect)
        
        # Detect columns
        columns = self._detect_columns(layout)
        
        # 3) Extract tables and build exclusion zones
        table_results = self._extract_tables(page, page_num, doc_id)
        exclusion_zones = []
        
        for table_result in table_results:
            table_schema, table_unit = table_result
            page_tables.append(table_schema)
            page_units.append(table_unit)
            
            # Add to exclusion zones with proper padding
            bbox = table_schema.bbox
            # Use reasonable padding to catch text that's part of the table
            padded_bbox = (
                max(0, bbox[0] - 5), 
                max(0, bbox[1] - 5),
                min(page.rect.width, bbox[2] + 5), 
                min(page.rect.height, bbox[3] + 5)
            )
            exclusion_zones.append(padded_bbox)
            logger.info(f"Added exclusion zone for table at {bbox} -> padded to {padded_bbox}")
        
        # 4) Extract paragraphs avoiding table zones
        text_blocks = [b for b in layout['blocks'] if b['type'] == 'text']
        
        # Log for debugging with detailed bbox information
        logger.info(f"Page {page_num}: Found {len(text_blocks)} text blocks before filtering")
        logger.info(f"Page {page_num}: {len(exclusion_zones)} exclusion zones from tables")
        
        # Log exclusion zones for debugging
        for i, zone in enumerate(exclusion_zones):
            logger.info(f"Page {page_num}: Exclusion zone {i}: {zone}")
        
        # Log text blocks for debugging
        for i, block in enumerate(text_blocks[:5]):  # Log first 5 blocks
            logger.info(f"Page {page_num}: Text block {i}: bbox={block['bbox']}, text_preview='{block.get('text', '')[:50]}...'")
        
        filtered_blocks = []
        
        for i, block in enumerate(text_blocks):
            # Ensure text block bbox is normalized to same coordinate system
            raw_bbox = block['bbox']
            # PyMuPDF blocks should already be in correct coordinate system, but normalize for consistency  
            block_bbox = self.normalize_bbox(raw_bbox, 'pymupdf', page.rect.width, page.rect.height, 0)
            block['bbox'] = block_bbox  # Update the block with normalized bbox
            
            # Calculate block area and text length for better filtering decisions
            block_area = (block_bbox[2] - block_bbox[0]) * (block_bbox[3] - block_bbox[1])
            text_length = len(block.get('text', ''))
            
            is_excluded = self._is_in_exclusion_zone(block_bbox, exclusion_zones)
            
            # Special handling: Don't exclude large text blocks (likely important paragraphs)
            if is_excluded and (block_area > 5000 or text_length > 100):
                logger.warning(f"Page {page_num}: OVERRIDING exclusion for large text block {i} - area:{block_area:.1f}, text_len:{text_length}")
                is_excluded = False
            
            if not is_excluded:
                filtered_blocks.append(block)
                logger.info(f"Page {page_num}: KEPT text block {i} at {block_bbox}, area:{block_area:.1f}, text_len:{text_length}")
            else:
                logger.info(f"Page {page_num}: FILTERED text block {i} at {block_bbox} - overlaps with table")
        
        logger.info(f"Page {page_num}: {len(filtered_blocks)} text blocks after filtering (removed {len(text_blocks) - len(filtered_blocks)})")
        
        # Safety check: if ALL text blocks are filtered out, use more lenient criteria
        if not filtered_blocks and text_blocks and exclusion_zones:
            logger.warning(f"Page {page_num}: ALL text blocks filtered out! Using emergency fallback...")
            
            # Emergency fallback: only filter blocks that are ENTIRELY inside tables
            emergency_filtered = []
            for i, block in enumerate(text_blocks):
                block_bbox = block['bbox']
                should_exclude = False
                
                for zone in exclusion_zones:
                    # Only exclude if block is COMPLETELY inside table (very strict)
                    if (block_bbox[0] >= zone[0] and block_bbox[1] >= zone[1] and 
                        block_bbox[2] <= zone[2] and block_bbox[3] <= zone[3]):
                        should_exclude = True
                        logger.info(f"Page {page_num}: Emergency filter - block {i} completely inside table {zone}")
                        break
                
                if not should_exclude:
                    emergency_filtered.append(block)
                    logger.info(f"Page {page_num}: Emergency keep - block {i} at {block_bbox}")
            
            filtered_blocks = emergency_filtered
            logger.warning(f"Page {page_num}: Emergency fallback kept {len(filtered_blocks)} text blocks")
        
        # Group by column and process
        paragraphs = self._build_paragraphs(filtered_blocks, columns, page_num, doc_id)
        page_units.extend(paragraphs)
        
        # Final validation and fallback
        text_units = [u for u in paragraphs if u.unit_type == 'paragraph']
        table_units = [u for u in page_units if u.unit_type == 'table']
        
        # If we have very few text units but there should be more content, try OCR fallback
        if len(text_units) < 2 and len(table_units) > 0:
            logger.warning(f"Page {page_num}: Very few text units ({len(text_units)}) detected, trying OCR backup...")
            
            # Get simple text and check if there's substantial content missing
            simple_text = page.get_text("text")
            if len(simple_text) > 500:  # There should be more content
                # Try to extract text from non-table areas using OCR
                ocr_text = self._ocr_non_table_areas(page, page_num, exclusion_zones, artefacts_dir)
                if ocr_text:
                    ocr_unit = Unit(
                        unit_id=f"u_{doc_id}_p{page_num}_ocr_backup",
                        doc_id=doc_id,
                        page=page_num,
                        unit_type="paragraph",
                        column="full",
                        bbox=(0, 0, page.rect.width, page.rect.height),
                        y0=0,
                        source="ocr_backup",
                        anchor=f"md://u_{doc_id}_p{page_num}_ocr_backup",
                        content=ocr_text
                    )
                    page_units.append(ocr_unit)
                    text_units.append(ocr_unit)
                    logger.info(f"Added OCR backup unit with {len(ocr_text)} characters")
        
        logger.info(f"Page {page_num} FINAL RESULT: {len(text_units)} text units, {len(table_units)} table units")
        
        # 5) Extract figures if any
        figure_blocks = [b for b in layout['blocks'] if b['type'] == 'figure']
        for fig_block in figure_blocks[:self.max_ocr_crops_per_page]:
            figure_data = self._process_figure(
                fig_block, page, page_num, doc_id, artefacts_dir, exclusion_zones
            )
            if figure_data:
                page_figures.append(figure_data['metadata'])
                page_units.append(figure_data['unit'])
        
        # 6) OCR full page if no text found
        if not text_blocks and not table_results and TESSERACT_AVAILABLE:
            logger.info(f"Page {page_num}: No text/tables found, performing full-page OCR...")
            ocr_start = time.time()
            
            ocr_text = self._ocr_full_page(page, page_num, artefacts_dir)
            
            ocr_duration = time.time() - ocr_start
            logger.info(f"Page {page_num}: OCR completed in {ocr_duration:.2f} seconds")
            
            if ocr_text:
                unit = Unit(
                    unit_id=f"u_{doc_id}_p{page_num}_ocr",
                    doc_id=doc_id,
                    page=page_num,
                    unit_type="paragraph",
                    column="full",
                    bbox=tuple(page.rect),
                    y0=page.rect.y0,
                    source="ocr",
                    anchor=f"md://u_{doc_id}_p{page_num}_ocr",
                    content=ocr_text
                )
                page_units.append(unit)
                logger.info(f"Page {page_num}: OCR extracted {len(ocr_text)} characters")
        
        return page_units, page_tables, page_figures
    
    def _build_layout_map(self, page: fitz.Page) -> Dict[str, Any]:
        """Build layout map from page - IMPROVED VERSION"""
        layout = {'blocks': [], 'page_rect': tuple(page.rect)}
        
        # Try multiple extraction methods for better coverage
        # Method 1: Use dict extraction (more reliable than rawdict)
        dict_data = page.get_text("dict")
        
        # Also get simple text for validation
        simple_text = page.get_text("text")
        
        for block_idx, block in enumerate(dict_data.get('blocks', [])):
            block_type = block.get('type', 0)
            bbox = block.get('bbox', (0, 0, 0, 0))
            
            if block_type == 0:  # Text block
                # Extract text from lines and spans
                text_content = []
                for line in block.get('lines', []):
                    for span in line.get('spans', []):
                        text = span.get('text', '')
                        if text.strip():
                            text_content.append(text)
                
                if text_content:
                    layout['blocks'].append({
                        'type': 'text',
                        'bbox': bbox,
                        'text': ' '.join(text_content),
                        'lines': block.get('lines', []),
                        'block_idx': block_idx
                    })
            
            elif block_type == 1:  # Image block
                area_ratio = ((bbox[2] - bbox[0]) * (bbox[3] - bbox[1])) / (page.rect.width * page.rect.height)
                if area_ratio >= self.figure_min_area_ratio:
                    layout['blocks'].append({
                        'type': 'figure',
                        'bbox': bbox,
                        'block_idx': block_idx
                    })
        
        # Fallback: if no text blocks found but simple_text has content
        # Split into paragraphs to avoid single full-page block
        if not any(b['type'] == 'text' for b in layout['blocks']) and simple_text.strip():
            logger.warning("No text blocks from rawdict, using simple text extraction with paragraph splitting")
            
            # Split text into paragraphs
            paragraphs = [p.strip() for p in simple_text.split('\n\n') if p.strip()]
            
            # Create multiple text blocks instead of one massive block
            page_height = page.rect.height
            block_height = page_height / max(len(paragraphs), 1)
            
            for i, para in enumerate(paragraphs):
                y0 = i * block_height
                y1 = min((i + 1) * block_height, page_height)
                
                layout['blocks'].append({
                    'type': 'text',
                    'bbox': (0.0, y0, page.rect.width, y1),
                    'text': para,
                    'lines': [],
                    'block_idx': f"fallback_{i}"
                })
        
        text_blocks = [b for b in layout['blocks'] if b['type'] == 'text']
        figure_blocks = [b for b in layout['blocks'] if b['type'] == 'figure']
        
        logger.info(f"Layout map: {len(layout['blocks'])} total blocks ({len(text_blocks)} text, {len(figure_blocks)} figures)")
        
        # ENHANCED DEBUGGING: Log all text blocks in detail
        for i, block in enumerate(text_blocks):
            bbox = block['bbox']
            text_preview = block.get('text', '')[:150]
            bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            logger.info(f"Text block {i}: bbox={bbox}, area={bbox_area:.1f}, text='{text_preview}...'")
        
        # If very few text blocks found, try alternative extraction
        if len(text_blocks) < 3:
            logger.warning(f"VERY FEW TEXT BLOCKS ({len(text_blocks)})! Trying alternative extraction...")
            
            # Try blocks extraction with different method
            alternative_blocks = self._extract_text_blocks_alternative(page)
            if alternative_blocks:
                logger.info(f"Alternative method found {len(alternative_blocks)} additional blocks")
                layout['blocks'].extend(alternative_blocks)
        
        # If no text blocks found, log warning
        if not text_blocks:
            logger.warning(f"NO TEXT BLOCKS FOUND! Simple text length: {len(simple_text)}")
            if simple_text:
                logger.warning(f"Simple text preview: '{simple_text[:400]}...'")
        
        return layout
    
    def _extract_text_blocks_alternative(self, page: fitz.Page) -> List[Dict]:
        """Alternative text extraction method using different PyMuPDF approaches"""
        alternative_blocks = []
        
        try:
            # Method 1: Use text blocks directly
            text_page = page.get_textpage()
            blocks = text_page.extractBLOCKS()
            
            for i, block in enumerate(blocks):
                # block format: (x0, y0, x1, y1, "text", block_no, block_type)
                if len(block) >= 5:
                    x0, y0, x1, y1, text = block[:5]
                    text = text.strip()
                    
                    # Skip very small text blocks
                    if len(text) > 10 and (x1 - x0) > 30 and (y1 - y0) > 10:
                        alternative_blocks.append({
                            'type': 'text',
                            'bbox': (x0, y0, x1, y1),
                            'text': text,
                            'source': 'extractBLOCKS'
                        })
                        
        except Exception as e:
            logger.debug(f"Alternative text extraction failed: {e}")
        
        # Method 2: Try textbox extraction if first method didn't work
        if not alternative_blocks:
            try:
                # Use get_text with 'dict' but process differently
                words = page.get_text("words")
                
                # Group words into lines based on Y position
                lines = {}
                for word in words:
                    x0, y0, x1, y1, text, block_no, line_no, word_no = word
                    line_key = int(y0)  # Group by Y position
                    
                    if line_key not in lines:
                        lines[line_key] = {'words': [], 'bbox': [x0, y0, x1, y1]}
                    
                    lines[line_key]['words'].append(text)
                    # Expand bbox
                    bbox = lines[line_key]['bbox']
                    lines[line_key]['bbox'] = [
                        min(bbox[0], x0), min(bbox[1], y0),
                        max(bbox[2], x1), max(bbox[3], y1)
                    ]
                
                # Convert lines to blocks
                for line_y, line_data in lines.items():
                    text = ' '.join(line_data['words'])
                    if len(text.strip()) > 15:  # Only substantial text
                        bbox = line_data['bbox']
                        alternative_blocks.append({
                            'type': 'text',
                            'bbox': tuple(bbox),
                            'text': text.strip(),
                            'source': 'words_grouped'
                        })
                        
            except Exception as e:
                logger.debug(f"Words extraction failed: {e}")
        
        logger.info(f"Alternative extraction found {len(alternative_blocks)} blocks")
        return alternative_blocks
    
    def _ocr_non_table_areas(self, page: fitz.Page, page_num: int, 
                            exclusion_zones: List[Tuple], artefacts_dir: Path) -> Optional[str]:
        """OPTIMIZED OCR non-table areas when text detection fails"""
        if not TESSERACT_AVAILABLE:
            return None
            
        try:
            # OPTIMIZED: Use same efficient method as main OCR
            mat = fitz.Matrix(300 / 72.0, 300 / 72.0)  # Optimal DPI
            pix = page.get_pixmap(matrix=mat)
            
            # OPTIMIZED: Direct memory OCR (no temp files)
            img_data = pix.tobytes("png")
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(img_data))
            
            # OCR with balanced config (same as main OCR)
            full_text = pytesseract.image_to_string(
                img,
                lang=self.ocr_lang,
                config='--oem 3 --psm 6 -c tessedit_create_hocr=0'
            )
            
            # Enhanced post-processing
            if full_text:
                full_text = self._post_process_ocr_text(full_text)
                
                # Filter out table-like content
                if exclusion_zones:
                    lines = full_text.split('\n')
                    filtered_lines = []
                    
                    for line in lines:
                        line = line.strip()
                        # Keep substantial text lines
                        if len(line) > 20 and not self._is_likely_table_text(line):
                            filtered_lines.append(line)
                    
                    result = '\n'.join(filtered_lines)
                    return result.strip() if result.strip() else None
            
            return full_text.strip() if full_text else None
            
        except Exception as e:
            logger.warning(f"OCR backup failed on page {page_num}: {e}")
            return None
    
    def _is_likely_table_text(self, text: str) -> bool:
        """Check if text line is likely from a table"""
        # Simple heuristics to identify table text
        text = text.strip()
        
        # Very short text
        if len(text) < 10:
            return True
        
        # Mostly numbers and punctuation
        non_alpha = sum(1 for c in text if not c.isalpha())
        if len(text) > 0 and non_alpha / len(text) > 0.7:
            return True
        
        # Common table patterns
        table_patterns = ['%', '$', '|', 'USD', 'IDR', 'Feb', 'Jan']
        if any(pattern in text for pattern in table_patterns):
            return True
        
        return False
    
    def _remove_headers_footers(self, layout: Dict, page_rect: fitz.Rect) -> Dict:
        """Remove headers and footers based on mode"""
        if self.header_footer_mode == 'margin':
            # Remove blocks in margin areas
            header_y = page_rect.height * self.header_margin_pct
            footer_y = page_rect.height * (1 - self.footer_margin_pct)
            
            filtered_blocks = []
            for block in layout['blocks']:
                y_center = (block['bbox'][1] + block['bbox'][3]) / 2
                if header_y < y_center < footer_y:
                    filtered_blocks.append(block)
            
            layout['blocks'] = filtered_blocks
        
        elif self.header_footer_mode == 'auto':
            # Simple auto detection - remove very small blocks at top/bottom
            filtered_blocks = []
            for block in layout['blocks']:
                # Skip very small text at page edges
                if block['type'] == 'text':
                    text = block.get('text', '')
                    y_pos = block['bbox'][1]
                    # Skip page numbers, dates at extremes
                    if len(text) < 50 and (y_pos < 50 or y_pos > page_rect.height - 50):
                        continue
                filtered_blocks.append(block)
            
            layout['blocks'] = filtered_blocks
        
        return layout
    
    def _detect_columns(self, layout: Dict) -> Dict[str, Any]:
        """Detect column layout using histogram or kmeans strategy"""
        text_blocks = [b for b in layout['blocks'] if b['type'] == 'text']
        
        if not text_blocks:
            return {'type': 'single', 'boundaries': []}
        
        if self.column_split_strategy == 'histogram':
            return self._detect_columns_histogram(text_blocks, layout['page_rect'])
        else:  # kmeans2
            return self._detect_columns_kmeans(text_blocks, layout['page_rect'])
    
    def _detect_columns_histogram(self, text_blocks: List[Dict], 
                                  page_rect: Tuple) -> Dict[str, Any]:
        """Detect columns using X-coordinate histogram"""
        if len(text_blocks) < 5:
            return {'type': 'single', 'boundaries': []}
        
        # Get X centers
        x_centers = [(b['bbox'][0] + b['bbox'][2]) / 2 for b in text_blocks]
        page_width = page_rect[2] - page_rect[0]
        page_mid = page_width / 2
        
        # Check distribution
        left_blocks = [x for x in x_centers if x < page_mid]
        right_blocks = [x for x in x_centers if x >= page_mid]
        
        if len(left_blocks) > 3 and len(right_blocks) > 3:
            # Verify there's a clear gap
            left_max = max(left_blocks) if left_blocks else 0
            right_min = min(right_blocks) if right_blocks else page_width
            
            if right_min - left_max > page_width * 0.05:  # 5% gap minimum
                return {
                    'type': 'two_column',
                    'boundaries': [page_mid],
                    'left_range': (0, page_mid),
                    'right_range': (page_mid, page_width)
                }
        
        return {'type': 'single', 'boundaries': []}
    
    def _detect_columns_kmeans(self, text_blocks: List[Dict], 
                               page_rect: Tuple) -> Dict[str, Any]:
        """Detect columns using K-means clustering"""
        try:
            from sklearn.cluster import KMeans
            
            if len(text_blocks) < 10:
                return {'type': 'single', 'boundaries': []}
            
            # Get X centers
            x_centers = np.array([(b['bbox'][0] + b['bbox'][2]) / 2 for b in text_blocks]).reshape(-1, 1)
            
            # Check variance
            if np.std(x_centers) < 50:  # Low variance = likely single column
                return {'type': 'single', 'boundaries': []}
            
            # Cluster
            kmeans = KMeans(n_clusters=2, random_state=42)
            labels = kmeans.fit_predict(x_centers)
            
            # Find boundary
            centers = kmeans.cluster_centers_.flatten()
            boundary = np.mean(centers)
            
            # Verify clusters are well separated
            cluster_0 = x_centers[labels == 0]
            cluster_1 = x_centers[labels == 1]
            
            if len(cluster_0) > 3 and len(cluster_1) > 3:
                gap = abs(np.max(cluster_0) - np.min(cluster_1))
                page_width = page_rect[2] - page_rect[0]
                
                if gap > page_width * 0.05:
                    return {
                        'type': 'two_column',
                        'boundaries': [boundary],
                        'left_range': (0, boundary),
                        'right_range': (boundary, page_width)
                    }
        except ImportError:
            pass  # sklearn not available, fallback to single column
        
        return {'type': 'single', 'boundaries': []}
    
    def _extract_tables(self, page: fitz.Page, page_num: int, 
                       doc_id: str) -> List[Tuple[TableSchema, Unit]]:
        """Extract tables - prioritize PDFPlumber (more reliable) over Camelot"""
        results = []
        
        # Try PDFPlumber first (generally more reliable for most tables)
        if PDFPLUMBER_AVAILABLE and self.enable_pdfplumber_fallback:
            results = self._extract_tables_pdfplumber(page, page_num, doc_id)
            if results:
                logger.info(f"PDFPlumber found {len(results)} tables on page {page_num}")
                return results
        
        # Fallback to Camelot if PDFPlumber found nothing
        if CAMELOT_AVAILABLE and not results:
            results = self._extract_tables_camelot(page, page_num, doc_id)
            if results:
                logger.info(f"Camelot found {len(results)} tables on page {page_num}")
        
        return results
    
    def _extract_tables_camelot(self, page: fitz.Page, page_num: int,
                                doc_id: str) -> List[Tuple[TableSchema, Unit]]:
        """Extract tables using Camelot"""
        results = []
        
        try:
            import tempfile
            # Save page as temp PDF for Camelot
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                temp_pdf = tmp.name
            
            temp_doc = fitz.open()
            temp_doc.insert_pdf(page.parent, from_page=page_num-1, to_page=page_num-1)
            temp_doc.save(temp_pdf)
            temp_doc.close()
            
            # Try lattice mode (better for bordered tables)
            tables = []
            try:
                tables = camelot.read_pdf(temp_pdf, flavor='lattice', pages='1')
            except:
                # Fallback to stream mode
                try:
                    tables = camelot.read_pdf(temp_pdf, flavor='stream', pages='1')
                except:
                    pass
            
            for idx, table in enumerate(tables):
                df = self._postprocess_table(table.df)
                
                # Convert Camelot bbox to PyMuPDF coordinates
                bbox = (table.bbox[0], page.rect.height - table.bbox[3], 
                       table.bbox[2], page.rect.height - table.bbox[1])
                
                table_id = f"t_{doc_id}_p{page_num}_cm_{idx}"
                
                schema = TableSchema(
                    table_id=table_id,
                    page=page_num,
                    bbox=bbox,
                    headers=df.columns.tolist(),
                    rows=df.values.tolist(),
                    provenance='camelot'
                )
                
                unit = Unit(
                    unit_id=f"u_{table_id}",
                    doc_id=doc_id,
                    page=page_num,
                    unit_type="table",
                    column="full",
                    bbox=bbox,
                    y0=bbox[1],
                    source="camelot",
                    anchor=f"md://u_{table_id}",
                    content=self._table_to_markdown(df),
                    extra={'table_id': table_id}
                )
                
                results.append((schema, unit))
            
            # Clean up
            if os.path.exists(temp_pdf):
                os.remove(temp_pdf)
                
        except Exception as e:
            logger.warning(f"Camelot failed on page {page_num}: {e}")
        
        return results
    
    def _select_best_tables(self, lattice_tables, stream_tables) -> List:
        """Select best tables from lattice and stream results"""
        if not lattice_tables and not stream_tables:
            return []
        
        if lattice_tables and not stream_tables:
            return lattice_tables
        
        if stream_tables and not lattice_tables:
            return stream_tables
        
        # Compare quality if both exist
        lattice_score = sum(t.parsing_report.get('accuracy', 0) for t in lattice_tables) / max(len(lattice_tables), 1)
        stream_score = sum(t.parsing_report.get('accuracy', 0) for t in stream_tables) / max(len(stream_tables), 1)
        
        if lattice_score >= stream_score:
            return lattice_tables
        return stream_tables
    
    def _extract_tables_pdfplumber(self, page: fitz.Page, page_num: int, 
                                   doc_id: str) -> List[Tuple[TableSchema, Unit]]:
        """Extract tables using pdfplumber as fallback"""
        results = []
        
        if not PDFPLUMBER_AVAILABLE:
            return results
        
        try:
            import pdfplumber
            import tempfile
            
            # Save page as temp PDF
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                temp_pdf = tmp.name
            
            temp_doc = fitz.open()
            temp_doc.insert_pdf(page.parent, from_page=page_num-1, to_page=page_num-1)
            temp_doc.save(temp_pdf)
            temp_doc.close()
            
            # Extract with pdfplumber
            with pdfplumber.open(temp_pdf) as pdf:
                plumber_page = pdf.pages[0]
                tables = plumber_page.extract_tables()
                
                for idx, table_data in enumerate(tables):
                    if not table_data or len(table_data) < 2:
                        continue
                    
                    # Convert to DataFrame
                    df = pd.DataFrame(table_data[1:], columns=table_data[0])
                    df = self._postprocess_table(df)
                    
                    # Try to get actual bbox from pdfplumber table object
                    bbox = None
                    page_rotation = page.rotation if hasattr(page, 'rotation') else 0
                    try:
                        # Try to find the actual table boundaries
                        table_settings = {
                            'vertical_strategy': 'text', 
                            'horizontal_strategy': 'text',
                            'snap_tolerance': 3,
                            'join_tolerance': 3,
                            'edge_min_length': 5,
                            'min_words_vertical': 3,
                            'min_words_horizontal': 1
                        }
                        found_tables = plumber_page.find_tables(table_settings=table_settings)
                        
                        if found_tables and idx < len(found_tables):
                            table_obj = found_tables[idx]
                            # Get the actual bbox from the table object
                            if hasattr(table_obj, 'bbox'):
                                table_bbox = table_obj.bbox
                                # Normalize pdfplumber coordinates to PyMuPDF system
                                bbox = self.normalize_bbox(
                                    table_bbox, 'pdfplumber', 
                                    page.rect.width, page.rect.height, page_rotation
                                )
                                logger.debug(f"Table {idx} normalized bbox: {bbox}")
                        
                        # If bbox is still full width, try to refine it based on content
                        if bbox and (bbox[2] - bbox[0]) >= page.rect.width * 0.8:
                            # Table spans most of page width - try to narrow it down
                            # Analyze the DataFrame to find actual content boundaries
                            if len(df.columns) > 0:
                                # Estimate based on typical table width (about 40% of page for financial tables)
                                content_width = min(len(df.columns) * 80, page.rect.width * 0.45)
                                # Check if table is likely on right side (common for financial data)
                                if any(col.lower() in str(df.columns).lower() for col in ['%', 'index', 'rate', 'price']):
                                    # Right-aligned table
                                    x_start = page.rect.width - content_width - 50
                                else:
                                    # Left-aligned table
                                    x_start = 50
                                    
                                bbox = (x_start, bbox[1], x_start + content_width, bbox[3])
                                logger.debug(f"Refined table {idx} bbox: {bbox}")
                                
                    except Exception as e:
                        logger.debug(f"Error getting table bbox: {e}")
                    
                    # Final fallback with reasonable defaults
                    if not bbox:
                        # Use conservative estimates
                        estimated_width = min(len(df.columns) * 70, page.rect.width * 0.4, 300)
                        # Alternate between left and right
                        x_offset = 50 if idx % 2 == 0 else page.rect.width * 0.55
                        y_base = 100 + (idx * 120)  # Better vertical spacing
                        bbox = (x_offset, y_base, x_offset + estimated_width, y_base + len(df) * 20 + 40)
                    
                    table_id = f"t_{doc_id}_p{page_num}_pb_{idx}"
                    
                    schema = TableSchema(
                        table_id=table_id,
                        page=page_num,
                        bbox=bbox,
                        headers=df.columns.tolist(),
                        rows=df.values.tolist(),
                        fixes={'merged_tokens': 0},
                        provenance='pdfplumber'
                    )
                    
                    unit = Unit(
                        unit_id=f"u_{table_id}",
                        doc_id=doc_id,
                        page=page_num,
                        unit_type="table",
                        column="full",
                        bbox=bbox,
                        y0=bbox[1],
                        source="pdfplumber",
                        anchor=f"md://u_{table_id}",
                        content=self._table_to_markdown(df),
                        extra={'table_id': table_id}
                    )
                    
                    results.append((schema, unit))
            
            # Clean up
            if os.path.exists(temp_pdf):
                os.remove(temp_pdf)
                
        except Exception as e:
            logger.warning(f"pdfplumber failed on page {page_num}: {e}")
        
        return results
    
    def _postprocess_table(self, df: pd.DataFrame) -> pd.DataFrame:
        """Post-process table to fix merged cells and normalize"""
        # Fix merged numerics
        df = self._split_merged_numerics(df)
        
        # Normalize headers
        df = self._normalize_headers(df)
        
        # Remove empty columns
        df = df.loc[:, (df != '').any(axis=0)]
        
        # Trim whitespace
        df = df.map(lambda x: str(x).strip() if pd.notna(x) else '')
        
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
        """Check if bbox overlaps with any exclusion zone - FIXED VERSION"""
        for zone in exclusion_zones:
            # Calculate simple overlap
            x_overlap = max(0, min(bbox[2], zone[2]) - max(bbox[0], zone[0]))
            y_overlap = max(0, min(bbox[3], zone[3]) - max(bbox[1], zone[1]))
            
            # If there's any significant overlap, exclude it
            if x_overlap > 0 and y_overlap > 0:
                overlap_area = x_overlap * y_overlap
                bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                
                # Exclude if more than 10% of text block overlaps with table
                if bbox_area > 0 and (overlap_area / bbox_area) > 0.1:
                    logger.info(f"Text bbox {bbox} excluded by table zone {zone} - overlap: {overlap_area/bbox_area:.2%}")
                    return True
        
        return False
    
    def _horizontal_overlap(self, bbox1: Tuple, bbox2: Tuple) -> float:
        """Calculate horizontal overlap ratio"""
        x0 = max(bbox1[0], bbox2[0])
        x1 = min(bbox1[2], bbox2[2])
        
        if x0 >= x1:
            return 0.0
        
        width1 = bbox1[2] - bbox1[0]
        if width1 == 0:
            return 0.0
        
        return (x1 - x0) / width1
    
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
    
    def _build_paragraphs(self, text_blocks: List[Dict], columns: Dict,
                         page_num: int, doc_id: str) -> List[Unit]:
        """Build paragraph units from text blocks"""
        paragraphs = []
        
        # Group blocks by column
        if columns['type'] == 'two_column':
            left_blocks = []
            right_blocks = []
            
            for block in text_blocks:
                x_center = (block['bbox'][0] + block['bbox'][2]) / 2
                if x_center < columns['boundaries'][0]:
                    left_blocks.append(block)
                else:
                    right_blocks.append(block)
            
            # Process left column
            left_blocks.sort(key=lambda b: b['bbox'][1])  # Sort by Y
            left_paras = self._merge_adjacent_blocks(left_blocks)
            
            for idx, (para_text, para_bbox) in enumerate(left_paras):
                unit = Unit(
                    unit_id=f"u_{doc_id}_p{page_num}_left_{idx}",
                    doc_id=doc_id,
                    page=page_num,
                    unit_type="paragraph",
                    column="left",
                    bbox=para_bbox,
                    y0=para_bbox[1],
                    source="pymupdf",
                    anchor=f"md://u_{doc_id}_p{page_num}_left_{idx}",
                    content=para_text
                )
                paragraphs.append(unit)
            
            # Process right column
            right_blocks.sort(key=lambda b: b['bbox'][1])
            right_paras = self._merge_adjacent_blocks(right_blocks)
            
            for idx, (para_text, para_bbox) in enumerate(right_paras):
                unit = Unit(
                    unit_id=f"u_{doc_id}_p{page_num}_right_{idx}",
                    doc_id=doc_id,
                    page=page_num,
                    unit_type="paragraph",
                    column="right",
                    bbox=para_bbox,
                    y0=para_bbox[1],
                    source="pymupdf",
                    anchor=f"md://u_{doc_id}_p{page_num}_right_{idx}",
                    content=para_text
                )
                paragraphs.append(unit)
        
        else:  # Single column
            text_blocks.sort(key=lambda b: b['bbox'][1])
            merged_paras = self._merge_adjacent_blocks(text_blocks)
            
            for idx, (para_text, para_bbox) in enumerate(merged_paras):
                unit = Unit(
                    unit_id=f"u_{doc_id}_p{page_num}_para_{idx}",
                    doc_id=doc_id,
                    page=page_num,
                    unit_type="paragraph",
                    column="single",
                    bbox=para_bbox,
                    y0=para_bbox[1],
                    source="pymupdf",
                    anchor=f"md://u_{doc_id}_p{page_num}_para_{idx}",
                    content=para_text
                )
                paragraphs.append(unit)
        
        return paragraphs
    
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
    
    def _process_figure(self, fig_block: Dict, page: fitz.Page, page_num: int,
                       doc_id: str, artefacts_dir: Path, exclusion_zones: List[Tuple]) -> Optional[Dict]:
        """Process figure block - placeholder for text-only pipeline"""
        # Text-only pipeline - skip figure processing
        return None
    
    def _ocr_full_page(self, page: fitz.Page, page_num: int, artefacts_dir: Path) -> Optional[str]:
        """OPTIMIZED OCR full page - faster and more accurate"""
        if not TESSERACT_AVAILABLE:
            return None
        
        try:
            # PERFORMANCE OPTIMIZATION 1: Use optimal DPI (300 vs 400)
            # 300 DPI is sweet spot for speed vs quality
            mat = fitz.Matrix(300 / 72.0, 300 / 72.0)
            pix = page.get_pixmap(matrix=mat)
            
            # PERFORMANCE OPTIMIZATION 2: Direct memory OCR (no temp files!)
            # Convert pixmap to PIL Image in memory
            img_data = pix.tobytes("png")
            from PIL import Image
            import io
            
            img = Image.open(io.BytesIO(img_data))
            
            # PERFORMANCE + ACCURACY OPTIMIZATION: Balanced OCR config
            # PSM 6: Uniform block of text (better for structured docs)
            final_text = pytesseract.image_to_string(
                img,
                lang=self.ocr_lang,
                config='--oem 3 --psm 6 -c preserve_interword_spaces=1 -c tessedit_create_hocr=0'
            )
            
            # ACCURACY OPTIMIZATION: Enhanced post-processing
            if final_text:
                final_text = self._post_process_ocr_text(final_text)
            
            return final_text.strip() if final_text else None
            
        except Exception as e:
            logger.warning(f"OCR failed on page {page_num}: {e}")
            return None
    
    def _post_process_ocr_text(self, text: str) -> str:
        """IMPROVED OCR text post-processing - preserving structure"""
        import re
        
        # CONSERVATIVE approach - preserve layout structure
        
        # Fix common OCR mistakes but be more careful
        corrections = {
            # Only fix very obvious mistakes
            'dan': ['daan'],  # Conservative - only clear mistakes
            'yang': ['yano'], 
            'untuk': ['unluk'],
        }
        
        # Apply corrections carefully
        for correct, mistakes in corrections.items():
            for mistake in mistakes:
                text = re.sub(r'\b' + re.escape(mistake) + r'\b', correct, text, flags=re.IGNORECASE)
        
        # CRITICAL: Preserve numbered sections structure
        # Fix pattern: "1.46\n1.47\nTIN atau..." → "1.46 TIN atau...\n1.47 ..."
        lines = text.split('\n')
        processed_lines = []
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # Check if current line is a number/section marker
            if re.match(r'^\d+\.?\d*\.?\s*$', line) and i + 1 < len(lines):
                # Look ahead for the actual content
                next_line = lines[i + 1].strip()
                
                # If next line is also a number, collect all numbers first
                if re.match(r'^\d+\.?\d*\.?\s*$', next_line):
                    numbers = [line]
                    j = i + 1
                    while j < len(lines) and re.match(r'^\d+\.?\d*\.?\s*$', lines[j].strip()):
                        numbers.append(lines[j].strip())
                        j += 1
                    
                    # Now find content lines
                    content_start = j
                    for k, num in enumerate(numbers):
                        if content_start + k < len(lines):
                            content = lines[content_start + k].strip()
                            if content:
                                processed_lines.append(f"{num} {content}")
                            else:
                                processed_lines.append(num)
                        else:
                            processed_lines.append(num)
                    
                    i = content_start + len(numbers) - 1
                else:
                    # Simple case: number followed by content
                    if next_line and not re.match(r'^\d+\.?\d*\.?\s*$', next_line):
                        processed_lines.append(f"{line} {next_line}")
                        i += 1
                    else:
                        processed_lines.append(line)
            else:
                processed_lines.append(line)
            
            i += 1
        
        # Reconstruct text with preserved structure
        text = '\n'.join(processed_lines)
        
        # Minimal cleanup - preserve structure
        text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Remove excessive breaks
        text = re.sub(r'[ \t]+', ' ', text)  # Multiple spaces to single
        text = re.sub(r'^ +| +$', '', text, flags=re.MULTILINE)  # Trim lines
        
        return text
    
    
    def _assemble_markdown(self, units: List[Unit]) -> str:
        """Assemble final markdown from units sorted by position"""
        if not units:
            return ""
        
        # Sort units by page, then y_center (more stable), then column priority, then x0
        def sort_key(unit):
            # Calculate Y center for more stable sorting
            y_center = (unit.bbox[1] + unit.bbox[3]) / 2.0
            
            # Column priority: left first, then single/full, then right
            col_priority = {'left': 0, 'single': 1, 'full': 1, 'right': 2}
            col_order = col_priority.get(unit.column, 1)
            
            # X position for tie-breaking
            x0 = unit.bbox[0]
            
            return (unit.page, y_center, col_order, x0)
        
        sorted_units = sorted(units, key=sort_key)
        
        # Validation: check if sorting makes sense
        for i in range(1, len(sorted_units)):
            prev_unit = sorted_units[i-1]
            curr_unit = sorted_units[i]
            if prev_unit.page == curr_unit.page:
                prev_y = (prev_unit.bbox[1] + prev_unit.bbox[3]) / 2.0
                curr_y = (curr_unit.bbox[1] + curr_unit.bbox[3]) / 2.0
                if prev_y > curr_y + 10:  # Allow 10pt tolerance
                    logger.warning(f"Potential sort issue: unit {prev_unit.unit_id} (y={prev_y:.1f}) before {curr_unit.unit_id} (y={curr_y:.1f})")
        
        # Build markdown
        lines = []
        current_page = 0
        
        for unit in sorted_units:
            # Add page separator
            if unit.page != current_page:
                if current_page > 0:
                    lines.append("\n---\n")
                current_page = unit.page
                lines.append(f"\n## Page {unit.page}\n")
            
            # Add anchor for evidence linking (only if debug mode enabled)
            if getattr(self, 'include_debug_anchors', False):
                lines.append(f"<!-- {unit.anchor} -->")
            
            # Add content based on type
            if unit.unit_type == "paragraph":
                # Check if it's a heading
                if unit.content and unit.content.isupper() and len(unit.content) < 80:
                    lines.append(f"### {unit.content}\n")
                else:
                    lines.append(f"{unit.content}\n")
            
            elif unit.unit_type == "table":
                lines.append(f"\n{unit.content}\n")
            
            elif unit.unit_type == "figure":
                # Text-only pipeline - skip figures
                pass
        
        return '\n'.join(lines)
    
    def _update_progress(self, progress_path: Path, status: str, percent: float, message: str):
        """Update progress file for monitoring"""
        try:
            with open(progress_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'status': status,
                    'percent': percent,
                    'message': message
                }, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to update progress: {e}")


# Public API for Phase-0 extraction
def extract_pdf_to_markdown(
    doc_id: str,
    pdf_path: str,
    out_dir: str,
    original_filename: Optional[str] = None,
    ocr_lang: str = "ind+eng",
    ocr_primary_psm: int = 3,
    ocr_fallback_psm: list = None,
    dpi_fullpage: int = 300,
    zoom_clip: float = 2.0,
    enable_pdfplumber_fallback: bool = True,
    min_table_area_ratio: float = 0.01,
    header_footer_mode: str = "auto",
    header_margin_pct: float = 0.05,
    footer_margin_pct: float = 0.05,
    max_ocr_crops_per_page: int = 12,
    figure_min_area_ratio: float = 0.003,
    column_split_strategy: str = "histogram",
    include_debug_anchors: bool = False,
) -> ExtractionResult:
    """
    Public API for PDF to Markdown extraction with advanced layout analysis.
    
    Args:
        doc_id: Unique document identifier
        pdf_path: Path to input PDF file
        out_dir: Output directory for artifacts
        ocr_lang: OCR language(s), default "ind+eng" for Indonesian+English
        ocr_primary_psm: Primary PSM mode for Tesseract (3=auto)
        ocr_fallback_psm: List of fallback PSM modes, default [6, 11]
        dpi_fullpage: DPI for full page rendering
        zoom_clip: Zoom factor for figure crops
        enable_pdfplumber_fallback: Enable pdfplumber as table fallback
        min_table_area_ratio: Minimum table area ratio to process
        header_footer_mode: "auto" or "margin" for header/footer removal
        header_margin_pct: Top margin percentage for header removal
        footer_margin_pct: Bottom margin percentage for footer removal
        max_ocr_crops_per_page: Maximum figure crops per page for OCR
        figure_min_area_ratio: Minimum figure area ratio to process
        column_split_strategy: "histogram" or "kmeans2" for column detection
    
    Returns:
        ExtractionResult with paths to markdown and metadata files
    """
    if ocr_fallback_psm is None:
        ocr_fallback_psm = [6, 11]
    
    # Create extractor with all parameters
    extractor = PDFExtractorV2(
        ocr_lang=ocr_lang,
        ocr_primary_psm=ocr_primary_psm,
        ocr_fallback_psm=ocr_fallback_psm,
        dpi_fullpage=dpi_fullpage,
        zoom_clip=zoom_clip,
        enable_pdfplumber_fallback=enable_pdfplumber_fallback,
        min_table_area_ratio=min_table_area_ratio,
        header_footer_mode=header_footer_mode,
        header_margin_pct=header_margin_pct,
        footer_margin_pct=footer_margin_pct,
        max_ocr_crops_per_page=max_ocr_crops_per_page,
        figure_min_area_ratio=figure_min_area_ratio,
        column_split_strategy=column_split_strategy,
        include_debug_anchors=include_debug_anchors,
    )
    
    # Run extraction
    if original_filename is None:
        original_filename = Path(pdf_path).name
    return extractor.extract(
        doc_id=doc_id,
        pdf_path=pdf_path,
        out_dir=out_dir,
        original_filename=original_filename,
    )
