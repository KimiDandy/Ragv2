# PDF Extractor V2 - Advanced Layout-Aware Extraction System

## Overview
The `extractor_v2.py` implements a sophisticated Map-Sort-Mine architecture for PDF extraction with advanced layout awareness, column detection, and table masking. This system is specifically designed for complex financial documents with multi-column layouts.

## Key Features

### 1. Layout Analysis & Column Detection
- **Histogram-based column detection**: Analyzes X-coordinate distribution to identify two-column layouts
- **K-means clustering alternative**: Optional machine learning approach for column boundary detection
- **Automatic header/footer removal**: Supports both margin-based and auto-detection modes
- **Block-level analysis**: Uses PyMuPDF's rawdict for precise text block positioning

### 2. Advanced Table Extraction
- **Dual-mode Camelot processing**: 
  - Lattice mode for bordered tables
  - Stream mode for borderless tables
- **Automatic quality evaluation**: Selects best extraction method based on accuracy scores
- **pdfplumber fallback**: Ensures robustness when Camelot fails
- **Schema-aware cell splitting**: Fixes merged numeric values (e.g., "3.00%0.50%" → "3.00% | 0.50%")
- **Table masking**: Prevents narrative text from contaminating table areas

### 3. Position-Based Assembly
- **True reading order**: Sorts content by (page, Y-position, column) 
- **Column-aware flow**: Processes left column before right column
- **Anchor generation**: Each unit gets a unique anchor for evidence linking
- **Natural markdown output**: Maintains document structure in the final output

### 4. Adaptive OCR Processing
- **Smart routing**: Full-page OCR only when no text detected
- **Multi-language support**: Indonesian + English (`ind+eng`)
- **Multiple PSM modes**: Primary PSM with fallback options
- **Text-only pipeline**: Compliant with system requirements (no image processing)

### 5. Comprehensive Metadata
- **Unit-level tracking**: Each paragraph/table has:
  - Unique `unit_id`
  - Precise `bbox` coordinates
  - Column assignment (`left`/`right`/`single`/`full`)
  - Source attribution (`pymupdf`/`camelot`/`pdfplumber`/`ocr`)
  - Evidence anchor for RAG linking

## Installation

```bash
# Required dependencies
pip install pymupdf
pip install camelot-py[cv]  # For table extraction
pip install pdfplumber      # Fallback table extraction
pip install pandas          # Table processing
pip install pytesseract     # OCR support (optional)

# Tesseract OCR (Windows)
# Download from: https://github.com/UB-Mannheim/tesseract/wiki
# Default path: C:\Program Files\Tesseract-OCR\
```

## Usage

### Basic Usage

```python
from src.extract.extractor_v2 import extract_pdf_to_markdown

result = extract_pdf_to_markdown(
    doc_id="unique_doc_id",
    pdf_path="path/to/document.pdf",
    out_dir="output_directory"
)

print(f"Markdown: {result.markdown_path}")
print(f"Metadata: {result.units_meta_path}")
```

### Advanced Configuration

```python
result = extract_pdf_to_markdown(
    doc_id="doc_001",
    pdf_path="financial_report.pdf",
    out_dir="artifacts",
    
    # OCR settings
    ocr_lang="ind+eng",              # Indonesian + English
    ocr_primary_psm=3,                # Auto page segmentation
    ocr_fallback_psm=[6, 11],        # Fallback modes
    
    # Rendering settings
    dpi_fullpage=300,                 # DPI for page images
    zoom_clip=2.0,                    # Zoom for figure crops
    
    # Table extraction
    enable_pdfplumber_fallback=True,  # Use pdfplumber if Camelot fails
    min_table_area_ratio=0.01,        # Minimum table size
    
    # Layout analysis
    header_footer_mode="auto",        # Auto-detect headers/footers
    header_margin_pct=0.05,           # Top 5% margin
    footer_margin_pct=0.05,           # Bottom 5% margin
    
    # Figure processing
    max_ocr_crops_per_page=12,        # Max figures to OCR per page
    figure_min_area_ratio=0.003,      # Minimum figure size
    
    # Column detection
    column_split_strategy="histogram" # or "kmeans2"
)
```

## Output Structure

```
output_dir/
└── doc_id/
    ├── markdown_v1.md          # Final markdown output
    ├── conversion_progress.json # Real-time progress tracking
    ├── metrics.json            # Extraction metrics
    ├── meta/
    │   └── units_metadata.json # Detailed unit metadata
    ├── tables.json             # Table schemas (optional)
    ├── pages/                  # Debug page renders
    │   ├── page-1.png
    │   └── page-2.png
    ├── crops/                  # Figure crops (if any)
    └── logs/
        └── extract.log         # Detailed extraction log
```

## Metadata Schema

### units_metadata.json
```json
[
  {
    "unit_id": "u_doc001_p1_left_0",
    "doc_id": "doc001",
    "page": 1,
    "unit_type": "paragraph",
    "column": "left",
    "bbox": [72.0, 100.0, 270.0, 150.0],
    "y0": 100.0,
    "source": "pymupdf",
    "anchor": "md://u_doc001_p1_left_0",
    "content": "Paragraph text content...",
    "extra": {}
  }
]
```

### tables.json
```json
[
  {
    "table_id": "t_doc001_p1_0",
    "page": 1,
    "bbox": [72.0, 200.0, 540.0, 400.0],
    "headers": ["Date", "Value", "Change"],
    "rows": [
      ["11-Feb", "6,950.1", "0.43%"],
      ["12-Feb", "6,980.3", "0.52%"]
    ],
    "fixes": {"merged_tokens": 2},
    "provenance": "camelot:lattice"
  }
]
```

## API Integration

### FastAPI Endpoint Example

```python
from src.extract.extractor_v2 import extract_pdf_to_markdown

@router.post("/convert")
async def convert_pdf(document_id: str, file: UploadFile):
    # Save uploaded file
    pdf_path = save_upload(file)
    
    # Extract with new system
    result = await asyncio.to_thread(
        extract_pdf_to_markdown,
        doc_id=document_id,
        pdf_path=pdf_path,
        out_dir=ARTIFACTS_DIR
    )
    
    return {
        "status": "success",
        "markdown_path": result.markdown_path,
        "metadata_path": result.units_meta_path
    }
```

## Key Improvements Over Previous Version

1. **No narrative-numerical mixing**: Strict table masking prevents text contamination
2. **Proper reading order**: Position-based assembly maintains document flow
3. **Column awareness**: Separates left/right columns in financial documents  
4. **Better table handling**: Dual-mode extraction with quality evaluation
5. **Stable identifiers**: Every unit has a unique ID for evidence linking
6. **Progress tracking**: Real-time conversion progress via JSON file
7. **Comprehensive logging**: Detailed logs for debugging

## Performance Considerations

- **Table extraction**: Camelot can be slow on large tables; pdfplumber fallback helps
- **OCR**: Only runs when needed (no text detected)
- **Memory usage**: Processes one page at a time to minimize memory footprint
- **Parallel processing**: Not implemented (could be added for multi-page documents)

## Troubleshooting

### Camelot not detecting tables
- Ensure `opencv-python` is installed
- Try adjusting table detection parameters
- Check if tables have visible borders (lattice) or consistent spacing (stream)

### OCR not working
- Verify Tesseract is installed and in PATH
- Check language data files are installed (ind.traineddata, eng.traineddata)
- Try different PSM modes for your document type

### Column detection issues
- Switch between `histogram` and `kmeans2` strategies
- Adjust minimum block count thresholds
- Check if document is truly multi-column

## Future Enhancements

- [ ] Parallel page processing for speed
- [ ] Smart table header detection
- [ ] Footnote and caption association
- [ ] Multi-language heading detection
- [ ] Table of contents extraction
- [ ] Form field extraction
