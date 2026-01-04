"""
STYLER - Post-processing for redlined documents.

Fixes formatting issues and flags logical problems WITHOUT changing legal language.

Two passes:
1. STRUCTURE PASS - Fix numbering, format consistency (auto-apply if our insertion)
2. LOGIC PASS - Flag sequence, placement issues (never auto-fix)

Key constraint: No redlines over redlines - if we inserted text, modify directly.
"""

import re
import logging
import zipfile
from io import BytesIO
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from lxml import etree
from copy import deepcopy

logger = logging.getLogger("vibelegal.styler")

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


@dataclass
class StylerResult:
    """Result of styler processing."""
    fixes_applied: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    modified_bytes: bytes = b''
    
    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0
    
    @property
    def fix_count(self) -> int:
        return len(self.fixes_applied)


class Styler:
    """
    Post-processor for redlined documents.
    
    Fixes formatting issues and flags logical problems.
    """
    
    def __init__(self, doc_bytes: bytes, author: str = "VibeLegal", original_reference: dict = None):
        """
        Initialize styler with document bytes.
        
        Args:
            doc_bytes: The redlined document as bytes
            author: The author name used for VibeLegal insertions
            original_reference: Optional reference formats extracted from original document
        """
        self.doc_bytes = doc_bytes
        self.author = author
        self.fixes_applied: List[str] = []
        self.warnings: List[str] = []
        
        # Parse document
        self.zip_buffer = BytesIO(doc_bytes)
        self.zf = zipfile.ZipFile(self.zip_buffer, 'r')
        self.doc_xml = self.zf.read('word/document.xml')
        self.tree = etree.fromstring(self.doc_xml)
        self.body = self.tree.find(f'{W}body')
        
        # Use provided reference or detect from current document (fallback)
        if original_reference:
            self.reference_formats = original_reference
        else:
            self.reference_formats = self.detect_reference_formats()
        
    # =========================================================================
    # DETECTION METHODS
    # =========================================================================
    
    def _get_paragraphs(self) -> List[etree._Element]:
        """Get all paragraphs in document body."""
        return self.body.findall(f'{W}p')
    
    def _get_para_text(self, para: etree._Element) -> str:
        """Get plain text from paragraph (including track changes)."""
        texts = []
        for t in para.findall(f'.//{W}t'):
            if t.text:
                texts.append(t.text)
        return ''.join(texts)
    
    def is_vibelegal_insertion(self, para: etree._Element) -> bool:
        """
        Check if this paragraph was inserted by VibeLegal.
        
        Looks for w:ins elements with author matching our author name.
        A paragraph is "our insertion" if it's wrapped in our w:ins.
        """
        # Check for direct w:ins children
        for ins in para.findall(f'{W}ins'):
            author = ins.get(f'{W}author')
            if author == self.author:
                return True
        
        # Check if all runs are inside our w:ins
        runs = para.findall(f'{W}r')
        ins_runs = para.findall(f'{W}ins/{W}r')
        
        if len(ins_runs) > 0 and len(runs) == 0:
            # All content is in w:ins - check author
            for ins in para.findall(f'{W}ins'):
                if ins.get(f'{W}author') == self.author:
                    return True
        
        return False
    
    def detect_section_header(self, text: str) -> bool:
        """
        Detect if text looks like a section header.
        
        Pattern:
        - Short (under 60 chars)
        - Ends with colon
        - ALL CAPS or Title Case
        """
        text = text.strip()
        if len(text) > 60:
            return False
        
        # KEYWORD MATCHING (Legal headers)
        heading_patterns = [
            r'^(ARTICLE|SECTION|PART|SCHEDULE|EXHIBIT|ANNEX)\s+',
            r'^(DEFINITIONS?|INTERPRETATION|RECITALS?|BACKGROUND|PARTIES)',
            r'^(TERMS|CONDITIONS|OBLIGATIONS|REPRESENTATIONS|WARRANTIES)',
            r'^(CONFIDENTIAL|NON-DISCLOSURE|GOVERNING LAW|JURISDICTION)',
        ]
        for pattern in heading_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return True
        
        # PATTERN MATCHING (Ends with colon)
        if text.endswith(':'):
            # Remove trailing colon for case check
            check_text = text[:-1].strip()
            if check_text.isupper() or check_text.istitle():
                return True
        
        return False
    
    def _is_numbered_manual(self, text: str) -> bool:
        """Check if text starts with manual clause number (1., 2., 1.1, etc.)"""
        return bool(re.match(r'^\d+[\.\)]', text.strip()))
    
    def _extract_manual_number(self, text: str) -> Optional[str]:
        """Extract the manual clause number from text."""
        match = re.match(r'^(\d+(?:\.\d+)*)[\.\)]', text.strip())
        if match:
            return match.group(1)
        return None
    
    def _has_numPr(self, para: etree._Element) -> bool:
        """Check if paragraph has Word numbering (numPr)."""
        pPr = para.find(f'{W}pPr')
        if pPr is not None:
            return pPr.find(f'{W}numPr') is not None
        return False
    
    def _is_numbered_clause(self, para: etree._Element) -> bool:
        """Check if paragraph is a numbered clause (short, starts with number or has numPr)."""
        text = self._get_para_text(para)
        # Manual numbering
        if re.match(r'^\d+\.?\s', text):
            return True
        # Word auto-numbering + short text (heading style)
        if self._has_numPr(para) and len(text) < 100:
            return True
        return False
    
    def _is_body_paragraph(self, para: etree._Element) -> bool:
        """Body = long text, not starting with number."""
        text = self._get_para_text(para)
        if len(text) < 50:
            return False
        # Not a numbered heading like "2. Exclusions"
        if re.match(r'^\d+\.?\s', text):
            return False
        return True
    
    def _get_left_indent(self, para: etree._Element) -> Optional[int]:
        """Get left indent in twips from paragraph properties."""
        pPr = para.find(f'{W}pPr')
        if pPr is not None:
            ind = pPr.find(f'{W}ind')
            if ind is not None:
                left = ind.get(f'{W}left')
                if left:
                    try:
                        return int(left)
                    except ValueError:
                        pass
        return None
    
    def _set_left_indent(self, para: etree._Element, indent: int) -> None:
        """Set left indent in twips on paragraph properties."""
        pPr = para.find(f'{W}pPr')
        if pPr is None:
            pPr = etree.SubElement(para, f'{W}pPr')
            para.insert(0, pPr)  # pPr must be first child
        
        ind = pPr.find(f'{W}ind')
        if ind is None:
            ind = etree.SubElement(pPr, f'{W}ind')
        
        ind.set(f'{W}left', str(indent))
    
    def _is_run_bold(self, run: etree._Element) -> bool:
        """Check if a run has bold formatting."""
        rPr = run.find(f'{W}rPr')
        if rPr is not None:
            return rPr.find(f'{W}b') is not None
        return False
    
    def _add_bold_to_run(self, run: etree._Element) -> None:
        """Add bold formatting to a run."""
        rPr = run.find(f'{W}rPr')
        if rPr is None:
            rPr = etree.Element(f'{W}rPr')
            run.insert(0, rPr)  # rPr must be first child
        if rPr.find(f'{W}b') is None:
            etree.SubElement(rPr, f'{W}b')
    
    def detect_role(self, para: etree._Element) -> str:
        """
        Determine the role of a paragraph.
        
        Returns: SECTION_HEAD, CLAUSE, BULLET, LIST_ITEM, BODY, UNKNOWN
        """
        text = self._get_para_text(para).strip()
        
        # 1. Section header: ends with ":", short
        if text.endswith(':') and len(text) < 60:
            return 'SECTION_HEAD'
        
        # 2. Clause heading: "2. Exclusions" (number + short)
        if re.match(r'^\d+\.\s*\w', text) and len(text) < 50:
            return 'CLAUSE'
        
        # 3. Bullet: has bullet numPr
        # Note: Need _has_bullet_numPr impl or similar check
        if self._has_bullet_numPr(para):
            return 'BULLET'
        
        # 4. Numbered list item (not clause): has numPr but longer
        if self._has_numPr(para):
            return 'LIST_ITEM'
        
        # 5. Body: everything else that's long
        if len(text) > 50:
            return 'BODY'
        
        return 'UNKNOWN'

    def _has_bullet_numPr(self, para: etree._Element) -> bool:
        """Check if paragraph has bullet numbering."""
        pPr = para.find(f'{W}pPr')
        if pPr is None: return False
        numPr = pPr.find(f'{W}numPr')
        if numPr is None: return False
        
        # Simplified check: if ilvl is present, it's likely a list
        # To strictly check for bullet vs number requires numId lookup which is complex
        # For now, we rely on text pattern or assume numPr with short text is list
        return True

    def _count_number_track_changes(self, para: etree._Element) -> Tuple[int, int]:
        """
        Count number-related deletions and insertions in paragraph.
        
        Returns (deletion_count, insertion_count) for number-like text.
        """
        del_count = 0
        ins_count = 0
        
        # Check deletions
        for del_elem in para.findall(f'.//{W}del'):
            del_text = ''.join(t.text or '' for t in del_elem.findall(f'.//{W}delText'))
            if re.match(r'^\d+[\.\)]?\s*$', del_text.strip()):
                del_count += 1
        
        # Check insertions
        for ins in para.findall(f'.//{W}ins'):
            ins_text = ''.join(t.text or '' for t in ins.findall(f'.//{W}t'))
            if re.match(r'^\d+[\.\)]?\s*$', ins_text.strip()):
                ins_count += 1
        
        return del_count, ins_count
    
    # =========================================================================
    # REFERENCE FORMAT DETECTION
    # =========================================================================
    
    # REFERENCE FORMAT DETECTION
    # =========================================================================
    
    def detect_reference_formats(self) -> dict:
        """
        Scan original paragraphs and extract reference formats.
        Returns patterns for inline title bold and body indent.
        
        NOTE: When running on original document, is_vibelegal_insertion will be False,
        so checking it is fine (it won't exclude anything).
        """
        paragraphs = self._get_paragraphs()
        
        ref = {
            'inline_title': {
                'has_pattern': False,
                'title_is_bold': False,
                'title_ends_with': '.'
            },
            'section_header': {
                'is_bold': True # Default assumption if no headers found
            },
            'body_indent': {
                'left_indent': None
            }
        }
        
        # Detect section header style
        found_header = False
        for para in paragraphs:
            if self.is_vibelegal_insertion(para):
                continue
            
            text = self._get_para_text(para)
            if self.detect_section_header(text):
                # found a header, check if it is bold
                # it's bold if any significant run is bold
                is_bold = False
                for run in para.findall(f'.//{W}r'):
                    if self._is_run_bold(run):
                        is_bold = True
                        break
                
                ref['section_header']['is_bold'] = is_bold
                logger.info(f"Reference: section header bold = {is_bold} ('{text[:20]}...')")
                found_header = True
                break
        
        # If no headers found in original doc, assume Bold (standard legal style)
        if not found_header:
             logger.info("Reference: no original section headers found, defaulting to BOLD")
        
        # Detect inline title pattern from original numbered clauses
        for para in paragraphs:
            if self.is_vibelegal_insertion(para):
                continue
            if not self._is_numbered_clause(para):
                continue
            
            # Check first run for bold
            runs = para.findall(f'.//{W}r')
            if not runs:
                continue
            
            first_run = runs[0]
            first_text_elem = first_run.find(f'{W}t')
            if first_text_elem is None or not first_text_elem.text:
                continue
            
            first_text = first_text_elem.text
            
            # Check if first run has inline title pattern
            if '.' in first_text[:30]:
                ref['inline_title']['has_pattern'] = True
                ref['inline_title']['title_is_bold'] = self._is_run_bold(first_run)
                logger.info(f"Reference: inline title bold = {ref['inline_title']['title_is_bold']}")
                break  # Found pattern
        
        # Detect body indent from original body paragraphs
        for para in paragraphs:
            if self.is_vibelegal_insertion(para):
                continue
            if not self._is_body_paragraph(para):
                continue
            
            indent = self._get_left_indent(para)
            if indent is not None and indent > 0:
                ref['body_indent']['left_indent'] = indent
                logger.info(f"Reference: body indent = {indent}")
                break  # Found pattern
        
        # Detect spacing from original paragraphs
        ref['spacing'] = {'space_after': None}
        for para in paragraphs:
            if self.is_vibelegal_insertion(para):
                continue
            
            spacing_after = self._get_spacing_after(para)
            if spacing_after is not None and spacing_after > 0:
                ref['spacing']['space_after'] = spacing_after
                logger.info(f"Reference: space_after = {spacing_after}")
                break  # Found pattern
        
        return ref
    
    # =========================================================================
    # STRUCTURE FIXES
    # =========================================================================
    
    def fix_manual_numbering(self) -> List[str]:
        """
        Fix overlapping number track changes in manually numbered documents.
        
        Pattern detected: [2.][3.]3. Obligations
        Pattern fixed: [2.→3.] Obligations
        
        Processes top-to-bottom so each paragraph gets correct position.
        """
        fixes = []
        paragraphs = self._get_paragraphs()
        clause_counter = 0
        
        for i, para in enumerate(paragraphs):
            text = self._get_para_text(para)
            
            # Skip non-numbered paragraphs
            if not self._is_numbered_manual(text):
                continue
            
            clause_counter += 1
            correct_num = clause_counter
            
            # Check for overlapping track changes
            del_count, ins_count = self._count_number_track_changes(para)
            
            if del_count + ins_count > 1:
                # Overlapping! This needs fixing
                logger.info(f"Styler: p{i} has {del_count} deletions, {ins_count} insertions")
                
                if self.is_vibelegal_insertion(para):
                    # We inserted this - just log for now
                    # Direct modification requires more complex XML manipulation
                    fixes.append(f"p{i}: numbering overlap detected (VibeLegal insertion)")
                else:
                    # Original paragraph - would need clean track change
                    fixes.append(f"p{i}: numbering overlap detected (original paragraph)")
                
                # TODO: Implement actual fix:
                # 1. Clear all number-related track changes
                # 2. Apply single clean track change: [old→new]
        
        return fixes
    
    def fix_section_header_format(self) -> List[str]:
        """
        Fix section headers that got wrong format (BULLET instead of BOLD).
        
        Only fixes VibeLegal insertions (direct modification, no track change).
        Flags original paragraphs for human review.
        """
        fixes = []
        paragraphs = self._get_paragraphs()
        
        for i, para in enumerate(paragraphs):
            text = self._get_para_text(para)
            
            # Check if this looks like a section header
            if not self.detect_section_header(text):
                continue
            
            # Check reference: should section headers be bold?
            should_be_bold = self.reference_formats.get('section_header', {}).get('is_bold', True)
            
            if self.is_vibelegal_insertion(para):
                # 1. Handle Bullet -> Header conversion
                if self._has_numPr(para):
                    if should_be_bold:
                        if self._convert_to_bold_header(para):
                            fixes.append(f"p{i}: converted BULLET→BOLD header '{text[:30]}...'")
                    else:
                         if self._remove_bullet_only(para):
                             fixes.append(f"p{i}: removed BULLET from header '{text[:30]}...' (original not bold)")
                
                # 2. Handle Plain -> Bold Header enforcement
                elif should_be_bold and not self._is_run_bold(para.find(f'.//{W}r')):
                    # If not already bold, apply bold
                     if self._convert_to_bold_header(para): # Reusing this as it sets bold (ignoring numPr absence)
                        fixes.append(f"p{i}: applied BOLD to section header '{text[:30]}...'")
            
            else:
                 # Original paragraph - flag only if bulleted
                 if self._has_numPr(para):
                    self.warnings.append(f"⚠️ p{i}: section header has bullet format (original paragraph)")
        
        return fixes
    
    def _convert_to_bold_header(self, para: etree._Element) -> bool:
        """
        Convert a BULLET paragraph to a BOLD header.
        
        1. Remove numPr from pPr
        2. Apply bold formatting to all runs
        """
        # Remove numPr
        pPr = para.find(f'{W}pPr')
        if pPr is not None:
            numPr = pPr.find(f'{W}numPr')
            if numPr is not None:
                pPr.remove(numPr)
        
        # Apply bold to all runs (including those inside w:ins)
        for run in para.findall(f'.//{W}r'):
            rPr = run.find(f'{W}rPr')
            if rPr is None:
                rPr = etree.Element(f'{W}rPr')
                run.insert(0, rPr)
            if rPr.find(f'{W}b') is None:
                etree.SubElement(rPr, f'{W}b')
        
        return True
    
    def _remove_bullet_only(self, para: etree._Element) -> bool:
        """
        Remove bullet/numbering from paragraph (delete numPr).
        Does NOT apply bold.
        """
        pPr = para.find(f'{W}pPr')
        if pPr is not None:
            numPr = pPr.find(f'{W}numPr')
            if numPr is not None:
                pPr.remove(numPr)
                return True
        return False

    def fix_inline_title_bold(self) -> List[str]:
        """
        If original numbered clauses have bold inline titles,
        apply bold to inserted numbered clauses.
        
        Handles two patterns:
        - Short heading (< 50 chars): bold entire paragraph
        - Long clause with inline title: bold up to first "."
        """
        fixes = []
        ref = self.reference_formats.get('inline_title', {})
        
        if not ref.get('has_pattern') or not ref.get('title_is_bold'):
            return fixes
        
        paragraphs = self._get_paragraphs()
        
        for i, para in enumerate(paragraphs):
            if not self.is_vibelegal_insertion(para):
                continue
            if not self._is_numbered_clause(para):
                continue
            
            text = self._get_para_text(para)
            
            # Short heading (< 50 chars) → bold entire paragraph
            if len(text) < 50:
                self._apply_bold_entire(para)
                fixes.append(f"p{i}: applied bold to heading '{text[:30]}...'")
            # Long clause with inline title → bold up to "."
            elif '.' in text[:40]:
                title_end = text.find('.') + 1
                title_text = text[:title_end]
                self._apply_bold_to_title(para, title_end)
                fixes.append(f"p{i}: applied bold to '{title_text}'")
        
        return fixes
    
    def _apply_bold_to_title(self, para: etree._Element, title_end: int) -> None:
        """Bold text up to title_end character position."""
        char_pos = 0
        
        for run in para.findall(f'.//{W}r'):
            t = run.find(f'{W}t')
            if t is None or not t.text:
                continue
            
            run_end = char_pos + len(t.text)
            
            # If run overlaps with title range, apply bold
            if char_pos < title_end:
                self._add_bold_to_run(run)
            
            char_pos = run_end
            
            # Stop once past title
            if char_pos >= title_end:
                break
    
    def _apply_bold_entire(self, para: etree._Element) -> None:
        """Apply bold to entire paragraph (for heading style)."""
        for run in para.findall(f'.//{W}r'):
            self._add_bold_to_run(run)
    
    def fix_body_indentation(self) -> List[str]:
        """
        If original body paragraphs are indented,
        apply same indent to inserted body paragraphs.
        """
        fixes = []
        ref = self.reference_formats.get('body_indent', {})
        ref_indent = ref.get('left_indent')
        
        if ref_indent is None or ref_indent == 0:
            return fixes
        
        paragraphs = self._get_paragraphs()
        
        for i, para in enumerate(paragraphs):
            if not self.is_vibelegal_insertion(para):
                continue
            if not self._is_body_paragraph(para):
                continue
            
            actual_indent = self._get_left_indent(para) or 0
            
            if actual_indent != ref_indent:
                self._set_left_indent(para, ref_indent)
                fixes.append(f"p{i}: set indent to {ref_indent}")
        
        return fixes
    
    def fix_double_numbering(self) -> List[str]:
        """
        Fix double numbering like "3.3. Obligations" caused by
        track change del+ins overlapping with existing number.
        
        Pattern: "3.3." where first "3" is from w:ins, second "3." is original text
        Fix: Remove the duplicate by detecting the pattern in visible text
        """
        fixes = []
        paragraphs = self._get_paragraphs()
        
        for i, para in enumerate(paragraphs):
            text = self._get_para_text(para)
            
            # Detect double number pattern: "3.3." or "5.5." at start
            match = re.match(r'^(\d+)\.(\d+)\.', text)
            if not match:
                continue
            
            # Check if this paragraph has both del and ins of numbers
            del_count, ins_count = self._count_number_track_changes(para)
            
            if del_count > 0 and ins_count > 0:
                # This is likely a renumbering that created duplication
                # Find the duplicated number text and remove it
                if self._remove_duplicate_number_text(para, match.group(2) + '.'):
                    fixes.append(f"p{i}: removed duplicate number '{match.group(2)}.'")
        
        return fixes
    
    def _remove_duplicate_number_text(self, para: etree._Element, dup_text: str) -> bool:
        """
        Remove duplicate number text from paragraph.
        The duplicate is usually in a regular run (not inside w:ins or w:del).
        """
        # Find runs that are NOT inside w:ins or w:del
        for run in para.findall(f'{W}r'):  # Direct children only
            t = run.find(f'{W}t')
            if t is not None and t.text:
                if t.text.startswith(dup_text):
                    # Remove the duplicate portion
                    t.text = t.text[len(dup_text):].lstrip()
                    return True
        return False
    
    def fix_paragraph_spacing(self) -> List[str]:
        """
        Apply reference spacing to VibeLegal insertions.
        Matches space_after from original paragraphs.
        Skips list items which should have compact spacing.
        """
        fixes = []
        ref_spacing = self.reference_formats.get('spacing', {})
        ref_after = ref_spacing.get('space_after')
        
        if ref_after is None or ref_after == 0:
            return fixes
        
        paragraphs = self._get_paragraphs()
        
        for i, para in enumerate(paragraphs):
            if not self.is_vibelegal_insertion(para):
                continue
            
            # Check role to skip list items
            role = self.detect_role(para)
            if role in ['BULLET', 'LIST_ITEM']:
                continue
            
            actual_after = self._get_spacing_after(para) or 0
            
            if actual_after != ref_after:
                self._set_spacing(para, after=ref_after)
                fixes.append(f"p{i}: set space_after to {ref_after}")
        
        return fixes
    
    def _get_spacing_after(self, para: etree._Element) -> Optional[int]:
        """Get space after value from paragraph properties."""
        pPr = para.find(f'{W}pPr')
        if pPr is not None:
            spacing = pPr.find(f'{W}spacing')
            if spacing is not None:
                after = spacing.get(f'{W}after')
                if after:
                    try:
                        return int(after)
                    except ValueError:
                        pass
        return None
    
    def _set_spacing(self, para: etree._Element, before: int = None, after: int = None) -> None:
        """Set paragraph spacing."""
        pPr = para.find(f'{W}pPr')
        if pPr is None:
            pPr = etree.SubElement(para, f'{W}pPr')
            para.insert(0, pPr)  # pPr must be first child
        
        spacing = pPr.find(f'{W}spacing')
        if spacing is None:
            spacing = etree.SubElement(pPr, f'{W}spacing')
        
        if after is not None:
            spacing.set(f'{W}after', str(after))
        if before is not None:
            spacing.set(f'{W}before', str(before))
    
    def check_list_formatting(self) -> List[str]:
        """
        Check that inserted BULLET/NUMBERED paragraphs have numPr.
        
        Flags paragraphs that should be list items but are missing list formatting.
        """
        warnings = []
        paragraphs = self._get_paragraphs()
        
        for i, para in enumerate(paragraphs):
            if not self.is_vibelegal_insertion(para):
                continue
            
            text = self._get_para_text(para)
            
            # Check if it looks like a list item but lacks numPr
            # Patterns: (a), (i), •, -, 1., etc.
            list_patterns = [
                r'^\([a-z]\)',        # (a), (b)
                r'^\([ivx]+\)',       # (i), (ii)
                r'^[•\-\*]\s',        # bullet characters
            ]
            
            is_list_like = any(re.match(p, text.strip()) for p in list_patterns)
            
            if is_list_like and not self._has_numPr(para):
                warnings.append(f"⚠️ p{i}: appears to be list item but missing numPr formatting")
        
        return warnings
    
    # =========================================================================
    # LOGIC CHECKS
    # =========================================================================
    
    def check_clause_sequence(self) -> List[str]:
        """
        Check that clause numbers are sequential (no gaps or duplicates).
        """
        warnings = []
        paragraphs = self._get_paragraphs()
        
        last_num = 0
        
        for i, para in enumerate(paragraphs):
            text = self._get_para_text(para)
            num_str = self._extract_manual_number(text)
            
            if num_str is None:
                continue
            
            # Handle simple numbers (1, 2, 3) not sub-numbers (1.1, 1.2)
            if '.' in num_str:
                continue  # Skip sub-clauses for now
            
            try:
                num = int(num_str)
            except ValueError:
                continue
            
            if num == last_num:
                warnings.append(f"⚠️ Duplicate clause number: {num} at p{i}")
            elif num > last_num + 1:
                warnings.append(f"⚠️ Gap in numbering: jumps from {last_num} to {num} at p{i}")
            
            last_num = num
        
        return warnings
    
    def check_section_placement(self) -> List[str]:
        """
        Check that clauses are in logical sections.
        
        Flags potentially misplaced clauses like:
        - "Limitation of Liability" under OBLIGATIONS section
        - "Governing Law" not near the end
        """
        warnings = []
        paragraphs = self._get_paragraphs()
        
        # Keywords that suggest specific section placement
        section_keywords = {
            'limitation of liability': 'risk_allocation',
            'liability cap': 'risk_allocation',
            'indemnification': 'risk_allocation',
            'governing law': 'at_end',
            'jurisdiction': 'at_end',
            'term': 'middle',
            'duration': 'middle',
            'survival': 'middle',
        }
        
        total_paras = len(paragraphs)
        
        for i, para in enumerate(paragraphs):
            text = self._get_para_text(para).lower()
            
            for keyword, expected_location in section_keywords.items():
                if keyword in text:
                    position = i / total_paras if total_paras > 0 else 0
                    
                    if expected_location == 'at_end' and position < 0.7:
                        warnings.append(f"⚠️ '{keyword}' is at p{i} ({position:.0%}) - usually near end")
                    elif expected_location == 'at_start' and position > 0.3:
                        warnings.append(f"⚠️ '{keyword}' is at p{i} ({position:.0%}) - usually near start")
        
        return warnings
    
    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================
    
    def run(self) -> StylerResult:
        """
        Run all styler passes and return result.
        """
        logger.info("Styler: Starting STRUCTURE pass")
        
        # Structure fixes (auto-apply)
        numbering_fixes = self.fix_manual_numbering()
        self.fixes_applied.extend(numbering_fixes)
        
        header_fixes = self.fix_section_header_format()
        self.fixes_applied.extend(header_fixes)
        
        # NEW: Formatting match fixes
        # Fix double numbering first (most visible)
        double_num_fixes = self.fix_double_numbering()
        self.fixes_applied.extend(double_num_fixes)
        
        title_fixes = self.fix_inline_title_bold()
        self.fixes_applied.extend(title_fixes)
        
        indent_fixes = self.fix_body_indentation()
        self.fixes_applied.extend(indent_fixes)
        
        spacing_fixes = self.fix_paragraph_spacing()
        self.fixes_applied.extend(spacing_fixes)
        
        # Structure checks (warnings)
        list_warnings = self.check_list_formatting()
        self.warnings.extend(list_warnings)
        
        logger.info("Styler: Starting LOGIC pass")
        
        # Logic checks (warnings only)
        sequence_warnings = self.check_clause_sequence()
        self.warnings.extend(sequence_warnings)
        
        placement_warnings = self.check_section_placement()
        self.warnings.extend(placement_warnings)
        
        # Serialize modified document
        modified_bytes = self._save()
        
        logger.info(f"Styler: Complete - {len(self.fixes_applied)} fixes, {len(self.warnings)} warnings")
        
        return StylerResult(
            fixes_applied=self.fixes_applied,
            warnings=self.warnings,
            modified_bytes=modified_bytes
        )
    
    def save(self) -> bytes:
        """Public method to save the modified document to bytes."""
        return self._save()
    
    def _save(self) -> bytes:
        """Serialize the modified document to bytes."""
        # Create new docx with modified document.xml
        output = BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as out_zip:
            for item in self.zf.namelist():
                if item == 'word/document.xml':
                    # Write modified XML
                    out_zip.writestr(item, etree.tostring(self.tree, xml_declaration=True, encoding='UTF-8'))
                else:
                    # Copy unchanged
                    out_zip.writestr(item, self.zf.read(item))
        
        self.zf.close()
        return output.getvalue()
