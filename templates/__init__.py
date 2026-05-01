"""
JSON TEMPLATE LOADER
====================

Loads document templates from the /templates directory.
This allows for easy modification of structures without changing code.
"""

import json
import os
from pathlib import Path
from pipeline_architecture import (
    DocumentTemplate, 
    DocumentType, 
    TemplateSection
)

def load_template_from_json(json_path: Path) -> DocumentTemplate:
    """Load a single JSON file into a DocumentTemplate object"""
    with open(json_path, 'r') as f:
        data = json.load(f)
    
    # Convert sections to TemplateSection objects
    sections = []
    for s in data.get('sections', []):
        if 'constraints' in s:
            s['content_constraints'] = s.pop('constraints')
        sections.append(TemplateSection(**s))
    
    # Create DocumentTemplate
    # Filter data to only include fields in DocumentTemplate
    template_fields = {
        'name': data['name'],
        'type': DocumentType(data['type']),
        'description': data['description'],
        'sections': sections,
        'words_per_page': data.get('words_per_page', 300),
        'min_pages': data.get('min_pages', 2),
        'max_pages': data.get('max_pages', 200),
        'tone': data.get('tone', 'professional'),
        'target_emotion': data.get('target_emotion', 'informed'),
        'desired_action': data.get('desired_action', 'explore further'),
        'technical_level': data.get('technical_level', 'intermediate'),
        'citation_style': data.get('citation_style'),
        'visual_recommendations': data.get('visual_recommendations', {}),
        'expansion_titles': data.get('expansion_titles', [])
    }
    
    return DocumentTemplate(**template_fields)

def get_template(doc_type: DocumentType) -> DocumentTemplate:
    """Get template for document type by loading from templates/ directory"""
    template_dir = Path(__file__).parent / "templates"
    template_path = template_dir / f"{doc_type.value}.json"
    
    if template_path.exists():
        try:
            return load_template_from_json(template_path)
        except Exception as e:
            print(f"[Templates] Failed to load {doc_type.value}: {e}")
    
    # Fallback or error
    raise FileNotFoundError(f"Template not found for {doc_type.value} at {template_path}")

def list_templates() -> dict:
    """List all available templates in the templates/ directory"""
    template_dir = Path(__file__).parent / "templates"
    results = {}
    
    if not template_dir.exists():
        return results
        
    for json_file in template_dir.glob("*.json"):
        try:
            template = load_template_from_json(json_file)
            results[template.type.value] = {
                "name": template.name,
                "description": template.description,
                "sections": len(template.sections),
                "min_pages": template.min_pages,
                "max_pages": template.max_pages,
            }
        except Exception as e:
            print(f"[Templates] Error listing {json_file.name}: {e}")
            
    return results
