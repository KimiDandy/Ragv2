"""
Markdown v2 synthesizer that combines original content with enhancements.
"""

import re
from typing import List, Dict, Any, Optional
from pathlib import Path
import json
from collections import defaultdict
from datetime import datetime

from loguru import logger


class MarkdownSynthesizer:
    """Synthesizes Markdown v2 with enhancements and anchors."""
    
    def __init__(self):
        self.anchor_pattern = re.compile(r'<!-- ref://([^>]+) -->')
    
    def synthesize(
        self,
        doc_id: str,
        markdown_v1_path: str,
        enhancements: List[Dict[str, Any]],
        units_metadata: List[Dict[str, Any]],
        output_path: str
    ) -> Dict[str, Any]:
        """
        Synthesize Markdown v2 with enhancements.
        
        Args:
            doc_id: Document identifier
            markdown_v1_path: Path to original markdown
            enhancements: List of enhancement items
            units_metadata: Units metadata from extraction
            output_path: Output path for Markdown v2
            
        Returns:
            Synthesis metrics
        """
        logger.info(f"Synthesizing Markdown v2 for {doc_id}")
        
        # Load original markdown
        with open(markdown_v1_path, 'r', encoding='utf-8') as f:
            markdown_v1 = f.read()
        
        # Parse markdown to identify page breaks
        pages = self._parse_pages(markdown_v1)
        
        # Group enhancements by page and type
        page_enhancements = self._group_enhancements_by_page(enhancements)
        global_enhancements = self._group_global_enhancements(enhancements)
        
        # Build Markdown v2
        markdown_v2_parts = []
        
        # Add header
        markdown_v2_parts.append(self._create_header(doc_id))
        
        # Process each page
        for page_num, page_content in pages.items():
            # Add original page content
            markdown_v2_parts.append(page_content)
            
            # Add page enhancements if any
            if page_num in page_enhancements:
                page_section = self._create_page_enhancements_section(
                    page_num,
                    page_enhancements[page_num]
                )
                markdown_v2_parts.append(page_section)
        
        # Add global enhancement sections
        if global_enhancements:
            markdown_v2_parts.append("\n\n---\n\n# Global Enhancements\n")
            
            # Add Glossary
            if 'glossary' in global_enhancements:
                markdown_v2_parts.append(
                    self._create_glossary_section(global_enhancements['glossary'])
                )
            
            # Add Highlights
            if 'highlight' in global_enhancements:
                markdown_v2_parts.append(
                    self._create_highlights_section(global_enhancements['highlight'])
                )
            
            # Add FAQ
            if 'faq' in global_enhancements:
                markdown_v2_parts.append(
                    self._create_faq_section(global_enhancements['faq'])
                )
            
            # Add Figure Captions
            if 'caption' in global_enhancements:
                markdown_v2_parts.append(
                    self._create_captions_section(global_enhancements['caption'])
                )
        
        # Add metadata footer
        markdown_v2_parts.append(self._create_footer())
        
        # Combine all parts
        markdown_v2 = '\n'.join(markdown_v2_parts)
        
        # Save Markdown v2
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown_v2)
        
        # Calculate metrics
        metrics = {
            "doc_id": doc_id,
            "total_pages": len(pages),
            "enhanced_pages": len(page_enhancements),
            "total_enhancements": len(enhancements),
            "enhancement_types": self._count_enhancement_types(enhancements),
            "output_path": str(output_path),
            "synthesis_time": datetime.utcnow().isoformat()
        }
        
        logger.info(f"Markdown v2 synthesized: {metrics}")
        
        return metrics
    
    def _parse_pages(self, markdown: str) -> Dict[int, str]:
        """Parse markdown into pages based on page markers."""
        pages = {}
        current_page = 0
        current_content = []
        
        lines = markdown.split('\n')
        
        for line in lines:
            # Check for page marker (e.g., "## Page 1")
            page_match = re.match(r'^##\s+Page\s+(\d+)', line)
            
            if page_match:
                # Save previous page if exists
                if current_content and current_page > 0:
                    pages[current_page] = '\n'.join(current_content)
                
                # Start new page
                current_page = int(page_match.group(1))
                current_content = [line]
            else:
                current_content.append(line)
        
        # Save last page
        if current_content and current_page > 0:
            pages[current_page] = '\n'.join(current_content)
        
        # If no page markers found, treat as single page
        if not pages:
            pages[1] = markdown
        
        return pages
    
    def _group_enhancements_by_page(
        self,
        enhancements: List[Dict[str, Any]]
    ) -> Dict[int, List[Dict[str, Any]]]:
        """Group enhancements by their primary page."""
        page_groups = defaultdict(list)
        
        for enh in enhancements:
            pages = enh.get('pages', [])
            
            # Only include in page section if it's single-page
            if len(pages) == 1:
                page_groups[pages[0]].append(enh)
        
        return dict(page_groups)
    
    def _group_global_enhancements(
        self,
        enhancements: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group enhancements by type for global sections."""
        type_groups = defaultdict(list)
        
        for enh in enhancements:
            # Include all enhancements in global sections
            # Multi-page enhancements are especially suited for global
            enh_type = enh.get('type', 'unknown')
            type_groups[enh_type].append(enh)
        
        return dict(type_groups)
    
    def _create_header(self, doc_id: str) -> str:
        """Create document header."""
        return f"""# Enhanced Document: {doc_id}

*Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}*

> This document has been enhanced with glossary terms, highlights, FAQs, and figure captions.
> All enhancements include precise references to source content.

---

"""
    
    def _create_page_enhancements_section(
        self,
        page_num: int,
        enhancements: List[Dict[str, Any]]
    ) -> str:
        """Create enhancements section for a specific page."""
        if not enhancements:
            return ""
        
        section = [f"\n### 📝 Enhancements for Page {page_num}\n"]
        
        # Group by type for better organization
        by_type = defaultdict(list)
        for enh in enhancements:
            by_type[enh.get('type', 'unknown')].append(enh)
        
        # Add each type
        for enh_type, items in by_type.items():
            type_label = {
                'glossary': '**Definitions:**',
                'highlight': '**Key Points:**',
                'faq': '**Questions:**',
                'caption': '**Figure Notes:**'
            }.get(enh_type, f'**{enh_type.title()}:**')
            
            section.append(f"\n{type_label}")
            
            for item in items:
                section.append(self._format_enhancement_item(item))
        
        return '\n'.join(section)
    
    def _create_glossary_section(self, items: List[Dict[str, Any]]) -> str:
        """Create glossary section."""
        section = ["\n## 📚 Glossary\n"]
        
        # Sort alphabetically by title
        sorted_items = sorted(items, key=lambda x: x.get('title', '').upper())
        
        for item in sorted_items:
            title = item.get('title', 'Term')
            text = item.get('text', '')
            anchors = self._create_anchors(item.get('source_unit_ids', []))
            
            section.append(f"**{title}**: {text} {anchors}")
        
        return '\n\n'.join(section)
    
    def _create_highlights_section(self, items: List[Dict[str, Any]]) -> str:
        """Create highlights section."""
        section = ["\n## 🔍 Key Highlights\n"]
        
        # Group by section if available
        by_section = defaultdict(list)
        for item in items:
            section_name = item.get('section', 'General')
            by_section[section_name].append(item)
        
        for section_name, section_items in by_section.items():
            if len(by_section) > 1:
                section.append(f"\n### {section_name}\n")
            
            for item in section_items:
                text = item.get('text', '')
                anchors = self._create_anchors(item.get('source_unit_ids', []))
                
                # Add calculation details if present
                if item.get('server_calcs'):
                    calcs = item['server_calcs']
                    if 'change' in calcs:
                        change = calcs['change']
                        text += f" (Change: {change['absolute_change']} / {change['percent_change']}%)"
                
                section.append(f"- {text} {anchors}")
        
        return '\n'.join(section)
    
    def _create_faq_section(self, items: List[Dict[str, Any]]) -> str:
        """Create FAQ section."""
        section = ["\n## ❓ Frequently Asked Questions\n"]
        
        for idx, item in enumerate(items, 1):
            title = item.get('title', f'Question {idx}')
            text = item.get('text', '')
            anchors = self._create_anchors(item.get('source_unit_ids', []))
            
            section.append(f"**Q{idx}: {title}**")
            section.append(f"A: {text} {anchors}\n")
        
        return '\n\n'.join(section)
    
    def _create_captions_section(self, items: List[Dict[str, Any]]) -> str:
        """Create figure captions section."""
        section = ["\n## 🖼️ Figure & Table Captions\n"]
        
        # Group by page
        by_page = defaultdict(list)
        for item in items:
            pages = item.get('pages', [0])
            for page in pages:
                by_page[page].append(item)
        
        for page in sorted(by_page.keys()):
            if page > 0:
                section.append(f"\n**Page {page}:**\n")
            
            for item in by_page[page]:
                title = item.get('title', 'Figure')
                text = item.get('text', '')
                anchors = self._create_anchors(item.get('source_unit_ids', []))
                
                section.append(f"- *{title}*: {text} {anchors}")
        
        return '\n'.join(section)
    
    def _format_enhancement_item(self, item: Dict[str, Any]) -> str:
        """Format a single enhancement item."""
        text = item.get('text', '')
        anchors = self._create_anchors(item.get('source_unit_ids', []))
        
        # Format based on type
        enh_type = item.get('type', 'unknown')
        
        if enh_type == 'glossary':
            title = item.get('title', 'Term')
            return f"- **{title}**: {text} {anchors}"
        elif enh_type == 'faq':
            title = item.get('title', 'Question')
            return f"- **Q:** {title}\n  **A:** {text} {anchors}"
        else:
            return f"- {text} {anchors}"
    
    def _create_anchors(self, unit_ids: List[str]) -> str:
        """Create anchor references for source units."""
        if not unit_ids:
            return ""
        
        anchors = []
        for unit_id in unit_ids[:3]:  # Limit to 3 anchors for readability
            anchors.append(f"<!-- ref://{unit_id} -->")
        
        if len(unit_ids) > 3:
            anchors.append(f"<!-- +{len(unit_ids)-3} more -->")
        
        return ' '.join(anchors)
    
    def _create_footer(self) -> str:
        """Create document footer."""
        return f"""

---

## Metadata

- **Enhancement Version**: 2.0
- **Generated**: {datetime.utcnow().isoformat()}
- **Model**: GPT-4.1
- **Process**: Token-window Map-Reduce with micro-batch generation

*Note: All numeric values are sourced directly from the original document or calculated deterministically by the server.*
"""
    
    def _count_enhancement_types(self, enhancements: List[Dict[str, Any]]) -> Dict[str, int]:
        """Count enhancements by type."""
        counts = defaultdict(int)
        
        for enh in enhancements:
            enh_type = enh.get('type', 'unknown')
            counts[enh_type] += 1
        
        return dict(counts)
    
    def save_enhancements_json(
        self,
        doc_id: str,
        planning_result: Dict[str, Any],
        generation_result: Dict[str, Any],
        output_path: str
    ):
        """Save complete enhancements data to JSON."""
        enhancements_data = {
            "doc_id": doc_id,
            "run_id": f"enh_{datetime.utcnow().isoformat()}",
            "windows": planning_result.get('windows', []),
            "candidates": planning_result.get('final_candidates', []),
            "items": generation_result.get('items', []),
            "metrics": {
                "planning": planning_result.get('metrics', {}),
                "generation": generation_result.get('metrics', {}),
                "version": "v2.0"
            }
        }
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(enhancements_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Saved enhancements JSON to {output_path}")
