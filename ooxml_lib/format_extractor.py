"""
Format Extractor - Extract formatting facts from document paragraphs.

No interpretation, no roles - just raw formatting data.
Used to create format maps for Styler AI comparison.

Bold text is marked with **markdown** syntax in content:
- "**Purpose.** The Parties wish..." = inline title bold
- "**OBLIGATIONS:**" = entire paragraph bold
"""

import logging
from lxml import etree
from zipfile import ZipFile
from io import BytesIO
from typing import List, Dict

logger = logging.getLogger("vibelegal.format_extractor")

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def extract_format_map(doc_bytes: bytes, check_vibelegal: bool = False) -> List[Dict]:
    """
    Extract formatting map from document.
    
    Args:
        doc_bytes: Document as bytes
        check_vibelegal: If True, include is_vibelegal flag (for modified doc)
    
    Returns:
        List of paragraph formatting dictionaries
    """
    with ZipFile(BytesIO(doc_bytes)) as zf:
        doc_xml = zf.read('word/document.xml')
    
    root = etree.fromstring(doc_xml)
    body = root.find(f'{W}body')
    paragraphs = body.findall(f'{W}p')
    
    format_map = []
    
    for i, para in enumerate(paragraphs):
        # Content includes **bold** markers
        content = _get_text_with_bold_markers(para)
        
        # Skip empty paragraphs
        if not content.strip():
            continue
        
        para_format = {
            'id': f'p{i}',
            'content': content[:100],  # Increased to show more context with markers
            'left_indent': _get_left_indent(para),
            'space_before': _get_space_before(para),
            'space_after': _get_space_after(para),
            'has_numbering': _has_numbering(para),
            'has_bullet': _has_bullet(para),
        }
        
        if check_vibelegal:
            para_format['is_vibelegal'] = _is_vibelegal_insertion(para)
        
        format_map.append(para_format)
    
    return format_map


def format_map_to_string(format_map: List[Dict]) -> str:
    """
    Convert format map to readable string for AI prompt.
    
    Output format:
        p0: [PLAIN] "**MUTUAL NDA**"
        p3: [NUMBERED, space_after=120] "**Purpose.** The Parties wish..."
        p5: [BULLET, indent=720] "Trade secrets..."
        p9: [space_after=200] [VIBELEGAL] "Exclusions. Confidential..."
    """
    lines = []
    
    for p in format_map:
        flags = []
        
        # List flags
        if p.get('has_bullet'):
            flags.append('BULLET')
        elif p.get('has_numbering'):
            flags.append('NUMBERED')
        
        # Indent
        if p.get('left_indent', 0) > 0:
            flags.append(f"indent={p['left_indent']}")
        
        # Spacing
        if p.get('space_before', 0) > 0:
            flags.append(f"space_before={p['space_before']}")
        if p.get('space_after', 0) > 0:
            flags.append(f"space_after={p['space_after']}")
        
        # Build line - content already includes **bold** markers
        flags_str = ", ".join(flags) if flags else "PLAIN"
        vibelegal = " [VIBELEGAL]" if p.get('is_vibelegal') else ""
        
        lines.append(f"{p['id']}: [{flags_str}]{vibelegal} \"{p['content']}\"")
    
    return "\n".join(lines)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def _get_text_with_bold_markers(para) -> str:
    """
    Extract text with **bold** markers around bold text.
    AI can read this like markdown.
    
    Examples:
        "**Purpose.** The Parties wish..." = inline title bold
        "**OBLIGATIONS:**" = entire paragraph bold
        "The Receiving Party shall..." = nothing bold
    """
    result = []
    in_bold = False
    
    for run in para.findall(f'.//{W}r'):
        t = run.find(f'{W}t')
        if t is None or not t.text:
            continue
        
        text = t.text
        
        # Check if this run is bold
        rPr = run.find(f'{W}rPr')
        is_bold = rPr is not None and rPr.find(f'{W}b') is not None
        
        # Add markers when bold state changes
        if is_bold and not in_bold:
            result.append('**')
            in_bold = True
        elif not is_bold and in_bold:
            result.append('**')
            in_bold = False
        
        result.append(text)
    
    # Close bold if still open at end
    if in_bold:
        result.append('**')
    
    return ''.join(result)


def _get_left_indent(para) -> int:
    """Get left indent in twips."""
    pPr = para.find(f'{W}pPr')
    if pPr is None:
        return 0
    ind = pPr.find(f'{W}ind')
    if ind is None:
        return 0
    left = ind.get(f'{W}left')
    return int(left) if left else 0


def _get_space_before(para) -> int:
    """Get space before in twips."""
    pPr = para.find(f'{W}pPr')
    if pPr is None:
        return 0
    spacing = pPr.find(f'{W}spacing')
    if spacing is None:
        return 0
    before = spacing.get(f'{W}before')
    return int(before) if before else 0


def _get_space_after(para) -> int:
    """Get space after in twips."""
    pPr = para.find(f'{W}pPr')
    if pPr is None:
        return 0
    spacing = pPr.find(f'{W}spacing')
    if spacing is None:
        return 0
    after = spacing.get(f'{W}after')
    return int(after) if after else 0


def _has_numbering(para) -> bool:
    """Check if paragraph has Word auto-numbering."""
    pPr = para.find(f'{W}pPr')
    if pPr is None:
        return False
    numPr = pPr.find(f'{W}numPr')
    return numPr is not None


def _has_bullet(para) -> bool:
    """
    Check if paragraph has bullet formatting.
    
    Note: Full detection requires checking numbering.xml for abstractNumId.
    For now, we check for numPr presence (bullets and numbered lists).
    The AI can distinguish based on content patterns.
    """
    pPr = para.find(f'{W}pPr')
    if pPr is None:
        return False
    numPr = pPr.find(f'{W}numPr')
    return numPr is not None


def _is_vibelegal_insertion(para, author: str = "VibeLegal") -> bool:
    """Check if paragraph was inserted by VibeLegal."""
    for ins in para.findall(f'.//{W}ins'):
        ins_author = ins.get(f'{W}author')
        if ins_author == author:
            return True
    return False
