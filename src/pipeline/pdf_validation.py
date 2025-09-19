"""
Validation and testing suite for PDF extraction quality.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Any, Tuple
from loguru import logger


def validate_extraction_quality(doc_dir: Path) -> Dict[str, Any]:
    """Validate the quality of PDF extraction results."""
    
    metrics = {
        "extraction_quality": {},
        "content_structure": {},
        "metadata_completeness": {},
        "issues": []
    }
    
    try:
        # Load extraction results
        markdown_path = doc_dir / "markdown_v1.md"
        metadata_path = doc_dir / "meta" / "units_metadata.json"
        
        if not markdown_path.exists():
            metrics["issues"].append("Missing markdown_v1.md")
            return metrics
        
        if not metadata_path.exists():
            metrics["issues"].append("Missing units_metadata.json")
            return metrics
        
        markdown_content = markdown_path.read_text(encoding="utf-8")
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        # 1. EXTRACTION QUALITY METRICS
        metrics["extraction_quality"] = _assess_extraction_quality(markdown_content)
        
        # 2. CONTENT STRUCTURE METRICS
        metrics["content_structure"] = _assess_content_structure(markdown_content, metadata)
        
        # 3. METADATA COMPLETENESS
        metrics["metadata_completeness"] = _assess_metadata_completeness(metadata)
        
        # 4. DETECT COMMON ISSUES
        metrics["issues"].extend(_detect_common_issues(markdown_content, metadata))
        
        # 5. OVERALL SCORE
        metrics["overall_score"] = _calculate_overall_score(metrics)
        
    except Exception as e:
        metrics["issues"].append(f"Validation error: {str(e)}")
        logger.error(f"Validation failed: {e}")
    
    return metrics


def _assess_extraction_quality(markdown: str) -> Dict[str, Any]:
    """Assess the quality of text extraction."""
    
    # Basic text metrics
    total_chars = len(markdown)
    total_words = len(markdown.split())
    total_lines = len(markdown.split('\n'))
    
    # Check for merged digits (sign of poor table extraction)
    merged_digits = len(re.findall(r'\d+\.\d+%\d+\.\d+%', markdown))
    merged_numbers = len(re.findall(r'\d{4,}\s*\d{4,}', markdown))
    
    # Check for proper paragraph structure
    empty_lines = markdown.count('\n\n')
    paragraph_ratio = empty_lines / max(total_lines, 1)
    
    # Check for proper heading structure
    headings = re.findall(r'^#{1,3}\s+.+$', markdown, re.MULTILINE)
    heading_count = len(headings)
    
    # Check whitespace quality
    excessive_spaces = len(re.findall(r'  +', markdown))
    whitespace_ratio = excessive_spaces / max(total_words, 1)
    
    return {
        "total_characters": total_chars,
        "total_words": total_words,
        "total_lines": total_lines,
        "heading_count": heading_count,
        "paragraph_ratio": round(paragraph_ratio, 3),
        "merged_digits_found": merged_digits,
        "merged_numbers_found": merged_numbers,
        "excessive_whitespace_ratio": round(whitespace_ratio, 3),
        "quality_score": _calculate_text_quality_score(
            merged_digits, merged_numbers, paragraph_ratio, whitespace_ratio
        )
    }


def _assess_content_structure(markdown: str, metadata: List[Dict]) -> Dict[str, Any]:
    """Assess the logical structure of extracted content."""
    
    # Count content types in metadata
    type_counts = {}
    for unit in metadata:
        unit_type = unit.get("unit_type", "unknown")
        type_counts[unit_type] = type_counts.get(unit_type, 0) + 1
    
    # Check for balanced content distribution
    total_units = len(metadata)
    paragraph_ratio = type_counts.get("paragraph", 0) / max(total_units, 1)
    table_ratio = type_counts.get("table", 0) / max(total_units, 1)
    
    # Check for page distribution
    pages = set(unit.get("page", 0) for unit in metadata)
    page_count = len(pages)
    units_per_page = total_units / max(page_count, 1)
    
    # Check for column awareness
    columns = set(unit.get("column") for unit in metadata if unit.get("column"))
    has_column_detection = len(columns) > 1
    
    # Check for unit IDs (important for RAG)
    has_unit_ids = sum(1 for unit in metadata if unit.get("unit_id")) / max(total_units, 1)
    
    return {
        "total_content_units": total_units,
        "content_type_distribution": type_counts,
        "paragraph_ratio": round(paragraph_ratio, 3),
        "table_ratio": round(table_ratio, 3),
        "page_count": page_count,
        "units_per_page": round(units_per_page, 2),
        "has_column_detection": has_column_detection,
        "unit_id_coverage": round(has_unit_ids, 3),
        "structure_score": _calculate_structure_score(
            paragraph_ratio, table_ratio, has_column_detection, has_unit_ids
        )
    }


def _assess_metadata_completeness(metadata: List[Dict]) -> Dict[str, Any]:
    """Assess completeness of metadata fields."""
    
    if not metadata:
        return {"completeness_score": 0.0, "missing_fields": ["all"]}
    
    required_fields = ["doc_id", "page", "unit_type", "section", "bbox"]
    optional_fields = ["column", "unit_id", "source"]
    
    field_coverage = {}
    for field in required_fields + optional_fields:
        coverage = sum(1 for unit in metadata if unit.get(field) is not None) / len(metadata)
        field_coverage[field] = round(coverage, 3)
    
    # Check bbox validity
    valid_bbox_count = 0
    for unit in metadata:
        bbox = unit.get("bbox")
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            if all(isinstance(x, (int, float)) for x in bbox):
                valid_bbox_count += 1
    
    bbox_validity = valid_bbox_count / len(metadata)
    
    missing_fields = [f for f in required_fields if field_coverage.get(f, 0) < 0.9]
    
    return {
        "field_coverage": field_coverage,
        "bbox_validity": round(bbox_validity, 3),
        "missing_required_fields": missing_fields,
        "completeness_score": _calculate_completeness_score(field_coverage, bbox_validity)
    }


def _detect_common_issues(markdown: str, metadata: List[Dict]) -> List[str]:
    """Detect common extraction issues."""
    
    issues = []
    
    # Issue 1: Merged table cells
    if re.search(r'\d+\.\d+%\d+\.\d+%', markdown):
        issues.append("Merged percentage values detected (table extraction issue)")
    
    # Issue 2: Header/footer repetition
    lines = markdown.split('\n')
    line_counts = {}
    for line in lines:
        if line.strip():
            line_counts[line.strip()] = line_counts.get(line.strip(), 0) + 1
    
    repeated_lines = [line for line, count in line_counts.items() if count > 3 and len(line) < 100]
    if repeated_lines:
        issues.append(f"Potential header/footer repetition: {len(repeated_lines)} repeated lines")
    
    # Issue 3: Empty or very short content units
    empty_units = sum(1 for unit in metadata if not unit.get("section") or len(str(unit.get("section", ""))) < 3)
    if empty_units > len(metadata) * 0.1:  # More than 10% empty
        issues.append(f"Too many empty content units: {empty_units}/{len(metadata)}")
    
    # Issue 4: Missing tables despite table indicators
    table_indicators = len(re.findall(r'\b(table|tabel|chart|data)\b', markdown.lower()))
    actual_tables = sum(1 for unit in metadata if unit.get("unit_type") == "table")
    if table_indicators > 3 and actual_tables == 0:
        issues.append("Table indicators found but no tables extracted")
    
    # Issue 5: Unbalanced content distribution
    pages = set(unit.get("page", 0) for unit in metadata)
    if len(pages) > 1:
        units_per_page = [sum(1 for unit in metadata if unit.get("page") == p) for p in pages]
        if max(units_per_page) > 5 * min(units_per_page):
            issues.append("Highly unbalanced content distribution across pages")
    
    return issues


def _calculate_text_quality_score(merged_digits: int, merged_numbers: int, paragraph_ratio: float, whitespace_ratio: float) -> float:
    """Calculate text quality score (0-1)."""
    
    score = 1.0
    
    # Penalize merged content
    score -= min(0.3, merged_digits * 0.05)
    score -= min(0.2, merged_numbers * 0.02)
    
    # Penalize poor paragraph structure
    if paragraph_ratio < 0.1:  # Too few paragraph breaks
        score -= 0.2
    elif paragraph_ratio > 0.5:  # Too many breaks
        score -= 0.1
    
    # Penalize excessive whitespace
    score -= min(0.2, whitespace_ratio * 2)
    
    return max(0.0, score)


def _calculate_structure_score(paragraph_ratio: float, table_ratio: float, has_columns: bool, unit_id_coverage: float) -> float:
    """Calculate structure quality score (0-1)."""
    
    score = 0.0
    
    # Good paragraph ratio (should be majority)
    if 0.6 <= paragraph_ratio <= 0.9:
        score += 0.3
    elif paragraph_ratio >= 0.4:
        score += 0.2
    
    # Tables present but not overwhelming
    if 0.05 <= table_ratio <= 0.3:
        score += 0.2
    elif table_ratio > 0:
        score += 0.1
    
    # Column detection
    if has_columns:
        score += 0.2
    
    # Unit ID coverage
    score += unit_id_coverage * 0.3
    
    return min(1.0, score)


def _calculate_completeness_score(field_coverage: Dict[str, float], bbox_validity: float) -> float:
    """Calculate metadata completeness score (0-1)."""
    
    required_fields = ["doc_id", "page", "unit_type", "section", "bbox"]
    
    # Average coverage of required fields
    required_coverage = sum(field_coverage.get(f, 0) for f in required_fields) / len(required_fields)
    
    # Bbox validity is critical
    score = (required_coverage * 0.7) + (bbox_validity * 0.3)
    
    return min(1.0, score)


def _calculate_overall_score(metrics: Dict[str, Any]) -> float:
    """Calculate overall extraction quality score (0-1)."""
    
    extraction_score = metrics.get("extraction_quality", {}).get("quality_score", 0)
    structure_score = metrics.get("content_structure", {}).get("structure_score", 0)
    completeness_score = metrics.get("metadata_completeness", {}).get("completeness_score", 0)
    
    # Weighted average
    overall = (extraction_score * 0.4) + (structure_score * 0.3) + (completeness_score * 0.3)
    
    # Penalize critical issues
    critical_issues = len(metrics.get("issues", []))
    penalty = min(0.3, critical_issues * 0.05)
    
    return max(0.0, overall - penalty)


def run_extraction_test(pdf_path: str, expected_results: Dict[str, Any] = None) -> Dict[str, Any]:
    """Run a complete extraction test on a PDF file."""
    
    from .pdf_markdownpp import process_pdf_markdownpp
    
    try:
        # Run extraction
        logger.info(f"Testing extraction on: {pdf_path}")
        result = process_pdf_markdownpp(pdf_path, mode="basic")
        
        # Validate results
        doc_dir = Path(result["output_dir"])
        validation_metrics = validate_extraction_quality(doc_dir)
        
        # Combine results
        test_result = {
            "pdf_path": pdf_path,
            "extraction_result": result,
            "validation_metrics": validation_metrics,
            "test_passed": validation_metrics.get("overall_score", 0) > 0.6,
            "timestamp": "2025-09-19T19:01:51+07:00"
        }
        
        # Compare with expected results if provided
        if expected_results:
            test_result["comparison"] = _compare_with_expected(validation_metrics, expected_results)
        
        return test_result
        
    except Exception as e:
        logger.error(f"Extraction test failed: {e}")
        return {
            "pdf_path": pdf_path,
            "test_passed": False,
            "error": str(e),
            "timestamp": "2025-09-19T19:01:51+07:00"
        }


def _compare_with_expected(actual: Dict[str, Any], expected: Dict[str, Any]) -> Dict[str, Any]:
    """Compare actual results with expected benchmarks."""
    
    comparison = {
        "metrics_comparison": {},
        "threshold_checks": {},
        "regression_detected": False
    }
    
    # Define key metrics to compare
    key_metrics = [
        ("extraction_quality", "quality_score"),
        ("content_structure", "structure_score"),
        ("metadata_completeness", "completeness_score"),
        ("overall_score",)
    ]
    
    for metric_path in key_metrics:
        actual_value = actual
        expected_value = expected
        
        for key in metric_path:
            actual_value = actual_value.get(key, 0) if isinstance(actual_value, dict) else 0
            expected_value = expected_value.get(key, 0) if isinstance(expected_value, dict) else 0
        
        metric_name = "_".join(metric_path)
        comparison["metrics_comparison"][metric_name] = {
            "actual": actual_value,
            "expected": expected_value,
            "difference": actual_value - expected_value,
            "relative_change": ((actual_value - expected_value) / max(expected_value, 0.01)) if expected_value > 0 else 0
        }
        
        # Check for significant regression (>10% drop)
        if actual_value < expected_value * 0.9:
            comparison["regression_detected"] = True
    
    return comparison


if __name__ == "__main__":
    # Example usage
    import sys
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        result = run_extraction_test(pdf_path)
        print(json.dumps(result, indent=2, ensure_ascii=False))
