"""
Universal Enhancement Types - Adaptive untuk semua jenis dokumen
Tidak terikat pada domain finansial/asuransi spesifik
"""

from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class UniversalEnhancementType(str, Enum):
    """
    Universal enhancement types yang adaptif untuk semua dokumen
    """
    
    # Numerical/Quantitative (untuk dokumen dengan data numerik)
    FORMULA_DISCOVERY = "formula_discovery"
    PROJECTION_ANALYSIS = "projection_analysis"  
    PATTERN_EXTRACTION = "pattern_extraction"
    CALCULATION_METHOD = "calculation_method"
    STATISTICAL_INSIGHT = "statistical_insight"
    
    # Logical/Analytical (untuk dokumen naratif/konseptual)
    IMPLICATION_ANALYSIS = "implication_analysis"
    RELATIONSHIP_MAPPING = "relationship_mapping"
    CAUSE_EFFECT_CHAIN = "cause_effect_chain"
    DECISION_FRAMEWORK = "decision_framework"
    
    # Procedural/Process (untuk dokumen prosedural)
    PROCESS_COMPLETION = "process_completion"
    REQUIREMENT_SYNTHESIS = "requirement_synthesis"
    WORKFLOW_EXTRACTION = "workflow_extraction"
    DEPENDENCY_MAPPING = "dependency_mapping"
    
    # Comparative/Evaluative (untuk analisis perbandingan)
    COMPARISON_MATRIX = "comparison_matrix"
    TRADE_OFF_ANALYSIS = "trade_off_analysis"
    OPTIMIZATION_PATH = "optimization_path"
    SCENARIO_MODELING = "scenario_modeling"
    
    # Risk/Compliance (untuk dokumen regulasi/kebijakan)
    RISK_IDENTIFICATION = "risk_identification"
    COMPLIANCE_MAPPING = "compliance_mapping"
    GAP_ANALYSIS = "gap_analysis"
    MITIGATION_STRATEGY = "mitigation_strategy"
    
    # Knowledge Synthesis (universal)
    CONCEPT_SYNTHESIS = "concept_synthesis"
    SUMMARY_INSIGHT = "summary_insight"
    KEY_TAKEAWAY = "key_takeaway"
    ACTION_ITEM = "action_item"


class DocumentProfile(BaseModel):
    """Profile dokumen untuk menentukan strategi enhancement"""
    
    dominant_type: str = Field(
        ..., 
        description="numerical|textual|procedural|regulatory|mixed"
    )
    
    content_characteristics: Dict[str, Any] = Field(
        default_factory=dict,
        description="Karakteristik konten (has_tables, has_numbers, has_procedures, etc)"
    )
    
    detected_domains: List[str] = Field(
        default_factory=list,
        description="Domain yang terdeteksi (jika ada)"
    )
    
    complexity_level: str = Field(
        default="medium",
        description="low|medium|high"
    )
    
    enhancement_strategy: List[str] = Field(
        default_factory=list,
        description="Recommended enhancement types based on profile"
    )


class AdaptiveEnhancementCandidate(BaseModel):
    """Universal enhancement candidate yang adaptif"""
    
    enhancement_type: UniversalEnhancementType
    title: str
    target_info: str
    rationale: str
    
    # Flexible source references
    source_references: List[Dict[str, Any]]
    
    # Adaptive context based on content type
    required_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Context yang diperlukan (bisa numerical, textual, atau mixed)"
    )
    
    # Universal metadata
    priority: int = Field(default=5, ge=1, le=10)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    applicability: List[str] = Field(
        default_factory=list,
        description="Scenario dimana enhancement ini applicable"
    )


class EnhancementSelector:
    """
    Selects appropriate enhancement types based on document profile
    """
    
    @staticmethod
    def select_enhancement_types(profile: DocumentProfile) -> List[UniversalEnhancementType]:
        """
        Select enhancement types based on document characteristics
        """
        selected = []
        
        # Check content characteristics
        chars = profile.content_characteristics
        
        # Numerical content
        if chars.get('has_tables') or chars.get('has_numbers'):
            selected.extend([
                UniversalEnhancementType.FORMULA_DISCOVERY,
                UniversalEnhancementType.PROJECTION_ANALYSIS,
                UniversalEnhancementType.PATTERN_EXTRACTION,
                UniversalEnhancementType.STATISTICAL_INSIGHT
            ])
        
        # Procedural content  
        if chars.get('has_procedures') or chars.get('has_steps'):
            selected.extend([
                UniversalEnhancementType.PROCESS_COMPLETION,
                UniversalEnhancementType.WORKFLOW_EXTRACTION,
                UniversalEnhancementType.DEPENDENCY_MAPPING
            ])
        
        # Regulatory/Policy content
        if chars.get('has_regulations') or chars.get('has_policies'):
            selected.extend([
                UniversalEnhancementType.COMPLIANCE_MAPPING,
                UniversalEnhancementType.RISK_IDENTIFICATION,
                UniversalEnhancementType.GAP_ANALYSIS
            ])
        
        # Comparative content
        if chars.get('has_comparisons') or chars.get('has_options'):
            selected.extend([
                UniversalEnhancementType.COMPARISON_MATRIX,
                UniversalEnhancementType.TRADE_OFF_ANALYSIS,
                UniversalEnhancementType.SCENARIO_MODELING
            ])
        
        # Always include universal types
        selected.extend([
            UniversalEnhancementType.IMPLICATION_ANALYSIS,
            UniversalEnhancementType.RELATIONSHIP_MAPPING,
            UniversalEnhancementType.KEY_TAKEAWAY
        ])
        
        # Remove duplicates
        return list(set(selected))
    
    @staticmethod
    def prioritize_by_value(
        candidates: List[AdaptiveEnhancementCandidate],
        profile: DocumentProfile
    ) -> List[AdaptiveEnhancementCandidate]:
        """
        Prioritize candidates based on practical value
        """
        
        # Score each candidate
        for candidate in candidates:
            score = candidate.confidence * 10  # Base score
            
            # Boost for enabling new capabilities
            if 'prediction' in candidate.applicability:
                score += 3
            if 'decision' in candidate.applicability:
                score += 2
            if 'compliance' in candidate.applicability:
                score += 2
            
            # Adjust by profile match
            if profile.dominant_type == 'numerical' and 'formula' in candidate.enhancement_type.value:
                score += 2
            elif profile.dominant_type == 'procedural' and 'process' in candidate.enhancement_type.value:
                score += 2
            elif profile.dominant_type == 'regulatory' and 'compliance' in candidate.enhancement_type.value:
                score += 2
            
            candidate.priority = min(10, int(score))
        
        # Sort by priority
        return sorted(candidates, key=lambda x: x.priority, reverse=True)
