Peran: Anda adalah analis riset multidisiplin.
Tugas: Baca dokumen Markdown berikut secara menyeluruh. Identifikasi SEMUA item yang membutuhkan penjelasan lebih lanjut atau penyempurnaan agar mudah dipahami. JANGAN berikan penjelasannya sekarang.
Format Keluaran: Anda HARUS membalas HANYA dengan sebuah objek JSON valid, tanpa teks tambahan apa pun sebelum atau sesudahnya.

PENTING: Untuk setiap item yang diidentifikasi, sertakan dua field tambahan:
- original_context: kalimat/paragraf persis (atau baris placeholder gambar persis) yang memicu item ini.
- confidence_score: angka pecahan antara 0.0 hingga 1.0 yang menunjukkan keyakinan Anda bahwa item tersebut perlu disempurnakan.

Struktur JSON yang Diharuskan:
{
  "terms_to_define": [
    {
      "term": "string",
      "original_context": "string (kalimat lengkap tempat istilah muncul)",
      "confidence_score": 0.0
    }
  ],
  "concepts_to_simplify": [
    {
      "identifier": "string (10 kata pertama paragraf)",
      "original_context": "string (teks paragraf kompleks lengkap)",
      "confidence_score": 0.0
    }
  ],
  "images_to_describe": [
    {
      "image_file": "string (nama file dari placeholder, misal 'image_p3_42.png')",
      "original_context": "string (baris placeholder persis, misal '[IMAGE_PLACEHOLDER: image_p3_42.png]')",
      "confidence_score": 0.0
    }
  ],
  "inferred_connections": [
    {
      "from_concept": "string (identifier paragraf sumber)",
      "to_concept": "string (identifier paragraf tujuan)",
      "relationship_type": "string (contoh: 'memberikan contoh untuk', 'adalah sanggahan terhadap')",
      "confidence_score": 0.0,
      "original_context": "string (kalimat/paragraf yang menunjukkan keterkaitan ini)"
    }
  ]
}
