"""
PDF Extraction Module - Phase 1

Professional PDF extraction with table and text recognition.
Generates structured markdown with metadata.
"""

from .extractor import extract_pdf_to_markdown

__all__ = ['extract_pdf_to_markdown']
