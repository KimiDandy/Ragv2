"""
Token-based windowing system for scalable document processing.
"""

import tiktoken
from typing import List, Dict, Any, Tuple, Optional
import json
from pathlib import Path
from dataclasses import dataclass, asdict
import hashlib


@dataclass
class DocumentWindow:
    """Represents a window of the document for processing."""
    window_id: str
    doc_id: str
    window_index: int
    token_span: Tuple[int, int]  # (start_token, end_token)
    pages: List[int]
    unit_ids: List[str]
    text_preview: str
    total_tokens: int
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TokenWindowManager:
    """Manages token-based windowing for documents."""
    
    def __init__(
        self, 
        window_size: int = 10000,
        overlap_size: int = 500,
        model: str = "gpt-4"
    ):
        self.window_size = window_size
        self.overlap_size = overlap_size
        self.encoding = tiktoken.encoding_for_model(model)
    
    def create_windows(
        self,
        doc_id: str,
        units_metadata: List[Dict[str, Any]],
        markdown_path: str
    ) -> List[DocumentWindow]:
        """
        Create token-based windows from document units.
        
        Args:
            doc_id: Document identifier
            units_metadata: List of unit metadata from extraction
            markdown_path: Path to markdown file
            
        Returns:
            List of DocumentWindow objects
        """
        # Load markdown content
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
        
        # Sort units by page and y-position
        sorted_units = sorted(
            units_metadata,
            key=lambda u: (u.get('page', 0), u.get('y0', 0))
        )
        
        windows = []
        current_window_units = []
        current_window_tokens = 0
        current_window_text = []
        window_index = 0
        token_offset = 0
        
        for unit in sorted_units:
            unit_text = unit.get('content', '')
            unit_tokens = len(self.encoding.encode(unit_text))
            
            # Check if adding this unit would exceed window size
            if (current_window_tokens + unit_tokens > self.window_size 
                and current_window_units):
                # Create window
                window = self._create_window(
                    doc_id=doc_id,
                    window_index=window_index,
                    units=current_window_units,
                    text_parts=current_window_text,
                    token_start=token_offset,
                    token_end=token_offset + current_window_tokens
                )
                windows.append(window)
                
                # Handle overlap
                overlap_units, overlap_text, overlap_tokens = self._get_overlap(
                    current_window_units,
                    current_window_text
                )
                
                # Start new window with overlap
                window_index += 1
                token_offset += current_window_tokens - overlap_tokens
                current_window_units = overlap_units
                current_window_text = overlap_text
                current_window_tokens = overlap_tokens
            
            # Add unit to current window
            current_window_units.append(unit)
            current_window_text.append(unit_text)
            current_window_tokens += unit_tokens
        
        # Create final window if there are remaining units
        if current_window_units:
            window = self._create_window(
                doc_id=doc_id,
                window_index=window_index,
                units=current_window_units,
                text_parts=current_window_text,
                token_start=token_offset,
                token_end=token_offset + current_window_tokens
            )
            windows.append(window)
        
        return windows
    
    def _create_window(
        self,
        doc_id: str,
        window_index: int,
        units: List[Dict[str, Any]],
        text_parts: List[str],
        token_start: int,
        token_end: int
    ) -> DocumentWindow:
        """Create a DocumentWindow object."""
        # Extract unique pages and unit_ids
        pages = sorted(set(u.get('page', 0) for u in units))
        unit_ids = [u.get('unit_id') for u in units if u.get('unit_id')]
        
        # Create preview (first 500 chars)
        full_text = ' '.join(text_parts)
        text_preview = full_text[:500] + '...' if len(full_text) > 500 else full_text
        
        # Generate window ID
        window_id = f"w_{doc_id}_{window_index}_{hashlib.md5(full_text.encode()).hexdigest()[:8]}"
        
        return DocumentWindow(
            window_id=window_id,
            doc_id=doc_id,
            window_index=window_index,
            token_span=(token_start, token_end),
            pages=pages,
            unit_ids=unit_ids,
            text_preview=text_preview,
            total_tokens=token_end - token_start
        )
    
    def _get_overlap(
        self,
        units: List[Dict[str, Any]],
        text_parts: List[str]
    ) -> Tuple[List[Dict[str, Any]], List[str], int]:
        """
        Get overlap units and text for the next window.
        
        Returns units and text that should overlap into the next window.
        """
        if not units:
            return [], [], 0
        
        overlap_units = []
        overlap_text = []
        overlap_tokens = 0
        
        # Work backwards to find overlap
        for i in range(len(units) - 1, -1, -1):
            unit_text = text_parts[i] if i < len(text_parts) else ""
            unit_tokens = len(self.encoding.encode(unit_text))
            
            if overlap_tokens + unit_tokens <= self.overlap_size:
                overlap_units.insert(0, units[i])
                overlap_text.insert(0, unit_text)
                overlap_tokens += unit_tokens
            else:
                break
        
        return overlap_units, overlap_text, overlap_tokens
    
    def estimate_windows(self, total_tokens: int) -> int:
        """Estimate the number of windows needed for a document."""
        if total_tokens <= self.window_size:
            return 1
        
        effective_window_size = self.window_size - self.overlap_size
        return ((total_tokens - self.window_size) // effective_window_size) + 1
    
    def save_windows(self, windows: List[DocumentWindow], output_path: str):
        """Save windows to JSON file."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(
                [w.to_dict() for w in windows],
                f,
                ensure_ascii=False,
                indent=2
            )
    
    def load_windows(self, input_path: str) -> List[DocumentWindow]:
        """Load windows from JSON file."""
        with open(input_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return [DocumentWindow(**w) for w in data]
    
    def get_window_summary(self, window: DocumentWindow, units_metadata: List[Dict[str, Any]]) -> str:
        """Generate a brief summary of a window for planning context."""
        # Find units in this window
        window_units = [u for u in units_metadata if u.get('unit_id') in window.unit_ids]
        
        # Get key information
        unit_types = {}
        for unit in window_units:
            unit_type = unit.get('unit_type', 'unknown')
            unit_types[unit_type] = unit_types.get(unit_type, 0) + 1
        
        summary_parts = [
            f"Window {window.window_index + 1}",
            f"Pages {min(window.pages)}-{max(window.pages)}",
            f"{window.total_tokens} tokens"
        ]
        
        for unit_type, count in unit_types.items():
            summary_parts.append(f"{count} {unit_type}")
        
        return " | ".join(summary_parts)
