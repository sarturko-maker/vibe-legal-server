from ooxml_lib import DocumentEditor
import logging
from typing import Dict, Any

logger = logging.getLogger("vibelegal")

def extract_document(editor: DocumentEditor) -> Dict[str, Any]:
    """
    Extracts content from a DocumentEditor instance.
    Returns a dictionary representing the document structure.
    """
    logger.info("Extracting content from DocumentEditor")
    
    paragraphs = editor.get_paragraphs_text()
    
    # Structure for AI analysis
    # We join all paragraphs with newlines for now, or keep them structured
    full_text = "\n".join(paragraphs)
    
    return {
        "text": full_text,
        "paragraphs": [
            {"id": str(i), "text": text} 
            for i, text in enumerate(paragraphs)
        ]
    }
