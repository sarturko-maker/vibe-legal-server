"""
VibeLegal OOXML Library v0.6.0

Surgical editing of Word documents with STRUCTURE and STYLE AWARENESS.

Main classes:
    DocumentEditor - Edit documents by modifying original XML
    StyleAwareEditor - Style-matching inserts with smart detection
    StructureMap - Document hierarchy representation
"""

from .surgical_editor import DocumentEditor

# Structure detection
from .structure_detector import (
    detect_structure,
    detect_structure_from_docx,
    StructureMap,
    StructureNode,
    NodeRole
)

# Style-aware editing
from .style_aware_editor import StyleAwareEditor

# Structure operations (if available)
try:
    from .structure_operations import (
        StructureOperation,
        ResolvedOperation,
        InsertPosition,
        OperationResolver,
        ContentNode,
        generate_structure_prompt,
        generate_full_ai_prompt
    )
except ImportError:
    pass

# Styler (post-processing)
from .styler import Styler, StylerResult

__version__ = "0.7.0"
__all__ = [
    # Editors
    'DocumentEditor',
    'StyleAwareEditor',
    # Structure detection
    'detect_structure',
    'detect_structure_from_docx',
    'StructureMap', 
    'StructureNode',
    'NodeRole',
    # Structure operations
    'StructureOperation',
    'ResolvedOperation',
    'InsertPosition',
    'OperationResolver',
    'ContentNode',
    'generate_structure_prompt',
    'generate_full_ai_prompt',
    # Styler
    'Styler',
    'StylerResult',
]
