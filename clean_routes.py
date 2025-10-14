"""
Script to clean routes.py by removing all legacy endpoints
"""

def clean_routes_file():
    """Remove legacy endpoints from routes.py"""
    
    routes_path = "src/api/routes.py"
    
    # Read original file
    with open(routes_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # Define sections to KEEP (line ranges)
    # Format: (start_line, end_line, description)
    keep_sections = [
        (1, 206, "Imports and helper functions"),
        (483, 516, "/ask/ endpoint"),
        (809, 1103, "Automated pipeline endpoints"),
    ]
    
    # Build new file content
    new_lines = []
    new_lines.append("# Genesis RAG API Routes - Cleaned Version\n")
    new_lines.append("# Legacy manual workflow endpoints removed\n")
    new_lines.append("# Only automated pipeline endpoints remain\n\n")
    
    for start, end, description in keep_sections:
        new_lines.append(f"# ============================================================================\n")
        new_lines.append(f"# {description}\n")
        new_lines.append(f"# ============================================================================\n\n")
        new_lines.extend(lines[start-1:end])
        new_lines.append("\n\n")
    
    # Write cleaned file
    with open(routes_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    
    print(f"✓ routes.py cleaned successfully!")
    print(f"  Original: {len(lines)} lines")
    print(f"  New: {len(new_lines)} lines")
    print(f"  Removed: {len(lines) - len(new_lines)} lines ({100*(len(lines) - len(new_lines))/len(lines):.1f}%)")

if __name__ == "__main__":
    clean_routes_file()
