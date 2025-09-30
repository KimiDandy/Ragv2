# Additional methods for extractor_v2.py - Part 2

def _extract_tables(self, page: fitz.Page, page_num: int, 
                   doc_id: str) -> List[Tuple[TableSchema, Unit]]:
    """Extract tables using Camelot with pdfplumber fallback"""
    results = []
    
    # Try Camelot if available
    if CAMELOT_AVAILABLE:
        try:
            import tempfile
            # Save page as temp PDF for Camelot
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                temp_pdf = tmp.name
            
            temp_doc = fitz.open()
            temp_doc.insert_pdf(page.parent, from_page=page_num-1, to_page=page_num-1)
            temp_doc.save(temp_pdf)
            temp_doc.close()
            
            # Try lattice mode
            lattice_tables = None
            stream_tables = None
            
            try:
                lattice_tables = camelot.read_pdf(temp_pdf, flavor='lattice', pages='1')
            except Exception as e:
                logger.debug(f"Camelot lattice failed: {e}")
            
            # Try stream mode
            try:
                stream_tables = camelot.read_pdf(temp_pdf, flavor='stream', pages='1')
            except Exception as e:
                logger.debug(f"Camelot stream failed: {e}")
            
            # Select best tables
            best_tables = self._select_best_tables(lattice_tables, stream_tables)
            
            for idx, table in enumerate(best_tables):
                table_id = f"t_{doc_id}_p{page_num}_{idx}"
                
                # Post-process table
                df = self._postprocess_table(table.df)
                
                # Get bbox (Camelot bbox is (x1,y1,x2,y2))
                bbox = (table.bbox[0], page.rect.height - table.bbox[3], 
                       table.bbox[2], page.rect.height - table.bbox[1])
                
                # Create schema
                schema = TableSchema(
                    table_id=table_id,
                    page=page_num,
                    bbox=bbox,
                    headers=df.columns.tolist(),
                    rows=df.values.tolist(),
                    fixes={'merged_tokens': self.metrics.get('merged_tokens', 0)},
                    provenance=f'camelot:{table.flavor}'
                )
                
                # Create unit
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
            
            # Clean up temp file
            if os.path.exists(temp_pdf):
                os.remove(temp_pdf)
                
        except Exception as e:
            logger.warning(f"Camelot failed on page {page_num}: {e}")
    
    # Fallback to pdfplumber if no tables found
    if not results and PDFPLUMBER_AVAILABLE and self.enable_pdfplumber_fallback:
        results.extend(self._extract_tables_pdfplumber(page, page_num, doc_id))
    
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
                
                # Get approximate bbox
                bbox = (0, page_num * 100 + idx * 50, page.rect.width, 
                       page_num * 100 + idx * 50 + 100)
                
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
        # Use pandas to_markdown if available
        if hasattr(df, 'to_markdown'):
            return df.to_markdown(index=False)
    except:
        pass
    
    # Manual markdown generation as fallback
    lines = []
    
    # Headers
    headers = '| ' + ' | '.join(str(h) for h in df.columns) + ' |'
    lines.append(headers)
    
    # Separator
    sep = '|' + '|'.join(['-' * (len(str(h)) + 2) for h in df.columns]) + '|'
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
                   doc_id: str, artefacts_dir: Path, 
                   exclusion_zones: List[Tuple]) -> Optional[Dict]:
    """Process a figure block (text-only pipeline - no OCR)"""
    # Since this is a text-only pipeline, we skip figure processing
    return None

def _ocr_full_page(self, page: fitz.Page, page_num: int, 
                  artefacts_dir: Path) -> Optional[str]:
    """OCR full page when no text is found"""
    if not TESSERACT_AVAILABLE:
        return None
    
    try:
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
        logger.warning(f"OCR failed on page {page_num}: {e}")
        return None

def _assemble_markdown(self, units: List[Unit]) -> str:
    """Assemble final markdown from units sorted by position"""
    if not units:
        return ""
    
    # Sort units by page, then y0, then column priority
    def sort_key(unit):
        col_priority = {'left': 0, 'single': 1, 'right': 2, 'full': 3}
        return (unit.page, unit.y0, col_priority.get(unit.column, 99))
    
    sorted_units = sorted(units, key=sort_key)
    
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
        
        # Add anchor for evidence linking
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
