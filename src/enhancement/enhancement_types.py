"""
Enhancement Types untuk Financial/Insurance Domain
Fokus pada informasi TERSIRAT bukan tersurat
"""

from enum import Enum
from typing import List, Dict, Any
from pydantic import BaseModel, Field

class EnhancementType(str, Enum):
    """
    Tipe enhancement yang difokuskan pada analisis tersirat
    untuk kebutuhan klien perbankan/asuransi
    """
    # Analisis Numerik & Matematika
    FORMULA_DISCOVERY = "formula_discovery"  # Menemukan rumus perhitungan dari data
    SCENARIO_ANALYSIS = "scenario_analysis"  # Proyeksi skenario yang tidak ada
    PATTERN_RECOGNITION = "pattern_recognition"  # Pola dari data historis
    CALCULATION_METHOD = "calculation_method"  # Cara perhitungan tersirat
    
    # Analisis Bisnis & Regulasi  
    REQUIREMENT_SYNTHESIS = "requirement_synthesis"  # Sintesis persyaratan
    RISK_ANALYSIS = "risk_analysis"  # Analisis risiko tersirat
    BENEFIT_COMPARISON = "benefit_comparison"  # Perbandingan manfaat
    PROCESS_FLOW = "process_flow"  # Alur proses yang tersirat
    
    # Proyeksi & Estimasi
    PROJECTION_MODEL = "projection_model"  # Model proyeksi masa depan
    TREND_ANALYSIS = "trend_analysis"  # Analisis tren dari data
    INTERPOLATION = "interpolation"  # Interpolasi nilai yang tidak ada
    EXTRAPOLATION = "extrapolation"  # Ekstrapolasi ke luar range
    
    # Legacy (akan di-deprecate)
    GLOSSARY = "glossary"  # Hanya untuk backward compatibility
    FAQ = "faq"  # Hanya untuk backward compatibility
    HIGHLIGHT = "highlight"  # Hanya untuk backward compatibility


class EnhancementCandidate(BaseModel):
    """Model untuk kandidat enhancement dengan referensi data"""
    
    enhancement_type: EnhancementType
    title: str = Field(..., description="Judul singkat enhancement")
    target_info: str = Field(..., description="Informasi tersirat yang akan diekstrak")
    rationale: str = Field(..., description="Alasan mengapa enhancement ini penting")
    
    # Referensi ke data source yang HARUS ada
    source_references: List[Dict[str, Any]] = Field(
        ..., 
        description="Referensi ke unit_ids, tables, atau text spans yang dibutuhkan"
    )
    
    # Konteks data yang diperlukan
    required_context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Data konteks seperti tabel, angka, formula yang diperlukan"
    )
    
    # Metadata untuk prioritas
    priority: int = Field(default=5, ge=1, le=10, description="Prioritas 1-10")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0, description="Confidence score")


class EnhancementPriority:
    """
    Prioritas enhancement berdasarkan kebutuhan klien financial
    """
    
    # High Priority - Informasi tersirat kritis
    HIGH_PRIORITY = [
        EnhancementType.FORMULA_DISCOVERY,
        EnhancementType.SCENARIO_ANALYSIS,
        EnhancementType.CALCULATION_METHOD,
        EnhancementType.PROJECTION_MODEL,
        EnhancementType.EXTRAPOLATION
    ]
    
    # Medium Priority - Analisis bisnis
    MEDIUM_PRIORITY = [
        EnhancementType.REQUIREMENT_SYNTHESIS,
        EnhancementType.RISK_ANALYSIS,
        EnhancementType.BENEFIT_COMPARISON,
        EnhancementType.PATTERN_RECOGNITION,
        EnhancementType.INTERPOLATION
    ]
    
    # Low Priority - Supporting info
    LOW_PRIORITY = [
        EnhancementType.PROCESS_FLOW,
        EnhancementType.TREND_ANALYSIS,
        EnhancementType.GLOSSARY,
        EnhancementType.FAQ,
        EnhancementType.HIGHLIGHT
    ]
    
    @classmethod
    def get_priority_score(cls, enhancement_type: EnhancementType) -> int:
        """Get priority score for enhancement type"""
        if enhancement_type in cls.HIGH_PRIORITY:
            return 10
        elif enhancement_type in cls.MEDIUM_PRIORITY:
            return 5
        else:
            return 2


class FinancialDomain:
    """Domain-specific configurations untuk financial/insurance"""
    
    # Keywords yang mengindikasikan kebutuhan formula discovery
    FORMULA_INDICATORS = [
        "premi", "bunga", "return", "yield", "rate", "persentase",
        "perhitungan", "kalkulasi", "formula", "rumus", "estimasi",
        "proyeksi", "simulasi", "skenario", "asumsi"
    ]
    
    # Patterns untuk scenario analysis
    SCENARIO_PATTERNS = [
        r"(\d+)\s*(tahun|bulan|hari)",  # Time periods
        r"usia\s*(\d+)",  # Age scenarios
        r"(Rp|IDR|USD)\s*[\d,.]+",  # Monetary values
        r"(\d+)%",  # Percentages
        r"plan\s*[A-Z]",  # Insurance plans
    ]
    
    # Numerical operations untuk calculation discovery
    CALCULATION_OPS = [
        "kali", "bagi", "tambah", "kurang", "persen",
        "compound", "sederhana", "progresif", "flat",
        "anuitas", "diskonto", "present value", "future value"
    ]
    
    # Document sections yang typically contain implicit info
    IMPLICIT_SECTIONS = [
        "tabel premi", "ilustrasi", "proyeksi", "simulasi",
        "contoh perhitungan", "asumsi", "ketentuan", "syarat",
        "manfaat", "risiko", "biaya", "potongan"
    ]
