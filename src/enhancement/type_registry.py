"""
Enhancement Type Registry

Loads enhancement type definitions from YAML configuration.
Provides clean API for accessing types, categories, and recommendations.

This replaces all hardcoded enhancement types with a flexible,
extensible, configuration-based system.
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
import yaml
from dataclasses import dataclass
from loguru import logger


@dataclass
class EnhancementType:
    """Single enhancement type definition"""
    id: str
    category: str
    name: str
    name_en: str
    description: str
    applicable_domains: List[str]
    default_priority: int
    default_enabled: bool
    prompt_instructions: str
    examples: Dict[str, str]
    
    def is_applicable_for_domain(self, domain: str) -> bool:
        """Check if this type is applicable for given domain"""
        return "all" in self.applicable_domains or domain in self.applicable_domains
    
    def get_example_for_domain(self, domain: str) -> Optional[str]:
        """Get domain-specific example if available"""
        return self.examples.get(domain)


@dataclass
class EnhancementCategory:
    """Category grouping for UI"""
    id: str
    name: str
    name_en: str
    description: str
    icon: str
    display_order: int


class EnhancementTypeRegistry:
    """
    Registry for enhancement types - loads from YAML configuration
    
    Benefits:
    - Zero hardcoding in code
    - Easy to extend (add type = add YAML block)
    - Single source of truth
    - Version controllable
    - Can be edited by non-programmers
    """
    
    def __init__(self, registry_path: Optional[Path] = None):
        """Initialize registry from YAML file"""
        if registry_path is None:
            # Default location
            registry_path = Path(__file__).parent / "types_registry.yaml"
        
        self.registry_path = registry_path
        self.categories: Dict[str, EnhancementCategory] = {}
        self.types: Dict[str, EnhancementType] = {}
        self.domain_recommendations: Dict[str, Dict[str, List[str]]] = {}
        self.metadata: Dict[str, Any] = {}
        
        self._load_registry()
    
    def _load_registry(self):
        """Load types from YAML"""
        try:
            if not self.registry_path.exists():
                raise FileNotFoundError(f"Registry file not found: {self.registry_path}")
            
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            
            # Load metadata
            self.metadata = data.get('metadata', {})
            
            # Load categories
            for cat_data in data.get('categories', []):
                category = EnhancementCategory(
                    id=cat_data['id'],
                    name=cat_data['name'],
                    name_en=cat_data.get('name_en', cat_data['name']),
                    description=cat_data['description'],
                    icon=cat_data.get('icon', '📄'),
                    display_order=cat_data.get('display_order', 99)
                )
                self.categories[category.id] = category
            
            # Load enhancement types
            for type_data in data.get('enhancement_types', []):
                enh_type = EnhancementType(
                    id=type_data['id'],
                    category=type_data['category'],
                    name=type_data['name'],
                    name_en=type_data.get('name_en', type_data['name']),
                    description=type_data['description'],
                    applicable_domains=type_data.get('applicable_domains', ['all']),
                    default_priority=type_data.get('default_priority', 5),
                    default_enabled=type_data.get('default_enabled', False),
                    prompt_instructions=type_data.get('prompt_instructions', ''),
                    examples=type_data.get('examples', {})
                )
                self.types[enh_type.id] = enh_type
            
            # Load domain recommendations
            self.domain_recommendations = data.get('domain_recommendations', {})
            
            logger.info(f"✓ Enhancement Type Registry loaded: {len(self.categories)} categories, {len(self.types)} types (version {self.metadata.get('version', 'unknown')})")
            
        except Exception as e:
            logger.error(f"Failed to load enhancement type registry: {e}")
            raise
    
    def get_all_categories(self) -> List[EnhancementCategory]:
        """Get all categories sorted by display order"""
        return sorted(self.categories.values(), key=lambda x: x.display_order)
    
    def get_category(self, category_id: str) -> Optional[EnhancementCategory]:
        """Get specific category"""
        return self.categories.get(category_id)
    
    def get_types_by_category(self, category_id: str) -> List[EnhancementType]:
        """Get all types in a category"""
        return [t for t in self.types.values() if t.category == category_id]
    
    def get_type(self, type_id: str) -> Optional[EnhancementType]:
        """Get specific enhancement type"""
        return self.types.get(type_id)
    
    def get_all_types(self) -> List[EnhancementType]:
        """Get all enhancement types"""
        return list(self.types.values())
    
    def get_recommended_types_for_domain(self, domain: str) -> Dict[str, List[str]]:
        """
        Get recommended enhancement types for a domain
        
        Returns:
            Dict with keys: auto_suggest, optional, not_recommended
        """
        recommendations = self.domain_recommendations.get(domain)
        if not recommendations:
            # Fallback to default recommendations
            recommendations = self.domain_recommendations.get('default', {
                'auto_suggest': ['executive_summary', 'implication_analysis', 'pattern_recognition'],
                'optional': ['section_summary'],
                'not_recommended': []
            })
        return recommendations
    
    def build_dynamic_system_prompt(
        self, 
        selected_type_ids: List[str], 
        domain_hint: Optional[str] = None
    ) -> str:
        """
        Build dynamic system prompt based on selected enhancement types
        
        This is the KEY METHOD that replaces hardcoded DIRECT_ENHANCEMENT_SYSTEM_PROMPT
        with dynamically generated prompts based on user selection.
        
        Args:
            selected_type_ids: List of enhancement type IDs selected by user
            domain_hint: Optional domain hint for better examples
            
        Returns:
            Complete system prompt string
        """
        
        # Base prompt (domain-agnostic, universal, production-ready)
        base_prompt = """Anda adalah AI Document Intelligence Specialist dengan kemampuan analisis mendalam lintas domain untuk mengekstrak informasi tersirat dari dokumen apapun.

## MISI UTAMA
Menganalisis dokumen secara komprehensif untuk mengungkap informasi tersirat, pola tersembunyi, hubungan tidak langsung, dan insight strategis yang membutuhkan inferensi mendalam - BUKAN sekedar merangkum yang sudah eksplisit.

## PRINSIP KERJA UNIVERSAL (DOMAIN-AGNOSTIC)

### 1. FOKUS PADA INFORMASI TERSIRAT
**DILARANG:**
- ❌ Menyalin atau merangkum informasi yang sudah eksplisit
- ❌ Membuat enhancement yang obvious atau trivial
- ❌ Generalisasi tanpa evidence spesifik dari dokumen
- ❌ Menambahkan informasi yang tidak ada di dokumen

**WAJIB:**
- ✅ Ekstrak insight yang membutuhkan analisis dan inferensi
- ✅ Temukan hubungan, pola, implikasi tidak langsung
- ✅ Identifikasi konsekuensi dan risiko tersembunyi
- ✅ Berikan value-add intelligence beyond what's written

### 2. UNIVERSAL ANALYTICAL FRAMEWORK
**Setiap enhancement WAJIB mencakup 4 komponen:**

1. **Root Analysis (WHY)** - Mengapa informasi ini penting dan relevan? Apa konteks dan signifikansinya?

2. **Practical Application (HOW)** - Bagaimana informasi ini bisa digunakan? Apa use case konkret dan real-world application?

3. **Decision Support (WHAT)** - Keputusan atau action apa yang didukung? Apa pilihan atau opsi yang tersedia?

4. **Risk/Opportunity Assessment (IMPACT)** - Risiko apa yang perlu diwaspadai? Peluang apa yang bisa dimanfaatkan? Mitigasi apa yang diperlukan?

### 3. GENERATIVE INTELLIGENCE (CRITICAL!)
Ketika menganalisis dokumen atau menjawab pertanyaan:
- **JANGAN** katakan "data tidak tersedia" jika informasi bisa di-derive
- **BUAT** proyeksi/analisis reasonable berdasarkan data yang ada
- **GUNAKAN** extrapolation, interpolation, pattern-based inference
- **JELASKAN** asumsi, metode, dan limitasi secara transparan
- **BERIKAN** confidence level yang appropriate (0.0-1.0)

**Contoh Mindset:**
❌ "Dokumen tidak memiliki data 10 tahun"
✅ "Berdasarkan pola 3-5 tahun (CAGR 7.2%), proyeksi 10 tahun: Rp X miliar (confidence: 0.75, asumsi: pertumbuhan stabil, no major disruption)"

### 4. DOMAIN-ADAPTIVE INTELLIGENCE
**Deteksi domain dari konten dokumen dan aplikasikan expertise yang sesuai:**

- **Dokumen finansial/banking** → Formula, trend analysis, risk assessment, projections
- **Dokumen legal/kebijakan** → Implications, requirements synthesis, compliance, penalties
- **Dokumen operasional/teknis** → Process mapping, dependencies, optimization, capacity planning
- **Dokumen research/strategic** → Pattern recognition, comparative analysis, hypothesis, recommendations

**PENTING:** Anda TIDAK terbatas pada domain-domain di atas. Adaptasi dengan konten aktual dokumen!

### 5. OUTPUT QUALITY STANDARDS
- **Minimum 150 kata** per enhancement (lebih panjang = lebih baik jika berkualitas)
- **Bahasa Indonesia** yang professional, precise, dan actionable
- **Struktur 4-komponen**: Root → Application → Decision → Impact
- **Evidence-based**: Reference specific data points, numbers, facts dari dokumen
- **Quantitative analysis**: Gunakan angka, percentages, calculations jika applicable
- **Practical value**: Setiap enhancement harus lulus "So what?" test

"""
        
        # Get selected types and build type-specific instructions
        selected_types = [self.get_type(tid) for tid in selected_type_ids if tid in self.types]
        
        if selected_types:
            base_prompt += "\n## ENHANCEMENT TYPES YANG DIPILIH USER (WAJIB DIGUNAKAN SEMUA!)\n\n"
            base_prompt += f"User telah memilih {len(selected_types)} enhancement types berikut.\n"
            base_prompt += "🔥 **CRITICAL ENFORCEMENT**: Anda WAJIB generate enhancement untuk SETIAP type di bawah ini!\n"
            base_prompt += f"🔥 **DISTRIBUTION TARGET**: Buat minimal 2-3 enhancement per type → total minimal {len(selected_types) * 2} enhancement\n\n"
            base_prompt += "**VALID TYPE IDs (HANYA gunakan yang ini!):**\n"
            base_prompt += "- " + "\n- ".join([f"`{t.id}` ({t.name})" for t in selected_types]) + "\n\n"
            
            for i, enh_type in enumerate(selected_types, 1):
                base_prompt += f"### {i}. {enh_type.name.upper()} (ID: `{enh_type.id}`)\n"
                base_prompt += f"**Deskripsi:** {enh_type.description}\n\n"
                base_prompt += f"**Instruksi Spesifik:**\n{enh_type.prompt_instructions}\n\n"
                
                # Add domain-specific examples if available
                if domain_hint:
                    example = enh_type.get_example_for_domain(domain_hint)
                    if example:
                        base_prompt += f"**Contoh untuk domain {domain_hint}:** {example}\n\n"
                
                base_prompt += "---\n\n"
        
        # Add final instructions with comprehensive JSON format and enforcement
        base_prompt += """
## JSON OUTPUT FORMAT (CRITICAL!)

Output WAJIB berupa JSON object dengan struktur:

```json
{
  "enhancements": [
    {
      "type": "type_id_dari_user_selection",
      "title": "Judul spesifik dan deskriptif (bukan generic seperti 'Analisis Data')",
      "content": "Analisis mendalam MINIMAL 150 kata dengan framework: [Root analysis - mengapa penting] → [Practical application - bagaimana digunakan] → [Decision support - keputusan apa] → [Risk/opportunity - impact apa]. Evidence spesifik dari dokumen: ...",
      "source_units": ["unit_id1", "unit_id2"],
      "confidence": 0.85,
      "priority": 7
    }
  ],
  "metadata": {}
}
```

**FIELD SPECIFICATIONS:**
- `type`: Type ID WAJIB dari user selection (lihat list di atas). JANGAN gunakan type lain!
- `title`: Deskriptif, specific, actionable. Hindari kata generic "Analisis", "Ringkasan", "Data"
- `content`: MINIMAL 150 kata, gunakan 4-komponen framework, evidence-based dengan data spesifik
- `source_units`: Unit IDs yang relevan dari window (tabel, paragraf, dll)
- `confidence`: 0.0-1.0 (rendah jika extrapolation/assumption, tinggi jika data explicit)
- `priority`: 1-10 (urgency dan impact untuk decision maker, bukan hardcoded priority)
- Backend akan auto-generate unique `enhancement_id` - JANGAN buat field ini!

**QUANTITY & DISTRIBUTION (SUPER CRITICAL!):**
🔥 Generate MINIMAL 2-3 enhancement untuk SETIAP type yang user pilih
🔥 Total target: {len(selected_types) * 2}-{len(selected_types) * 3}+ enhancements per window
🔥 JANGAN fokus hanya di 1-2 type favorit - distribusi MERATA!
🔥 NO artificial limits - generate sebanyak mungkin yang berkualitas!

**PROHIBITED (WAJIB HINDARI!):**
❌ Enhancement yang hanya copy-paste text dari dokumen
❌ Enhancement yang obvious, trivial, atau eksplisit
❌ Enhancement dengan content < 150 kata
❌ Generalisasi tanpa evidence spesifik dari dokumen
❌ Mengatakan "tidak ada data" tanpa attempt untuk analyze/derive/project
❌ Menggunakan type yang TIDAK ada di user selection
❌ Title generic seperti "Analisis Data Tabel", "Ringkasan Informasi"

**QUALITY ASSURANCE CHECKLIST:**
Sebelum output, pastikan setiap enhancement:
✓ Mengungkap informasi TERSIRAT (bukan eksplisit)
✓ Minimal 150 kata dengan 4-komponen framework
✓ Bahasa Indonesia professional dan actionable
✓ Evidence-based dengan reference data spesifik
✓ Type ID match dengan user selection
✓ Title spesifik dan deskriptif
✓ Confidence dan priority appropriate

**ULTIMATE GOAL:** Generate enhancement berkualitas tinggi sebanyak-banyaknya yang truly add intelligence value untuk decision maker!
"""
        
        return base_prompt
    
    def to_frontend_config(self) -> Dict[str, Any]:
        """
        Convert registry to frontend-friendly format for UI rendering
        
        Returns structure like:
        {
          "categories": [...],
          "types": [...],
          "recommendations": {...},
          "metadata": {...}
        }
        """
        return {
            "metadata": self.metadata,
            "categories": [
                {
                    "id": cat.id,
                    "name": cat.name,
                    "name_en": cat.name_en,
                    "description": cat.description,
                    "icon": cat.icon,
                    "display_order": cat.display_order
                }
                for cat in self.get_all_categories()
            ],
            "types": [
                {
                    "id": t.id,
                    "category": t.category,
                    "name": t.name,
                    "name_en": t.name_en,
                    "description": t.description,
                    "applicable_domains": t.applicable_domains,
                    "default_enabled": t.default_enabled,
                    "default_priority": t.default_priority
                }
                for t in self.types.values()
            ],
            "domain_recommendations": self.domain_recommendations
        }


# Global registry instance (singleton pattern)
_registry_instance: Optional[EnhancementTypeRegistry] = None

def get_type_registry() -> EnhancementTypeRegistry:
    """
    Get global registry instance (singleton)
    
    This ensures we only load the YAML once and reuse the same instance.
    """
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = EnhancementTypeRegistry()
    return _registry_instance


def reload_registry() -> EnhancementTypeRegistry:
    """
    Force reload registry from YAML (useful for development/testing)
    """
    global _registry_instance
    _registry_instance = EnhancementTypeRegistry()
    return _registry_instance
