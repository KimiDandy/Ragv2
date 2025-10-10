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
from datetime import datetime

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
        
        # 7) Extract document properties (UNIVERSAL - works for ANY document type)
        self._update_progress(progress_path, "running", 0.75, "Mengekstrak properti dokumen...")
        doc_properties = self._extract_document_properties(all_units, original_filename or "")
        
        # 8) Assemble markdown by position
        self._update_progress(progress_path, "running", 0.8, "Menyusun markdown...")
        markdown_content = self._assemble_markdown(all_units)
        
        # 9) Add YAML frontmatter with extracted properties
        markdown_with_frontmatter = self._add_yaml_frontmatter(markdown_content, doc_properties, total_pages)
        
        # 10) Persist outputs
        markdown_filename = default_markdown_filename("v1", base_name)
        markdown_path = artefacts_dir / markdown_filename
        markdown_path.write_text(markdown_with_frontmatter, encoding='utf-8')
        
        # Also save document properties separately
        properties_path = meta_dir / "document_properties.json"
        properties_path.write_text(json.dumps(doc_properties, indent=2, ensure_ascii=False), encoding='utf-8')
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
        
        # Write metrics with validation
        self.metrics['total_time'] = time.time() - start_time
        self.metrics['total_pages'] = total_pages
        self.metrics['total_units'] = len(all_units)
        self.metrics['total_tables'] = len(all_tables)
        self.metrics['total_figures'] = len(all_figures)
        self.metrics['text_units'] = len([u for u in all_units if u.unit_type == 'paragraph'])
        self.metrics['table_units'] = len([u for u in all_units if u.unit_type == 'table'])
        self.metrics['ocr_units'] = len([u for u in all_units if 'ocr' in u.source])
        self.metrics['markdown_length'] = len(markdown_with_frontmatter)
        self.metrics['has_frontmatter'] = markdown_with_frontmatter.startswith('---')
        
        # Validation warnings
        if self.metrics['text_units'] == 0:
            logger.warning("NO TEXT UNITS extracted - possible extraction failure!")
        if self.metrics['markdown_length'] < 500:
            logger.warning(f"Very short markdown ({self.metrics['markdown_length']} chars) - possible extraction issue!")
        
        metrics_path = artefacts_dir / "metrics.json"
        metrics_path.write_text(json.dumps(dict(self.metrics), indent=2), encoding='utf-8')
        
        # Update progress to complete
        self._update_progress(progress_path, "complete", 1.0, "Selesai")
        
        # COMPREHENSIVE SUMMARY LOG
        logger.info("="*80)
        logger.info("EXTRACTION COMPLETE - SUMMARY")
        logger.info("="*80)
        logger.info(f"⏱️  Total Time: {self.metrics['total_time']:.2f}s")
        logger.info(f"📄  Total Pages: {total_pages}")
        logger.info(f"📦  Total Units: {len(all_units)}")
        logger.info(f"   ├─ Text Units: {self.metrics['text_units']}")
        logger.info(f"   ├─ Table Units: {self.metrics['table_units']}")
        logger.info(f"   └─ OCR Units: {self.metrics['ocr_units']}")
        logger.info(f"📊  Tables Extracted: {len(all_tables)}")
        logger.info(f"🖼️  Figures Detected: {len(all_figures)}")
        logger.info(f"📝  Markdown Length: {self.metrics['markdown_length']:,} chars")
        logger.info(f"✨  Has Frontmatter: {self.metrics['has_frontmatter']}")
        logger.info(f"📋  Document Type: {doc_properties.get('document_type', 'unknown')}")
        logger.info(f"🌐  Language: {doc_properties.get('language', 'unknown')}")
        logger.info(f"📅  Dates Found: {len(doc_properties.get('dates', []))}")
        logger.info("="*80)
        
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
            text_content = block.get('text', '')
            text_length = len(text_content)
            
            # IMPROVED: Pass text for content-aware exclusion
            is_excluded = self._is_in_exclusion_zone(block_bbox, exclusion_zones, text_content)
            
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
        
        # 5) SMART FULL-PAGE SCAN DETECTION
        # Check if this page is primarily a scan (majority of area is images)
        figure_blocks = [b for b in layout['blocks'] if b['type'] == 'figure']
        is_full_page_scan = self._is_full_page_scan(page, figure_blocks, text_blocks)
        
        if is_full_page_scan and TESSERACT_AVAILABLE:
            logger.info(f"Page {page_num}: Detected FULL-PAGE SCAN - using optimized OCR workflow")
            
            # Optimized: OCR entire page at once instead of breaking into crops
            ocr_start = time.time()
            ocr_text = self._ocr_full_page(page, page_num, artefacts_dir)
            ocr_duration = time.time() - ocr_start
            
            if ocr_text and ocr_text.strip():
                ocr_unit = Unit(
                    unit_id=f"u_{doc_id}_p{page_num}_fullscan",
                    doc_id=doc_id,
                    page=page_num,
                    unit_type="paragraph",
                    column="full",
                    bbox=tuple(page.rect),
                    y0=page.rect.y0,
                    source="ocr_fullscan",
                    anchor=f"md://u_{doc_id}_p{page_num}_fullscan",
                    content=ocr_text,
                    extra={'source_type': 'full_page_scan'}
                )
                page_units.append(ocr_unit)
                logger.info(f"Page {page_num}: Full-page OCR completed in {ocr_duration:.2f}s - extracted {len(ocr_text)} chars")
                logger.info(f"Preview: '{ocr_text[:150]}'")
            
            # Skip individual image OCR since we already OCR'd the full page
            
        else:
            # Normal workflow: Extract figures AND perform OCR on images with text
            for fig_idx, fig_block in enumerate(figure_blocks[:self.max_ocr_crops_per_page]):
                # IMPROVED: Process figure for OCR if marked
                if fig_block.get('needs_ocr', False) and TESSERACT_AVAILABLE:
                    ocr_result = self._ocr_image_block(
                        fig_block, page, page_num, doc_id, artefacts_dir
                    )
                    
                    if ocr_result and ocr_result.strip():
                        # Create paragraph unit from OCR'd image
                        ocr_unit = Unit(
                            unit_id=f"u_{doc_id}_p{page_num}_img_ocr_{fig_idx}",
                            doc_id=doc_id,
                            page=page_num,
                            unit_type="paragraph",
                            column="full",
                            bbox=fig_block['bbox'],
                            y0=fig_block['bbox'][1],
                            source="ocr_image",
                            anchor=f"md://u_{doc_id}_p{page_num}_img_ocr_{fig_idx}",
                            content=ocr_result,
                            extra={'source_type': 'image_block', 'block_idx': fig_block.get('block_idx')}
                        )
                        page_units.append(ocr_unit)
                        logger.info(f"OCR from image block: '{ocr_result[:100]}'")
                
                # Also process as figure metadata (for reference)
                figure_data = self._process_figure(
                    fig_block, page, page_num, doc_id, artefacts_dir, exclusion_zones
                )
                if figure_data:
                    page_figures.append(figure_data['metadata'])
        
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
                
                # IMPROVED: Always process images (not just large ones)
                # Store both as figure AND for potential OCR processing
                layout['blocks'].append({
                    'type': 'figure',
                    'bbox': bbox,
                    'block_idx': block_idx,
                    'area_ratio': area_ratio
                })
                
                # CRITICAL: Also mark for OCR if conditions met
                # 1. Small images at top (likely headers/banners)
                # 2. Images within text flow (likely containing text)
                y_pos = bbox[1]
                page_height = page.rect.height
                is_top_region = y_pos < (page_height * 0.20)  # Top 20%
                is_mid_region = (page_height * 0.10) < y_pos < (page_height * 0.90)  # Middle 80%
                
                # Mark for OCR if: top region OR mid-sized image in content area
                should_ocr = (is_top_region or (is_mid_region and area_ratio < 0.3))
                
                if should_ocr:
                    # Add flag for OCR processing in _process_page
                    layout['blocks'][-1]['needs_ocr'] = True
                    logger.info(f"Image at {bbox} marked for OCR (top_region={is_top_region}, area={area_ratio:.3f})")
        
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
        """IMPROVED: Smart header/footer removal with content-aware logic (UNIVERSAL)"""
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
            # IMPROVED: Smart auto detection with semantic analysis
            filtered_blocks = []
            
            for block in layout['blocks']:
                # Always keep non-text blocks (images, figures)
                if block['type'] != 'text':
                    filtered_blocks.append(block)
                    continue
                
                text = block.get('text', '').strip()
                bbox = block['bbox']
                y_pos = bbox[1]
                y_height = bbox[3] - bbox[1]
                text_length = len(text)
                
                # Determine if block is at extremes (top 50pt or bottom 50pt)
                is_at_top = y_pos < 50
                is_at_bottom = y_pos > page_rect.height - 50
                is_at_extreme = is_at_top or is_at_bottom
                
                # If not at extremes, always keep
                if not is_at_extreme:
                    filtered_blocks.append(block)
                    continue
                
                # UNIVERSAL LOGIC: At extremes, use intelligent filtering
                should_keep = self._is_important_header_or_footer(text, text_length, y_height, bbox, page_rect)
                
                if should_keep:
                    logger.info(f"KEEPING important header/footer: '{text[:80]}'")
                    filtered_blocks.append(block)
                else:
                    logger.info(f"Removing header/footer: '{text[:80]}'")
            
            layout['blocks'] = filtered_blocks
        
        return layout
    
    def _is_important_header_or_footer(self, text: str, text_length: int, 
                                       y_height: float, bbox: Tuple, page_rect: fitz.Rect) -> bool:
        """UNIVERSAL: Determine if header/footer text is important (works for ANY document type)"""
        
        # Rule 1: Very short text (< 5 chars) is likely page number
        if text_length < 5 and text.isdigit():
            return False  # Remove page numbers
        
        # Rule 2: Substantial text blocks should be kept (likely content, not header)
        if text_length > 100:
            return True  # Keep substantial text
        
        # Rule 3: Tall text blocks (> 15pt height) are likely content
        if y_height > 15:
            return True  # Keep tall blocks
        
        # Rule 4: Wide text blocks (> 50% page width) are likely content
        bbox_width = bbox[2] - bbox[0]
        page_width = page_rect[2] - page_rect[0]
        width_ratio = bbox_width / page_width if page_width > 0 else 0
        if width_ratio > 0.5:
            return True  # Keep wide blocks
        
        # Rule 5: Text with sentence structure (multiple words, punctuation) is likely content
        word_count = len(text.split())
        has_punctuation = any(c in text for c in '.,;:!?')
        if word_count >= 3 and has_punctuation:
            return True  # Keep structured text
        
        # Rule 6: Text with capitalization patterns (mixed case) is likely content
        # Page numbers and simple headers are usually all lowercase/uppercase
        has_lowercase = any(c.islower() for c in text)
        has_uppercase = any(c.isupper() for c in text)
        if has_lowercase and has_uppercase and word_count >= 2:
            return True  # Keep mixed-case multi-word text
        
        # Rule 7: Text with numbers AND letters (e.g., "13 Februari 2025") is likely important
        has_digits = any(c.isdigit() for c in text)
        has_letters = any(c.isalpha() for c in text)
        if has_digits and has_letters and word_count >= 2:
            return True  # Keep alphanumeric multi-word text (e.g., dates, identifiers)
        
        # Rule 8: Default - if text is > 20 chars and we're unsure, keep it (conservative)
        if text_length > 20:
            return True  # Conservative: keep longer text
        
        # If none of the above, it's likely a page number or simple header
        return False
    
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
    
    def _is_in_exclusion_zone(self, bbox: Tuple, exclusion_zones: List[Tuple], text: str = "") -> bool:
        """IMPROVED: Content-aware exclusion zone checking (UNIVERSAL)"""
        for zone in exclusion_zones:
            # Calculate simple overlap
            x_overlap = max(0, min(bbox[2], zone[2]) - max(bbox[0], zone[0]))
            y_overlap = max(0, min(bbox[3], zone[3]) - max(bbox[1], zone[1]))
            
            # If there's any significant overlap, analyze further
            if x_overlap > 0 and y_overlap > 0:
                overlap_area = x_overlap * y_overlap
                bbox_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                overlap_ratio = overlap_area / bbox_area if bbox_area > 0 else 0
                
                # UNIVERSAL: Determine if this text should be excluded based on content and position
                is_likely_title = self._is_likely_table_title(text, bbox, zone)
                is_likely_caption = self._is_likely_table_caption(text, bbox, zone)
                
                # More lenient threshold for titles/captions
                if is_likely_title or is_likely_caption:
                    threshold = 0.5  # 50% overlap required to exclude titles/captions
                    logger.info(f"Text is likely {'title' if is_likely_title else 'caption'}, using lenient threshold")
                else:
                    threshold = 0.15  # 15% for regular text (balanced)
                
                if overlap_ratio > threshold:
                    logger.info(f"Text bbox {bbox} excluded by table zone {zone} - overlap: {overlap_ratio:.2%}")
                    return True
        
        return False
    
    def _is_likely_table_title(self, text: str, text_bbox: Tuple, table_zone: Tuple) -> bool:
        """UNIVERSAL: Check if text is likely a table title"""
        if not text:
            return False
        
        # Title characteristics (UNIVERSAL across document types):
        # 1. Positioned ABOVE table
        is_above = text_bbox[3] <= table_zone[1] + 5  # Text bottom <= table top (+5pt tolerance)
        
        # 2. Short text (titles are concise)
        is_short = len(text) < 150
        
        # 3. Has title-like formatting (uppercase, titlecase, or bold indicators)
        is_formatted = (
            text.isupper() or  # ALL CAPS
            text.istitle() or  # Title Case
            ':' in text or     # "Table 1:", "Tabel:"
            text.startswith(('Tabel', 'Table', 'Figure', 'Gambar', 'Chart', 'Grafik'))  # Common prefixes
        )
        
        return is_above and is_short and is_formatted
    
    def _is_likely_table_caption(self, text: str, text_bbox: Tuple, table_zone: Tuple) -> bool:
        """UNIVERSAL: Check if text is likely a table caption"""
        if not text:
            return False
        
        # Caption characteristics (UNIVERSAL):
        # 1. Positioned BELOW table
        is_below = text_bbox[1] >= table_zone[3] - 5  # Text top >= table bottom (-5pt tolerance)
        
        # 2. Relatively short (captions are descriptive but concise)
        is_short = len(text) < 200
        
        # 3. Has caption-like content (source, note, etc.)
        is_descriptive = any(
            keyword in text.lower() 
            for keyword in ['source:', 'sumber:', 'note:', 'catatan:', '*', '**', 'keterangan:']
        )
        
        return is_below and (is_short or is_descriptive)
    
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
        """IMPROVED: Smart merge of adjacent text blocks into logical paragraphs"""
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
                # IMPROVED: Smart gap detection
                y_gap = block['bbox'][1] - current_bbox[3]
                current_height = current_bbox[3] - current_bbox[1]
                block_height = block['bbox'][3] - block['bbox'][1]
                avg_height = (current_height + block_height) / 2
                
                # Check horizontal alignment (same column)
                x_overlap = min(current_bbox[2], block['bbox'][2]) - max(current_bbox[0], block['bbox'][0])
                x_width = max(current_bbox[2], block['bbox'][2]) - min(current_bbox[0], block['bbox'][0])
                horizontal_similarity = x_overlap / x_width if x_width > 0 else 0
                
                # UNIVERSAL MERGE CRITERIA:
                # 1. Small gap (< 20pt or < 0.5x line height)
                # 2. Similar horizontal position (>70% overlap)
                # 3. No major formatting change (similar heights)
                
                should_merge = (
                    y_gap < 20 and  # Close vertically
                    horizontal_similarity > 0.7 and  # Same column
                    abs(block_height - current_height) < avg_height * 0.5  # Similar size
                )
                
                # Special case: Don't merge if current ends with sentence-ending punctuation AND gap is significant
                if current_text:
                    last_text = current_text[-1].strip()
                    ends_with_punctuation = last_text and last_text[-1] in '.!?'
                    if ends_with_punctuation and y_gap > avg_height * 0.3:
                        should_merge = False
                
                if should_merge:
                    # Merge with smart spacing
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
    
    def _is_full_page_scan(self, page: fitz.Page, figure_blocks: List[Dict], text_blocks: List[Dict]) -> bool:
        """
        UNIVERSAL: Detect if page is a full-page scan (mostly images)
        
        Criteria:
        1. Image area > 70% of page area
        2. Few or no extractable text blocks
        3. Multiple overlapping/adjacent images covering page
        
        Returns True if page should be OCR'd as a whole instead of per-image
        """
        if not figure_blocks:
            return False
        
        page_area = page.rect.width * page.rect.height
        total_image_area = 0
        
        for fig in figure_blocks:
            bbox = fig['bbox']
            img_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            total_image_area += img_area
        
        image_coverage = total_image_area / page_area if page_area > 0 else 0
        
        # Criteria evaluation
        high_image_coverage = image_coverage > 0.70  # 70% of page is images
        few_text_blocks = len(text_blocks) < 3       # Very few text blocks
        many_images = len(figure_blocks) >= 5        # Many small images (fragmented scan)
        
        # Decision logic
        is_scan = (
            high_image_coverage or  # Mostly images
            (few_text_blocks and len(figure_blocks) >= 2)  # Little text + multiple images
        )
        
        if is_scan:
            logger.info(f"Full-page scan detected: image_coverage={image_coverage:.2%}, "
                       f"text_blocks={len(text_blocks)}, figure_blocks={len(figure_blocks)}")
        
        return is_scan
    
    def _ocr_image_block(self, fig_block: Dict, page: fitz.Page, page_num: int,
                        doc_id: str, artefacts_dir: Path) -> Optional[str]:
        """UNIVERSAL: OCR text from image blocks (headers, banners, embedded images)"""
        if not TESSERACT_AVAILABLE:
            return None
        
        try:
            bbox = fig_block['bbox']
            logger.info(f"OCR processing image at {bbox}")
            
            # Crop image with high zoom for better OCR accuracy
            zoom = 2.5  # Higher zoom for small text
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, clip=fitz.Rect(bbox))
            
            # Save crop for debugging
            crop_filename = f"page{page_num}_img_ocr_{int(bbox[0])}_{int(bbox[1])}.png"
            crop_path = artefacts_dir / "crops" / crop_filename
            pix.save(str(crop_path))
            
            # Convert to PIL Image for preprocessing
            img_data = pix.tobytes("png")
            from PIL import Image, ImageEnhance, ImageFilter
            import io
            
            img = Image.open(io.BytesIO(img_data))
            
            # PREPROCESSING for better OCR (UNIVERSAL approach)
            # 1. Convert to grayscale
            if img.mode != 'L':
                img = img.convert('L')
            
            # 2. Enhance contrast (helps with faded text)
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(1.5)
            
            # 3. Sharpen (helps with blurry text)
            img = img.filter(ImageFilter.SHARPEN)
            
            # Try multiple OCR configurations (UNIVERSAL - works for any language/layout)
            best_result = None
            max_confidence = 0
            
            configs = [
                ('--oem 3 --psm 7', 'single_line'),      # Best for headers/banners
                ('--oem 3 --psm 6', 'uniform_block'),    # Best for paragraphs
                ('--oem 3 --psm 11', 'sparse_text'),     # Best for scattered text
                ('--oem 3 --psm 13', 'raw_line'),        # Best for single raw line
            ]
            
            for config, mode in configs:
                try:
                    result = pytesseract.image_to_string(
                        img, 
                        lang=self.ocr_lang,
                        config=config
                    )
                    
                    if result:
                        result = self._post_process_ocr_text(result)
                        # Estimate confidence (more meaningful text = better)
                        confidence = len(result.strip())
                        
                        if confidence > max_confidence:
                            max_confidence = confidence
                            best_result = result
                            logger.info(f"OCR mode '{mode}' got {confidence} chars: '{result[:80]}'")
                
                except Exception as e:
                    logger.debug(f"OCR mode '{mode}' failed: {e}")
            
            if best_result:
                logger.info(f"Best OCR result ({max_confidence} chars): '{best_result[:150]}'")
                return best_result
            
            return None
        
        except Exception as e:
            logger.warning(f"Image OCR failed: {e}")
            return None
    
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
    
    def _extract_document_properties(self, all_units: List[Unit], original_filename: str) -> Dict[str, Any]:
        """UNIVERSAL: Extract structured properties from document (works for ANY document type)"""
        
        # Combine all text content
        text_units = [u for u in all_units if u.unit_type == 'paragraph' and u.content]
        full_text = ' '.join(u.content for u in text_units)
        
        properties = {
            'original_filename': original_filename,
            'extraction_timestamp': datetime.now().isoformat(),
            'dates': [],
            'temporal_references': [],
            'numbers': [],
            'urls': [],
            'emails': [],
            'document_type': 'unknown',
            'language': 'unknown',
            'has_tables': False,
            'has_images': False,
        }
        
        # Check for tables and images
        properties['has_tables'] = any(u.unit_type == 'table' for u in all_units)
        properties['has_images'] = any(u.source in ['ocr_image', 'ocr'] for u in all_units)
        
        # UNIVERSAL DATE EXTRACTION (multiple formats)
        date_patterns = [
            # Indonesian dates: "13 Februari 2025", "17 Maret 2024"
            r'\b(\d{1,2})\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+(\d{4})\b',
            # With day names: "Kamis, 13 Februari 2025"
            r'\b(Senin|Selasa|Rabu|Kamis|Jumat|Sabtu|Minggu),?\s+(\d{1,2})\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+(\d{4})\b',
            # English dates: "January 13, 2025", "March 17, 2024"
            r'\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b',
            # ISO format: "2025-02-13"
            r'\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b',
            # Short format: "13/02/2025", "17-03-2024"
            r'\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b',
        ]
        
        for pattern in date_patterns:
            matches = re.findall(pattern, full_text, re.IGNORECASE)
            for match in matches:
                if isinstance(match, tuple):
                    date_str = ' '.join(str(m) for m in match if m)
                else:
                    date_str = str(match)
                properties['dates'].append(date_str)
        
        # Deduplicate dates
        properties['dates'] = list(set(properties['dates']))[:20]  # Top 20 dates
        
        # UNIVERSAL TEMPORAL REFERENCES
        temporal_patterns = [
            r'\b(Q[1-4]\s+\d{4})\b',  # "Q1 2025"
            r'\b(FY\s*\d{4})\b',  # "FY2025"
            r'\b(tahun\s+\d{4})\b',  # "tahun 2025"
            r'\b(year\s+\d{4})\b',  # "year 2025"
        ]
        
        for pattern in temporal_patterns:
            matches = re.findall(pattern, full_text, re.IGNORECASE)
            properties['temporal_references'].extend(matches)
        
        properties['temporal_references'] = list(set(properties['temporal_references']))[:10]
        
        # UNIVERSAL NUMBER EXTRACTION (percentages, currency, large numbers)
        number_patterns = [
            r'\b(\d+[,.]\d+)%',  # Percentages: "5.75%"
            r'\b(\d{1,3}(?:[,.]\d{3})*(?:[,.]\d+)?)\b',  # Numbers with separators
        ]
        
        for pattern in number_patterns:
            matches = re.findall(pattern, full_text)
            properties['numbers'].extend(matches)
        
        # Deduplicate and limit
        properties['numbers'] = list(set(properties['numbers']))[:30]
        
        # UNIVERSAL URL EXTRACTION
        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
        properties['urls'] = list(set(re.findall(url_pattern, full_text, re.IGNORECASE)))[:10]
        
        # UNIVERSAL EMAIL EXTRACTION
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        properties['emails'] = list(set(re.findall(email_pattern, full_text)))[:10]
        
        # UNIVERSAL DOCUMENT TYPE CLASSIFICATION (keyword-based)
        doc_type_keywords = {
            'financial_report': ['laporan keuangan', 'financial report', 'balance sheet', 'income statement', 'neraca', 'laba rugi'],
            'market_insight': ['market insight', 'market analysis', 'analisis pasar', 'daily market'],
            'product_info': ['product', 'produk', 'product focus', 'product information'],
            'legal_document': ['kontrak', 'contract', 'perjanjian', 'agreement', 'legal', 'hukum'],
            'research_paper': ['abstract', 'abstrak', 'methodology', 'metodologi', 'research', 'penelitian'],
            'proposal': ['proposal', 'usulan', 'penawaran'],
            'report': ['report', 'laporan'],
            'presentation': ['presentation', 'presentasi', 'slide'],
        }
        
        text_lower = full_text.lower()
        for doc_type, keywords in doc_type_keywords.items():
            if any(keyword in text_lower for keyword in keywords):
                properties['document_type'] = doc_type
                break
        
        # UNIVERSAL LANGUAGE DETECTION (simple heuristic)
        indonesian_words = ['dan', 'yang', 'untuk', 'dengan', 'pada', 'adalah', 'dari', 'ini', 'dalam']
        english_words = ['and', 'the', 'for', 'with', 'this', 'that', 'from', 'are', 'was', 'were']
        
        id_count = sum(1 for word in indonesian_words if f' {word} ' in f' {text_lower} ')
        en_count = sum(1 for word in english_words if f' {word} ' in f' {text_lower} ')
        
        if id_count > en_count:
            properties['language'] = 'indonesian'
        elif en_count > id_count:
            properties['language'] = 'english'
        else:
            properties['language'] = 'mixed'
        
        logger.info(f"Extracted document properties: type={properties['document_type']}, "
                   f"language={properties['language']}, dates={len(properties['dates'])}, "
                   f"tables={properties['has_tables']}, images={properties['has_images']}")
        
        return properties
    
    def _add_yaml_frontmatter(self, markdown_content: str, properties: Dict[str, Any], total_pages: int) -> str:
        """Add YAML frontmatter to markdown with extracted properties"""
        try:
            import yaml
            yaml_available = True
        except ImportError:
            yaml_available = False
            logger.warning("PyYAML not available, using manual YAML generation")
        
        # Prepare frontmatter data (NO extraction_date - not useful when batch processing)
        frontmatter = {
            'title': properties.get('original_filename', 'Untitled Document').replace('.pdf', ''),
            'document_type': properties.get('document_type', 'unknown'),
            'language': properties.get('language', 'unknown'),
            'total_pages': total_pages,
            'has_tables': properties.get('has_tables', False),
            'has_images': properties.get('has_images', False),
        }
        
        # Add dates if available
        if properties.get('dates'):
            frontmatter['dates'] = properties['dates'][:5]  # Top 5 dates in frontmatter
        
        # Add temporal references if available
        if properties.get('temporal_references'):
            frontmatter['temporal_references'] = properties['temporal_references'][:3]
        
        # Generate YAML string
        if yaml_available:
            try:
                yaml_str = yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False, sort_keys=False)
            except:
                yaml_str = self._manual_yaml_generation(frontmatter)
        else:
            yaml_str = self._manual_yaml_generation(frontmatter)
        
        # Add document footer/closer for clear boundaries
        footer = self._generate_document_footer(properties, total_pages)
        
        return f"---\n{yaml_str}---\n\n{markdown_content}\n\n{footer}"
    
    def _manual_yaml_generation(self, data: Dict[str, Any]) -> str:
        """Manual YAML generation fallback"""
        lines = []
        for key, value in data.items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                for item in value:
                    lines.append(f"  - {item}")
            elif isinstance(value, bool):
                lines.append(f"{key}: {str(value).lower()}")
            elif value is None:
                lines.append(f"{key}: null")
            else:
                # Escape special characters
                if isinstance(value, str) and (':' in value or '#' in value):
                    value = f'"{value}"'
                lines.append(f"{key}: {value}")
        return '\n'.join(lines) + '\n'
    
    def _generate_document_footer(self, properties: Dict[str, Any], total_pages: int) -> str:
        """Generate document footer/closer for clear boundaries when documents are concatenated"""
        footer_lines = [
            "---",
            "",
            "## 📄 End of Document",
            "",
            f"**Document:** {properties.get('original_filename', 'Unknown')}",
            f"**Type:** {properties.get('document_type', 'unknown')}",
            f"**Language:** {properties.get('language', 'unknown')}",
            f"**Pages:** {total_pages}",
        ]
        
        # Add additional metadata if available
        if properties.get('dates'):
            footer_lines.append(f"**Key Dates:** {', '.join(properties['dates'][:3])}")
        
        if properties.get('has_tables'):
            footer_lines.append(f"**Contains Tables:** Yes")
        
        footer_lines.extend([
            "",
            "---",
        ])
        
        return '\n'.join(footer_lines)
    
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
