"""Test script for the new PDF extractor"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.extract.extractor_v2 import extract_pdf_to_markdown
from pathlib import Path

def test_extractor():
    """Test the new extractor with a sample PDF"""
    
    # Use the dummy.pdf that exists in the project
    pdf_path = "dummy.pdf"
    if not Path(pdf_path).exists():
        print(f"Error: {pdf_path} not found")
        return
    
    # Create test output directory
    out_dir = "test_output"
    Path(out_dir).mkdir(exist_ok=True)
    
    # Run extraction
    print("Starting extraction...")
    try:
        result = extract_pdf_to_markdown(
            doc_id="test_doc_001",
            pdf_path=pdf_path,
            out_dir=out_dir,
            ocr_lang="ind+eng",
            ocr_primary_psm=3,
            enable_pdfplumber_fallback=True,
            header_footer_mode="auto",
            column_split_strategy="histogram"
        )
        
        print(f"✓ Extraction completed successfully!")
        print(f"  - Markdown: {result.markdown_path}")
        print(f"  - Metadata: {result.units_meta_path}")
        print(f"  - Artifacts: {result.artefacts_dir}")
        print(f"  - Metrics: {result.metrics_path}")
        
        # Read and display markdown preview
        with open(result.markdown_path, 'r', encoding='utf-8') as f:
            content = f.read()
            print(f"\nMarkdown preview (first 500 chars):")
            print(content[:500])
            
        # Read and display metrics
        import json
        with open(result.metrics_path, 'r', encoding='utf-8') as f:
            metrics = json.load(f)
            print(f"\nExtraction metrics:")
            for key, value in metrics.items():
                print(f"  - {key}: {value}")
                
    except Exception as e:
        print(f"✗ Extraction failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_extractor()
