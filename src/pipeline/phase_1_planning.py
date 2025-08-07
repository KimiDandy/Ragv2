import json
from pathlib import Path
import google.generativeai as genai
from loguru import logger

from ..core.config import GOOGLE_API_KEY, PLANNING_MODEL, PIPELINE_ARTEFACTS_DIR

# Configure the generative AI model
genai.configure(api_key=GOOGLE_API_KEY)

def create_enrichment_plan(markdown_path: str, doc_output_dir: str) -> str:
    """
    Analyzes a markdown document to create a comprehensive enrichment plan.

    This function corresponds to Phase 1 of the Genesis-RAG project.
    It reads the content of markdown_v1.md, sends it to the Gemini API with a 
    specific prompt, and saves the resulting JSON plan.

    Args:
        markdown_path (str): The path to the input markdown_v1.md file.
        doc_output_dir (str): The directory where the enrichment_plan.json will be saved.

    Returns:
        str: The path to the generated enrichment_plan.json file.
    """
    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
    except FileNotFoundError:
        logger.error(f"Markdown file not found at {markdown_path}")
        return ""

    prompt = f"""Role: You are a multi-disciplinary research analyst.
Task: Read the following Markdown document thoroughly. Identify ALL items that require further explanation or enrichment for a complete understanding. DO NOT provide the explanations now.
Format Output: You MUST respond ONLY with a valid JSON object, with no additional text before or after it.

Required JSON Structure:
{{
  "terms_to_define": [
    {{"term": "string", "context": "string (the full sentence where the term appears)"}}
  ],
  "concepts_to_simplify": [
    {{"identifier": "string (first 10 words of the paragraph)", "paragraph_text": "string (the full text of the complex paragraph)"}}
  ],
  "images_to_describe": ["string (the filename from the placeholder, e.g., 'image_01.png')"],
  "inferred_connections": [
    {{"from_concept": "string (source paragraph identifier)", "to_concept": "string (destination paragraph identifier)", "relationship_type": "string (e.g., 'provides an example for', 'is a counter-argument to')"}}
  ]
}}

Document to Analyze:
---
{markdown_content}
---
"""

    logger.info("Sending request to Gemini for enrichment plan...")
    model = genai.GenerativeModel(PLANNING_MODEL)
    response = model.generate_content(prompt)

    try:
        json_text = response.text.strip().replace('```json', '').replace('```', '').strip()
        plan_data = json.loads(json_text)
    except (json.JSONDecodeError, AttributeError) as e:
        logger.error(f"Error decoding JSON from Gemini response: {e}")
        logger.error(f"Raw response text:\n{response.text}")
        return ""

    plan_output_path = Path(doc_output_dir) / "enrichment_plan.json"
    with open(plan_output_path, 'w', encoding='utf-8') as f:
        json.dump(plan_data, f, indent=2)

    logger.info(f"Phase 1 completed. Enrichment plan saved to: {plan_output_path}")
    return str(plan_output_path)

if __name__ == '__main__':
    base_artefacts_dir = Path(PIPELINE_ARTEFACTS_DIR)
    if not base_artefacts_dir.exists():
        logger.error(f"'{PIPELINE_ARTEFACTS_DIR}' directory not found. Run phase_0 first.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            logger.error(f"No document directories found in '{PIPELINE_ARTEFACTS_DIR}'.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            markdown_file = latest_doc_dir / "markdown_v1.md"
            logger.info(f"Running Phase 1 on: {markdown_file}")
            create_enrichment_plan(str(markdown_file), str(latest_doc_dir))
