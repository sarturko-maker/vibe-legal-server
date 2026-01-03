"""
Document Style Detector

Analyzes Word documents to determine:
- Numbering: auto (Word handles) vs manual (numbers in text)
- Clause style: inline (title+body same line) vs heading (separate paras)

This tells the job processor which insert method to use and
what instructions to give the AI.
"""

import zipfile
import logging
import re
from lxml import etree
from io import BytesIO
from typing import NamedTuple, List

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

logger = logging.getLogger('vibelegal.style_detector')


class DocumentStyle(NamedTuple):
    """Document style configuration"""
    numbering: str          # "auto" or "manual"
    clause_style: str       # "inline" or "heading"
    include_numbers: bool   # Should AI include numbers in titles?
    insert_method: str      # Which method to use for inserts


def detect_document_style(source) -> DocumentStyle:
    """
    Analyze document and return style configuration.
    
    Args:
        source: File path (str), bytes, or BytesIO of .docx file
        
    Returns:
        DocumentStyle with detected settings
    """
    logger.info("Detecting document style...")
    
    # Load document bytes
    if isinstance(source, str):
        with open(source, 'rb') as f:
            doc_bytes = f.read()
    elif isinstance(source, bytes):
        doc_bytes = source
    else:
        source.seek(0)
        doc_bytes = source.read()
    
    # Parse XML
    with zipfile.ZipFile(BytesIO(doc_bytes), 'r') as zf:
        xml = zf.read('word/document.xml')
    
    root = etree.fromstring(xml)
    body = root.find(f'{W}body')
    paragraphs = body.findall(f'{W}p')
    
    # Detect numbering type and clause style
    numbering = _detect_numbering(paragraphs)
    clause_style = _detect_clause_style(paragraphs)
    
    # Determine settings based on detection
    include_numbers = (numbering == "manual")
    
    if clause_style == "heading":
        insert_method = "insert_clause_with_heading"
    else:
        insert_method = "insert_paragraph_after_with_bold_title"
    
    style = DocumentStyle(
        numbering=numbering,
        clause_style=clause_style,
        include_numbers=include_numbers,
        insert_method=insert_method
    )
    
    logger.info(f"  Numbering: {numbering}")
    logger.info(f"  Clause style: {clause_style}")
    logger.info(f"  Insert method: {insert_method}")
    
    return style


def _detect_numbering(paragraphs: List) -> str:
    """Detect auto vs manual numbering."""
    for p in paragraphs[:20]:
        # Check for numPr (Word auto-numbering)
        pPr = p.find(f'{W}pPr')
        if pPr is not None and pPr.find(f'{W}numPr') is not None:
            return "auto"
        
        # Check for manual numbers in text
        texts = [t.text for t in p.iter(f'{W}t') if t.text]
        text = ''.join(texts).strip()[:5]
        for pattern in ['1.', '2.', '3.', '4.', '5.', '6.', '7.', '8.', '9.']:
            if text.startswith(pattern):
                return "manual"
    
    return "manual"  # Default


def _detect_clause_style(paragraphs: List) -> str:
    """Detect inline vs heading+body style."""
    heading_count = 0
    inline_count = 0
    
    for i, p in enumerate(paragraphs[:25]):
        texts = [t.text for t in p.iter(f'{W}t') if t.text]
        text = ''.join(texts).strip()
        length = len(text)
        
        if length < 3:
            continue
        
        # Is this numbered?
        is_numbered = False
        if text[:5].startswith(('1.', '2.', '3.', '4.', '5.')):
            is_numbered = True
        pPr = p.find(f'{W}pPr')
        if pPr is not None and pPr.find(f'{W}numPr') is not None:
            is_numbered = True
        
        if is_numbered:
            next_len = 0
            if i + 1 < len(paragraphs):
                next_texts = [t.text for t in paragraphs[i+1].iter(f'{W}t') if t.text]
                next_len = len(''.join(next_texts).strip())
            
            if length < 50 and next_len > 80:
                heading_count += 1  # Short heading + long body
            elif length > 70:
                inline_count += 1   # Title and body on same line
            else:
                inline_count += 1
    
    return "heading" if heading_count > inline_count else "inline"


def get_ai_prompt_additions(style: DocumentStyle) -> str:
    """
    Generate comprehensive AI prompt additions based on detected style.
    
    This MUST be appended to the AI prompt to ensure correct output.
    """
    parts = []
    
    # ===========================================
    # RULE 1: MINIMAL REDLINING (CRITICAL!)
    # ===========================================
    parts.append("""=== CRITICAL: MINIMAL REDLINING RULE ===

Lawyers redline with PRECISION. Only mark text that ACTUALLY changes.
Do NOT delete and re-insert text that stays the same.

EXAMPLE 1 - Definition change:

Original clause: "Confidential Information" means any information disclosed by the Disclosing Party to the Receiving Party, whether orally or in writing, that is designated as confidential.

You want to change the definition criteria. 

BAD (over-redlining - deletes unchanged text):
{
  "type": "AMEND",
  "original_text": "\\"Confidential Information\\" means any information disclosed by the Disclosing Party to the Receiving Party, whether orally or in writing, that is designated as confidential.",
  "replacement_text": "\\"Confidential Information\\" means information that is: (a) marked as confidential in writing; or (b) confirmed as confidential within 14 days."
}
This deletes "Confidential Information" means" even though it doesn't change!

GOOD (minimal - only changes what's different):
{
  "type": "AMEND",
  "original_text": "any information disclosed by the Disclosing Party to the Receiving Party, whether orally or in writing, that is designated as confidential",
  "replacement_text": "information that is: (a) marked as confidential in writing; or (b) confirmed as confidential within 14 days"
}

EXAMPLE 2 - Adding text to end of clause:

Original: "This Agreement shall be governed by the laws of England and Wales."
You want to ADD: "The parties submit to the exclusive jurisdiction of the courts of England and Wales."

BAD (replaces entire clause):
{
  "type": "AMEND",
  "original_text": "This Agreement shall be governed by the laws of England and Wales.",
  "replacement_text": "This Agreement shall be governed by the laws of England and Wales. The parties submit to the exclusive jurisdiction of the courts of England and Wales."
}

GOOD (only adds the new part):
{
  "type": "AMEND",
  "original_text": "laws of England and Wales.",
  "replacement_text": "laws of England and Wales. The parties submit to the exclusive jurisdiction of the courts of England and Wales."
}

EXAMPLE 3 - Changing a number:

Original: "...for a period of five (5) years..."
You want: "...for a period of three (3) years..."

BAD: Replace entire sentence
GOOD: 
{
  "type": "AMEND",
  "original_text": "five (5) years",
  "replacement_text": "three (3) years"
}

ALWAYS find the SMALLEST text span that captures the change.""")

    # ===========================================
    # RULE 2: SURGICAL AMEND (preserve formatting)
    # ===========================================
    parts.append("""=== SURGICAL AMEND RULE ===

When amending a clause, do NOT include the clause title/number in original_text.
The title is bold and anchors the numbering - changing it breaks formatting.

BAD (includes title):
{
  "original_text": "1. Definition of Confidential Information. \\"Confidential Information\\" means...",
  "replacement_text": "1. Definition of Confidential Information. \\"Confidential Information\\" means..."
}

GOOD (preserves title):
{
  "original_text": "\\"Confidential Information\\" means any information...",
  "replacement_text": "\\"Confidential Information\\" means information that is..."
}

Leave clause numbers and titles untouched.""")

    # ===========================================
    # RULE 3: NUMBERING (based on detection)
    # ===========================================
    if style.include_numbers:
        parts.append("""=== NUMBERING RULE ===

This document uses MANUAL numbering (numbers are typed in text).
For INSERT operations, you MUST include clause numbers in new_clause_title.

Examples:
  "new_clause_title": "5. Jurisdiction."
  "new_clause_title": "2A. Exclusions."
  "new_clause_title": "3.1 Sub-clause."

Look at existing clause numbers to determine the next number.""")
    else:
        parts.append("""=== NUMBERING RULE ===

This document uses AUTO-NUMBERING (Word assigns numbers automatically).
For INSERT operations, do NOT include numbers in new_clause_title.

CORRECT: "new_clause_title": "Jurisdiction."
WRONG:   "new_clause_title": "5. Jurisdiction."

Word will assign the correct number based on position.""")

    # ===========================================
    # RULE 4: WHEN TO USE EACH OPERATION
    # ===========================================
    parts.append("""=== OPERATION SELECTION ===

AMEND - Use when CHANGING existing text:
  - Narrowing a definition
  - Changing a time period
  - Modifying obligations
  - Adding words to the END of existing text

DELETE - Use when REMOVING an entire clause:
  - Non-solicitation clause that shouldn't be in an NDA
  - Inappropriate penalty clauses

INSERT_CLAUSE - Use when ADDING a completely new clause:
  - Adding exclusions clause that doesn't exist
  - Adding return/destruction clause that's missing
  - Adding new protections not in the original

If the clause EXISTS but needs modification → AMEND
If the clause is MISSING entirely → INSERT_CLAUSE
If the clause should be REMOVED → DELETE""")

    # ===========================================
    # RULE 5: OUTPUT FORMAT
    # ===========================================
    parts.append("""=== OUTPUT FORMAT ===

Return ONLY a JSON array. No markdown, no explanation, no preamble.

[
  {
    "type": "AMEND",
    "original_text": "exact text to find (MINIMAL span)",
    "replacement_text": "new text",
    "reason": "brief explanation"
  },
  {
    "type": "INSERT_CLAUSE",
    "after_clause": "text from clause to insert after",
    "new_clause_title": "Title.",
    "new_clause_body": "Full body text of new clause.",
    "reason": "brief explanation"
  },
  {
    "type": "DELETE",
    "original_text": "text from paragraph to delete",
    "reason": "brief explanation"
  }
]""")

    return "\n\n".join(parts)
