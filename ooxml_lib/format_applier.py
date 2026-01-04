"""
Format Applier - Apply formatting fixes from Styler AI to document.

Takes the list of fixes returned by Styler AI and applies them to the document XML.
"""

import logging
from lxml import etree
from zipfile import ZipFile
from io import BytesIO
from typing import List, Dict

logger = logging.getLogger("vibelegal.format_applier")

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def apply_styler_fixes(doc_bytes: bytes, fixes: List[Dict]) -> bytes:
    """
    Apply formatting fixes to document.
    
    Args:
        doc_bytes: Document as bytes
        fixes: List of fix dictionaries from Styler AI
    
    Returns:
        Modified document bytes
    """
    if not fixes:
        logger.info("Format Applier: No fixes to apply")
        return doc_bytes
    
    logger.info(f"Format Applier: Applying {len(fixes)} fixes")
    
    # Load document
    zf_in = ZipFile(BytesIO(doc_bytes))
    doc_xml = zf_in.read('word/document.xml')
    root = etree.fromstring(doc_xml)
    body = root.find(f'{W}body')
    paragraphs = body.findall(f'{W}p')
    
    # Build paragraph lookup by ID
    para_lookup = {f'p{i}': para for i, para in enumerate(paragraphs)}
    
    # Apply each fix
    for fix in fixes:
        para_id = fix.get('id')
        para = para_lookup.get(para_id)
        
        if para is None:
            logger.warning(f"Format Applier: Paragraph {para_id} not found")
            continue
        
        # Handle bold range
        bold_range = fix.get('set_bold_range')
        if bold_range:
            if bold_range == 'entire':
                _apply_bold_entire(para)
                logger.info(f"  {para_id}: Applied BOLD (entire)")
            elif bold_range == 'start_to_period':
                _apply_bold_to_delimiter(para, '.')
                logger.info(f"  {para_id}: Applied BOLD (to period)")
            elif bold_range == 'start_to_colon':
                _apply_bold_to_delimiter(para, ':')
                logger.info(f"  {para_id}: Applied BOLD (to colon)")
            elif bold_range == 'none':
                _remove_all_bold(para)
                logger.info(f"  {para_id}: Removed BOLD")
        
        # Handle indent
        if 'set_indent' in fix:
            _set_indent(para, fix['set_indent'])
            logger.info(f"  {para_id}: Set indent={fix['set_indent']}")
        
        # Handle space after
        if 'set_space_after' in fix:
            _set_space_after(para, fix['set_space_after'])
            logger.info(f"  {para_id}: Set space_after={fix['set_space_after']}")
        
        # Handle space before
        if 'set_space_before' in fix:
            _set_space_before(para, fix['set_space_before'])
            logger.info(f"  {para_id}: Set space_before={fix['set_space_before']}")
    
    # Save document
    output = BytesIO()
    with ZipFile(output, 'w') as zf_out:
        for item in zf_in.namelist():
            if item == 'word/document.xml':
                zf_out.writestr(item, etree.tostring(root, xml_declaration=True, encoding='UTF-8'))
            else:
                zf_out.writestr(item, zf_in.read(item))
    
    zf_in.close()
    return output.getvalue()


# =============================================================================
# BOLD HELPERS
# =============================================================================

def _apply_bold_entire(para):
    """Make entire paragraph bold."""
    for run in para.findall(f'.//{W}r'):
        rPr = run.find(f'{W}rPr')
        if rPr is None:
            rPr = etree.Element(f'{W}rPr')
            run.insert(0, rPr)
        
        if rPr.find(f'{W}b') is None:
            etree.SubElement(rPr, f'{W}b')


def _apply_bold_to_delimiter(para, delimiter: str):
    """
    Make text bold from start until first delimiter (. or :).
    
    HANDLES SINGLE-RUN PARAGRAPHS: If the entire paragraph is one run,
    we split it at the delimiter position first, then bold only the first part.
    """
    # Get full text to find delimiter position
    text = _get_text(para)
    
    logger.info(f"    _apply_bold_to_delimiter: text='{text[:50]}...' delimiter='{delimiter}'")
    
    # Find delimiter position
    delim_pos = text.find(delimiter)
    if delim_pos == -1:
        logger.warning(f"    _apply_bold_to_delimiter: No '{delimiter}' found in text")
        return
    
    # Bold everything up to and including delimiter
    bold_end = delim_pos + 1
    logger.info(f"    _apply_bold_to_delimiter: bold_end={bold_end} (first {bold_end} chars will be bold)")
    
    # Find ALL runs (including inside w:ins wrappers)
    all_runs = list(para.findall(f'.//{W}r'))
    logger.info(f"    _apply_bold_to_delimiter: Found {len(all_runs)} runs")
    
    # STEP 1: Remove ALL bold from paragraph first
    removed_count = 0
    for run in all_runs:
        rPr = run.find(f'{W}rPr')
        if rPr is not None:
            b = rPr.find(f'{W}b')
            if b is not None:
                rPr.remove(b)
                removed_count += 1
    logger.info(f"    _apply_bold_to_delimiter: Removed bold from {removed_count} runs")
    
    # STEP 2: Find which run contains the delimiter and split if needed
    char_pos = 0
    for run in all_runs:
        t = run.find(f'{W}t')
        if t is None or not t.text:
            continue
        
        run_start = char_pos
        run_end = char_pos + len(t.text)
        
        # Check if this run CONTAINS the delimiter (spans across bold_end)
        if run_start < bold_end <= run_end:
            # This run needs to be SPLIT
            split_point = bold_end - run_start
            original_text = t.text
            
            logger.info(f"    Splitting run at char {split_point}: '{original_text[:split_point]}' | '{original_text[split_point:]}'")
            
            # Create new run for the second part (after delimiter)
            new_run = etree.Element(f'{W}r')
            new_t = etree.SubElement(new_run, f'{W}t')
            new_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            new_t.text = original_text[split_point:]
            
            # Truncate original run to first part (before/including delimiter)
            t.text = original_text[:split_point]
            
            # Insert new run after current run
            run.addnext(new_run)
            
            # Make the first part bold
            rPr = run.find(f'{W}rPr')
            if rPr is None:
                rPr = etree.Element(f'{W}rPr')
                run.insert(0, rPr)
            if rPr.find(f'{W}b') is None:
                etree.SubElement(rPr, f'{W}b')
            
            logger.info(f"    Split and bolded first part: '{t.text}'")
            return  # Done - we split and bolded
        
        elif run_end <= bold_end:
            # This entire run is before the delimiter, make it bold
            rPr = run.find(f'{W}rPr')
            if rPr is None:
                rPr = etree.Element(f'{W}rPr')
                run.insert(0, rPr)
            if rPr.find(f'{W}b') is None:
                etree.SubElement(rPr, f'{W}b')
            logger.info(f"    Run chars {run_start}-{run_end}: BOLD (entirely before delimiter)")
        
        else:
            # This run is entirely after the delimiter, no bold
            logger.info(f"    Run chars {run_start}-{run_end}: not bold (after delimiter)")
        
        char_pos = run_end
    
    logger.info(f"    _apply_bold_to_delimiter: Complete")


def _remove_all_bold(para):
    """Remove bold from entire paragraph."""
    for run in para.findall(f'.//{W}r'):
        rPr = run.find(f'{W}rPr')
        if rPr is not None:
            b = rPr.find(f'{W}b')
            if b is not None:
                rPr.remove(b)


# =============================================================================
# SPACING/INDENT HELPERS
# =============================================================================

def _set_indent(para, value: int):
    """Set left indent."""
    pPr = _get_or_create_pPr(para)
    
    ind = pPr.find(f'{W}ind')
    if ind is None:
        ind = etree.SubElement(pPr, f'{W}ind')
    
    ind.set(f'{W}left', str(value))


def _set_space_after(para, value: int):
    """Set space after paragraph."""
    pPr = _get_or_create_pPr(para)
    
    spacing = pPr.find(f'{W}spacing')
    if spacing is None:
        spacing = etree.SubElement(pPr, f'{W}spacing')
    
    spacing.set(f'{W}after', str(value))


def _set_space_before(para, value: int):
    """Set space before paragraph."""
    pPr = _get_or_create_pPr(para)
    
    spacing = pPr.find(f'{W}spacing')
    if spacing is None:
        spacing = etree.SubElement(pPr, f'{W}spacing')
    
    spacing.set(f'{W}before', str(value))


def _get_or_create_pPr(para):
    """Get or create paragraph properties element."""
    pPr = para.find(f'{W}pPr')
    if pPr is None:
        pPr = etree.Element(f'{W}pPr')
        para.insert(0, pPr)
    return pPr


def _get_text(para) -> str:
    """Extract plain text from paragraph."""
    texts = []
    for t in para.findall(f'.//{W}t'):
        if t.text:
            texts.append(t.text)
    return ''.join(texts)
