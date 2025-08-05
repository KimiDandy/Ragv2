import json
from pathlib import Path

def synthesize_final_markdown(doc_output_dir: str) -> str:
    """
    Synthesizes the final markdown document by merging original text with generated content.

    This function corresponds to Phase 3 of the Genesis-RAG project.
    It reads markdown_v1.md and generated_content.json, then intelligently injects
    the enriched content into the original text using a footnote strategy.

    Args:
        doc_output_dir (str): The document's artefact directory.

    Returns:
        str: The path to the final markdown_v2.md file.
    """
    doc_path = Path(doc_output_dir)
    markdown_path = doc_path / "markdown_v1.md"
    generated_content_path = doc_path / "generated_content.json"

    # Load required files
    try:
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()
        with open(generated_content_path, 'r', encoding='utf-8') as f:
            generated_content = json.load(f)
    except FileNotFoundError as e:
        print(f"Error: Required file not found - {e}")
        return ""

    footnote_counter = 1
    footnotes_list = []
    modified_markdown = markdown_content

    # Process term definitions
    for item in generated_content.get("terms_to_define", []):
        term = item.get("term")
        definition = item.get("definition")
        if term and definition:
            # Add footnote marker next to the term
            modified_markdown = modified_markdown.replace(term, f"{term}[^{footnote_counter}]", 1)
            footnotes_list.append(f"[^{footnote_counter}]: **{term}:** {definition}")
            footnote_counter += 1

    # Process simplified concepts
    for item in generated_content.get("concepts_to_simplify", []):
        identifier = item.get("identifier")
        simplified_text = item.get("simplified_text")
        if identifier and simplified_text:
            # Find the paragraph starting with the identifier and add a footnote
            if identifier in modified_markdown:
                modified_markdown = modified_markdown.replace(identifier, f"{identifier}[^{footnote_counter}]", 1)
                footnotes_list.append(f"[^{footnote_counter}]: **Simplified:** {simplified_text}")
                footnote_counter += 1

    # Process image descriptions
    for item in generated_content.get("image_descriptions", []):
        image_file = item.get("image_file")
        description = item.get("description")
        placeholder = f"[IMAGE_PLACEHOLDER: {image_file}]"
        if image_file and description:
            if placeholder in modified_markdown:
                modified_markdown = modified_markdown.replace(placeholder, f"{placeholder}[^{footnote_counter}]", 1)
                footnotes_list.append(f"[^{footnote_counter}]: **Image Description ({image_file}):** {description}")
                footnote_counter += 1

    # Combine the modified markdown with the footnotes
    if footnotes_list:
        final_content = modified_markdown + "\n\n---\n\n" + "\n".join(footnotes_list)
    else:
        final_content = modified_markdown # No changes if no footnotes were added

    # Save the final synthesized markdown
    output_path = doc_path / "markdown_v2.md"
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_content)

    print(f"Phase 3 completed. Final markdown saved to: {output_path}")
    return str(output_path)

if __name__ == '__main__':
    base_artefacts_dir = Path("pipeline_artefacts")
    if not base_artefacts_dir.exists():
        print("Error: 'pipeline_artefacts' directory not found.")
    else:
        all_doc_dirs = [d for d in base_artefacts_dir.iterdir() if d.is_dir()]
        if not all_doc_dirs:
            print("Error: No document directories found.")
        else:
            latest_doc_dir = max(all_doc_dirs, key=lambda d: d.stat().st_mtime)
            print(f"Running Phase 3 on directory: {latest_doc_dir}")
            synthesize_final_markdown(str(latest_doc_dir))
