"""
Simple windowing for direct enhancement - smaller chunks for faster processing
"""

import tiktoken
from typing import List, Dict, Any
from pydantic import BaseModel


class SimpleWindow(BaseModel):
    """Simple window for direct processing"""
    window_id: str
    text_content: str
    units: List[Dict[str, Any]]
    token_count: int
    
    
class SimpleWindowManager:
    """Create smaller windows for direct enhancement"""
    
    def __init__(self, max_tokens: int = 5000, overlap_tokens: int = 500):
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.encoder = tiktoken.get_encoding("cl100k_base")
    
    def create_windows(
        self,
        doc_id: str,
        markdown_content: str,
        units_metadata: Dict[str, Any]
    ) -> List[SimpleWindow]:
        """Create simple windows from markdown content"""
        
        # Get all units
        all_units = []
        if isinstance(units_metadata, dict):
            # Handle both flat and nested structures
            for key, value in units_metadata.items():
                if isinstance(value, dict) and 'content' in value:
                    all_units.append(value)
                elif isinstance(value, list):
                    all_units.extend([v for v in value if isinstance(v, dict)])
        elif isinstance(units_metadata, list):
            all_units = units_metadata
        
        # Sort units by position if available
        all_units.sort(key=lambda x: (
            x.get('page', 0),
            x.get('bbox', [0, 0])[1] if 'bbox' in x else 0
        ))
        
        # Create windows
        windows = []
        current_units = []
        current_text = []
        current_tokens = 0
        window_count = 0
        
        for unit in all_units:
            unit_text = unit.get('content', '')
            unit_tokens = len(self.encoder.encode(unit_text))
            
            # Check if adding this unit exceeds limit
            if current_tokens + unit_tokens > self.max_tokens and current_units:
                # Create window
                window_count += 1
                windows.append(SimpleWindow(
                    window_id=f"w{window_count}",
                    text_content='\n\n'.join(current_text),
                    units=current_units.copy(),
                    token_count=current_tokens
                ))
                
                # Keep overlap
                if self.overlap_tokens > 0 and current_text:
                    overlap_text = current_text[-2:] if len(current_text) >= 2 else current_text
                    overlap_units = current_units[-2:] if len(current_units) >= 2 else current_units
                    current_text = overlap_text
                    current_units = overlap_units
                    current_tokens = len(self.encoder.encode('\n\n'.join(overlap_text)))
                else:
                    current_text = []
                    current_units = []
                    current_tokens = 0
            
            # Add unit to current window
            current_text.append(unit_text)
            current_units.append(unit)
            current_tokens += unit_tokens
        
        # Add final window
        if current_units:
            window_count += 1
            windows.append(SimpleWindow(
                window_id=f"w{window_count}",
                text_content='\n\n'.join(current_text),
                units=current_units.copy(),
                token_count=current_tokens
            ))
        
        # If no windows created, create one from markdown
        if not windows:
            text = markdown_content[:20000]  # Limit size
            windows.append(SimpleWindow(
                window_id="w1",
                text_content=text,
                units=[],
                token_count=len(self.encoder.encode(text))
            ))
        
        return windows
