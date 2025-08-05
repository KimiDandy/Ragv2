import os
import json
import base64
from pathlib import Path
import google.generativeai as genai
from dotenv import load_dotenv
from PIL import Image

# Load environment variables and configure API
load_dotenv()
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

def generate_bulk_content(doc_output_dir: str) -> str:
    """
    Generates all enrichment content based on the plan from Phase 1.

    This function corresponds to Phase 2 of the Genesis-RAG project.
    It performs a single, complex multimodal API call to Gemini, providing
    the original markdown, the enrichment plan, and relevant image data.

    Args:
        doc_output_dir (str): The document's artefact directory, containing the
                              markdown file, plan, and assets.

    Returns:
        str: The path to the generated_content.json file.
    """
    doc_path = Path(doc_output_dir)
    markdown_path = doc_path / "markdown_v1.md"
    plan_path = doc_path / "enrichment_plan.json"
    assets_path = doc_path / "assets"

    # Load required files
    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
        with open(plan_path, 'r', encoding='utf-8') as f:
            enrichment_plan = json.load(f)
    except FileNotFoundError as e:
        print(f"Error: Required file not found - {e}")
        return ""

    # Prepare image data for the multimodal prompt
    image_parts = []
    images_to_describe = enrichment_plan.get("images_to_describe", [])
    for image_filename in images_to_describe:
        image_path = assets_path / image_filename
        if image_path.exists():
            try:
                img = Image.open(image_path)
                # The API can take PIL.Image objects directly
                image_parts.append(img)
                # Also add a text part to identify the image
                image_parts.append(f"\n--- Image Filename: {image_filename} ---\n")
            except Exception as e:
                print(f"Warning: Could not process image {image_filename}: {e}")
        else:
            print(f"Warning: Image file not found: {image_path}")

    # Construct the multimodal prompt
    prompt_parts = [
        "Role: You are a living encyclopedia and an expert technical writer.",
        "Context: I am providing you with an original document, an enrichment plan, and relevant image assets.",
        "Task: Execute the enrichment plan. Generate all requested content clearly and accurately based on the document's context.",
        "Format Output: You MUST respond ONLY with a single JSON object that maps each item from the plan to your generated content.",
        "\n--- Original Document (for context) ---",
        markdown_content,
        "\n--- Enrichment Plan to Execute ---",
        json.dumps(enrichment_plan, indent=2),
        "\n--- Image Assets ---"
    ] + image_parts

    print("Sending multimodal request to Gemini for content generation...")
    model = genai.GenerativeModel('gemini-2.5-flash')
    response = model.generate_content(prompt_parts)

    # Process and save the response
    try:
        json_text = response.text.strip().replace('```json', '').replace('```', '').strip()
        content_data = json.loads(json_text)
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"Error decoding JSON from Gemini response: {e}")
        print(f"Raw response text:\n{response.text}")
        return ""

    output_path = doc_path / "generated_content.json"
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(content_data, f, indent=2)

    print(f"Phase 2 completed. Generated content saved to: {output_path}")
    return str(output_path)

if __name__ == '__main__':
    base_artefacts_dir = Path("pipeline_artefacts")
    if not base_artefacts_dir.exists():
        print("Error: 'pipeline_artefacts' directory not found. Run phase_0 first.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            print("Error: No document directories found in 'pipeline_artefacts'.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            print(f"Running Phase 2 on directory: {latest_doc_dir}")
            generate_bulk_content(str(latest_doc_dir))