"""
Style-aware editor for legal documents.
Detects and matches formatting styles for inserts.

v0.7 FIXES:
- Never copy numPr or pStyle (avoids duplicate numbering)
- Strip leading numbers from AI content (AI shouldn't provide numbers)
- Proper insert ordering with element tracking
- Better text normalization for quote/apostrophe matching
"""

from io import BytesIO
from copy import deepcopy
from lxml import etree
import zipfile
import re
import logging
from datetime import datetime
from typing import Optional
from diff_match_patch import diff_match_patch

logger = logging.getLogger(__name__)

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def strip_leading_number(text: str) -> str:
    """
    Remove leading clause numbers from text.
    AI shouldn't include numbers - they're added by the system or Word.
    
    Examples:
        "5. Return of Information" -> "Return of Information"
        "2A. Exclusions" -> "Exclusions"
        "1.1 Sub-clause" -> "Sub-clause"
        "Return of Information" -> "Return of Information" (unchanged)
    """
    if not text:
        return text
    
    # Pattern: optional number(s) with dots, optional letter, then dot/space
    patterns = [
        r'^\d+[A-Za-z]?\.\s+',      # "5. ", "2A. ", "1. "
        r'^\d+\.\d+\.?\s+',          # "1.1 ", "1.1. "
        r'^\d+\.\d+\.\d+\.?\s+',     # "1.1.1 ", "1.1.1. "
        r'^\([a-z]\)\s+',            # "(a) ", "(b) "
        r'^\([ivxlc]+\)\s+',         # "(i) ", "(ii) "
    ]
    
    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if match:
            return text[match.end():]
    
    return text


class StyleAwareEditor:
    
    def __init__(self, docx_bytes: bytes, author: str = "VibeLegal"):
        self.original_bytes = docx_bytes
        self.author = author
        
        with zipfile.ZipFile(BytesIO(docx_bytes), 'r') as zf:
            self.document_xml = zf.read('word/document.xml')
        
        self.root = etree.fromstring(self.document_xml)
        self.body = self.root.find(f'{W}body')
        self.next_id = self._find_max_id() + 1
        
        # Track last inserted paragraph ELEMENT for each anchor index
        # This ensures proper ordering when multiple inserts target same anchor
        self._last_insert_at: dict = {}
    
    @classmethod
    def from_file(cls, file_path: str, author: str = "VibeLegal"):
        """Create editor from file path."""
        with open(file_path, 'rb') as f:
            return cls(f.read(), author)
    
    def _find_max_id(self) -> int:
        max_id = 0
        for elem in self.root.iter():
            id_val = elem.get(f'{W}id')
            if id_val and id_val.isdigit():
                max_id = max(max_id, int(id_val))
        return max_id
    
    def _get_next_id(self) -> str:
        id_val = self.next_id
        self.next_id += 1
        return str(id_val)
    
    def _create_tc_attrs(self) -> dict:
        return {
            f'{W}id': self._get_next_id(),
            f'{W}author': self.author,
            f'{W}date': datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ')
        }
    
    def _get_paragraphs(self):
        return self.body.findall(f'{W}p')
    
    def _get_para_text(self, p) -> str:
        return ''.join(t.text or '' for t in p.iter(f'{W}t'))
    
    def get_all_text(self) -> list:
        return [self._get_para_text(p) for p in self._get_paragraphs()]
    
    def _has_word_numbering(self, para) -> bool:
        pPr = para.find(f'{W}pPr')
        if pPr is not None:
            return pPr.find(f'{W}numPr') is not None
        return False
    
    def _normalize_text(self, text: str) -> str:
        """
        Normalize text for comparison.
        Handles curly quotes, apostrophes, dashes, whitespace.
        """
        if not text:
            return ""
        
        # Normalize quotes
        text = text.replace('"', '"').replace('"', '"')
        text = text.replace("'", "'").replace("'", "'")
        text = text.replace("'", "'")
        
        # Normalize dashes
        text = text.replace("–", "-").replace("—", "-")
        
        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip().lower()
    
    def _diff_words(self, old_text: str, new_text: str) -> list:
        """
        Word-level diff like lawyers do (not character-level).
        
        Lawyers redline at word boundaries, not mid-word.
        Example:
          Character diff: DELETE "English law and t" + INSERT "the laws..."
          Word diff:      DELETE "English law and the" + INSERT "the laws..."
        
        Returns list of (op, text) tuples where op is:
          0  = EQUAL (unchanged)
          -1 = DELETE
          1  = INSERT
        """
        import re
        dmp = diff_match_patch()
        
        # Tokenize into words (preserving spaces)
        def tokenize(text):
            return re.findall(r'\S+|\s+', text)
        
        old_tokens = tokenize(old_text)
        new_tokens = tokenize(new_text)
        
        # Create char mapping (each unique token = one char)
        token_to_char = {}
        char_to_token = []
        
        def encode(tokens):
            result = []
            for token in tokens:
                if token not in token_to_char:
                    token_to_char[token] = chr(len(char_to_token))
                    char_to_token.append(token)
                result.append(token_to_char[token])
            return ''.join(result)
        
        old_encoded = encode(old_tokens)
        new_encoded = encode(new_tokens)
        
        # Diff the encoded strings (word-by-word)
        diffs = dmp.diff_main(old_encoded, new_encoded)
        dmp.diff_cleanupSemantic(diffs)
        
        # Decode back to human-readable text
        result = []
        for op, chars in diffs:
            text = ''.join(char_to_token[ord(c)] for c in chars)
            if text:  # Skip empty
                result.append((op, text))
        
        return result
    
    def _find_para_containing(self, text: str):
        """Find paragraph containing the given text (normalized comparison)."""
        text_norm = self._normalize_text(text)
        for idx, p in enumerate(self._get_paragraphs()):
            p_text = self._get_para_text(p)
            p_norm = self._normalize_text(p_text)
            if text_norm in p_norm:
                return idx, p
        return None
    
    def _copy_safe_properties(self, src_para) -> etree._Element:
        """
        Copy ONLY safe paragraph properties (ind, spacing).
        
        CRITICAL FIX: NEVER copy:
        - numPr (causes Word auto-numbering = duplicate numbers)
        - pStyle (may reference a numbered style)
        """
        new_pPr = etree.Element(f'{W}pPr')
        
        src_pPr = src_para.find(f'{W}pPr')
        if src_pPr is not None:
            # ONLY copy indentation and spacing - nothing else
            for prop in ['ind', 'spacing']:
                elem = src_pPr.find(f'{W}{prop}')
                if elem is not None:
                    new_pPr.append(deepcopy(elem))
        
        return new_pPr if len(new_pPr) > 0 else None
    
    def _get_insert_target(self, after_index: int):
        """
        Get the paragraph to insert after, respecting previous inserts.
        
        CRITICAL FIX: Returns the actual ELEMENT to use with addnext(),
        ensuring proper ordering when multiple inserts target same anchor.
        """
        paragraphs = self._get_paragraphs()
        if after_index < 0 or after_index >= len(paragraphs):
            return None, None
        
        original_para = paragraphs[after_index]
        
        # Check if we've already inserted after this anchor
        if after_index in self._last_insert_at:
            target = self._last_insert_at[after_index]
            # Verify the element is still in the tree
            if target.getparent() is not None:
                return target, original_para
        
        return original_para, original_para
    
    def detect_inline_title_style(self, para_index: int) -> str:
        """Detect if nearby paragraphs use bold, underline, or plain titles."""
        paragraphs = self._get_paragraphs()
        if para_index >= len(paragraphs):
            return 'plain'
        
        check_indices = [para_index]
        if para_index > 0:
            check_indices.append(para_index - 1)
        if para_index < len(paragraphs) - 1:
            check_indices.append(para_index + 1)
        
        for idx in check_indices:
            para = paragraphs[idx]
            text = self._get_para_text(para)
            
            if len(text) < 20:
                continue
            
            first_run = para.find(f'{W}r')
            if first_run is None:
                continue
            
            rPr = first_run.find(f'{W}rPr')
            if rPr is not None:
                if rPr.find(f'{W}b') is not None:
                    return 'bold'
                if rPr.find(f'{W}u') is not None:
                    return 'underline'
            
            if '. ' in text[:50]:
                return 'plain'
        
        return 'none'
    
    def detect_numbering_style(self, para_index: int) -> tuple:
        """Detect numbering style of paragraph."""
        paragraphs = self._get_paragraphs()
        if para_index >= len(paragraphs):
            return 'none', None
        
        para = paragraphs[para_index]
        
        if self._has_word_numbering(para):
            return 'word_list', None
        
        text = self._get_para_text(para).strip()
        
        sub_match = re.match(r'^(\d+\.\d+)', text)
        if sub_match:
            return 'sub', sub_match.group(1)
        
        main_match = re.match(r'^(\d+)\.?\s', text)
        if main_match:
            return 'manual', main_match.group(1) + '.'
        
        return 'none', None
    
    def insert_plain_paragraph(self, after_index: int, text: str) -> bool:
        """Insert a plain paragraph with track changes."""
        target, original_para = self._get_insert_target(after_index)
        if target is None:
            return False
        
        # CRITICAL FIX: Strip any leading numbers from AI content
        text = strip_leading_number(text)
        
        new_para = etree.Element(f'{W}p')
        
        # Copy ONLY safe properties (no numPr, no pStyle)
        safe_pPr = self._copy_safe_properties(original_para)
        if safe_pPr is not None:
            new_para.append(safe_pPr)
        
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        run = etree.SubElement(ins, f'{W}r')
        t = etree.SubElement(run, f'{W}t')
        t.text = text
        
        target.addnext(new_para)
        
        # Track this insert for subsequent inserts at same anchor
        self._last_insert_at[after_index] = new_para
        return True
    
    def insert_with_bold_title(self, after_index: int, title: str, body: str) -> bool:
        """Insert paragraph with bold title and normal body."""
        target, original_para = self._get_insert_target(after_index)
        if target is None:
            return False
        
        # CRITICAL FIX: Strip any leading numbers from AI content
        title = strip_leading_number(title)
        
        new_para = etree.Element(f'{W}p')
        
        # Copy ONLY safe properties
        safe_pPr = self._copy_safe_properties(original_para)
        if safe_pPr is not None:
            new_para.append(safe_pPr)
        
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        
        # Bold title run
        title_run = etree.SubElement(ins, f'{W}r')
        title_rPr = etree.SubElement(title_run, f'{W}rPr')
        etree.SubElement(title_rPr, f'{W}b')
        title_t = etree.SubElement(title_run, f'{W}t')
        title_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        title_t.text = title + ". "
        
        # Normal body run
        body_run = etree.SubElement(ins, f'{W}r')
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = body
        
        target.addnext(new_para)
        
        self._last_insert_at[after_index] = new_para
        return True
    
    def insert_numbered_clause(self, after_index: int, title: str, body: str) -> bool:
        """
        Insert a new numbered clause that joins the document's numbered list.
        
        Use this for TOP-LEVEL clause inserts (e.g., new "4. Return of Information").
        DO NOT use for sub-items or content inside definition lists.
        
        This method DOES copy numPr from nearby paragraphs to continue the numbering.
        """
        target, original_para = self._get_insert_target(after_index)
        if target is None:
            return False
        
        # Strip leading numbers from title
        title = strip_leading_number(title)
        
        new_para = etree.Element(f'{W}p')
        
        # Find numPr from a nearby numbered paragraph
        numPr_source = self._find_nearby_numPr(after_index)
        
        # Create pPr with numbering
        new_pPr = etree.SubElement(new_para, f'{W}pPr')
        
        if numPr_source is not None:
            # Copy the numbering properties to join the list
            new_pPr.append(deepcopy(numPr_source))
        
        # Also copy indentation and spacing from original
        src_pPr = original_para.find(f'{W}pPr')
        if src_pPr is not None:
            ind = src_pPr.find(f'{W}ind')
            if ind is not None:
                new_pPr.append(deepcopy(ind))
            spacing = src_pPr.find(f'{W}spacing')
            if spacing is not None:
                new_pPr.append(deepcopy(spacing))
        
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        
        # Bold title run
        title_run = etree.SubElement(ins, f'{W}r')
        title_rPr = etree.SubElement(title_run, f'{W}rPr')
        etree.SubElement(title_rPr, f'{W}b')
        title_t = etree.SubElement(title_run, f'{W}t')
        title_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        title_t.text = title + ". "
        
        # Normal body run
        body_run = etree.SubElement(ins, f'{W}r')
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = body
        
        target.addnext(new_para)
        
        self._last_insert_at[after_index] = new_para
        return True
    
    def _find_nearby_numPr(self, index: int):
        """Find numPr from a nearby paragraph to use for numbering."""
        paragraphs = self.body.findall(f'{W}p')
        
        # Look at the anchor paragraph and a few before/after
        search_range = range(max(0, index - 3), min(len(paragraphs), index + 3))
        
        for i in search_range:
            para = paragraphs[i]
            pPr = para.find(f'{W}pPr')
            if pPr is not None:
                numPr = pPr.find(f'{W}numPr')
                if numPr is not None:
                    return numPr
        
        return None
    
    def insert_styled_clause(self, after_index: int, title: str, body: str, 
                             heading_style: str = "Heading2") -> bool:
        """
        Insert a new clause with heading style (for documents without Word auto-numbering).
        
        Use this for documents like NDA-Headers-Indented where:
        - Clause headings use a style (e.g., Heading2) with manual numbers
        - Clause body is a separate paragraph below
        
        This method:
        1. Calculates the next clause number from nearby headings
        2. Inserts a heading paragraph with the style
        3. Inserts a body paragraph below
        """
        target, original_para = self._get_insert_target(after_index)
        if target is None:
            return False
        
        # Strip leading numbers from title
        title = strip_leading_number(title)
        
        # Find the next number by looking at nearby headings
        next_num = self._calculate_next_clause_number(after_index, heading_style)
        
        # Create HEADING paragraph with style
        heading_para = etree.Element(f'{W}p')
        heading_pPr = etree.SubElement(heading_para, f'{W}pPr')
        pStyle = etree.SubElement(heading_pPr, f'{W}pStyle')
        pStyle.set(f'{W}val', heading_style)
        
        # Copy spacing from original if available
        src_pPr = original_para.find(f'{W}pPr')
        if src_pPr is not None:
            spacing = src_pPr.find(f'{W}spacing')
            if spacing is not None:
                heading_pPr.append(deepcopy(spacing))
        
        ins_heading = etree.SubElement(heading_para, f'{W}ins', self._create_tc_attrs())
        heading_run = etree.SubElement(ins_heading, f'{W}r')
        heading_t = etree.SubElement(heading_run, f'{W}t')
        heading_t.text = f"{next_num}. {title}"
        
        # Create BODY paragraph
        body_para = etree.Element(f'{W}p')
        body_pPr = etree.SubElement(body_para, f'{W}pPr')
        
        # Copy indentation and spacing from original body paragraphs
        if src_pPr is not None:
            ind = src_pPr.find(f'{W}ind')
            if ind is not None:
                body_pPr.append(deepcopy(ind))
            spacing = src_pPr.find(f'{W}spacing')
            if spacing is not None:
                body_pPr.append(deepcopy(spacing))
        
        ins_body = etree.SubElement(body_para, f'{W}ins', self._create_tc_attrs())
        body_run = etree.SubElement(ins_body, f'{W}r')
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = body
        
        # Insert both: heading first, then body
        target.addnext(heading_para)
        heading_para.addnext(body_para)
        
        # Track the last inserted element (body paragraph)
        self._last_insert_at[after_index] = body_para
        return True
    
    def _calculate_next_clause_number(self, after_index: int, heading_style: str) -> int:
        """Calculate the next clause number based on the heading BEFORE our insert point."""
        paragraphs = self.body.findall(f'{W}p')
        
        # Find the last heading number at or before after_index
        last_num_before = 0
        
        for i in range(min(after_index + 1, len(paragraphs))):
            para = paragraphs[i]
            pPr = para.find(f'{W}pPr')
            if pPr is not None:
                pStyle = pPr.find(f'{W}pStyle')
                if pStyle is not None and pStyle.get(f'{W}val') == heading_style:
                    text = ''.join(t.text or '' for t in para.findall(f'.//{W}t'))
                    match = re.match(r'^(\d+)\.', text)
                    if match:
                        last_num_before = int(match.group(1))
        
        # Also check if we've inserted any headings after after_index already
        # (for sequential inserts at same anchor)
        if after_index in self._last_insert_at:
            last_inserted = self._last_insert_at[after_index]
            # Walk backwards to find the heading of our last insert
            prev = last_inserted.getprevious()
            while prev is not None:
                pPr = prev.find(f'{W}pPr')
                if pPr is not None:
                    pStyle = pPr.find(f'{W}pStyle')
                    if pStyle is not None and pStyle.get(f'{W}val') == heading_style:
                        text = ''.join(t.text or '' for t in prev.findall(f'.//{W}t'))
                        match = re.match(r'^(\d+)\.', text)
                        if match:
                            inserted_num = int(match.group(1))
                            if inserted_num > last_num_before:
                                last_num_before = inserted_num
                        break
                prev = prev.getprevious()
        
        return last_num_before + 1
    
    def insert_section_heading(self, after_index: int, heading_text: str, body_text: str, 
                               spacing_after: bool = False) -> bool:
        """
        Insert a section heading + body paragraph (for bullet-point documents).
        
        Use this for documents where clauses are section headings (bold, no bullets)
        followed by bullet lists, like:
            OBLIGATIONS:
            • Item 1
            • Item 2
        """
        paragraphs = self.body.findall(f'{W}p')
        if after_index < 0 or after_index >= len(paragraphs):
            return False
        
        target = paragraphs[after_index]
        
        if after_index in self._last_insert_at:
            last = self._last_insert_at[after_index]
            if last.getparent() is not None:
                target = last
        
        # HEADING paragraph - bold, with spacing before
        heading_para = etree.Element(f'{W}p')
        pPr = etree.SubElement(heading_para, f'{W}pPr')
        spacing = etree.SubElement(pPr, f'{W}spacing')
        spacing.set(f'{W}before', '240')  # ~12pt space before
        
        ins = etree.SubElement(heading_para, f'{W}ins', self._create_tc_attrs())
        run = etree.SubElement(ins, f'{W}r')
        rPr = etree.SubElement(run, f'{W}rPr')
        etree.SubElement(rPr, f'{W}b')  # Bold
        t = etree.SubElement(run, f'{W}t')
        t.text = heading_text
        
        target.addnext(heading_para)
        
        # BODY paragraph
        body_para = etree.Element(f'{W}p')
        
        if spacing_after:
            body_pPr = etree.SubElement(body_para, f'{W}pPr')
            body_spacing = etree.SubElement(body_pPr, f'{W}spacing')
            body_spacing.set(f'{W}after', '240')  # ~12pt space after
        
        ins_body = etree.SubElement(body_para, f'{W}ins', self._create_tc_attrs())
        body_run = etree.SubElement(ins_body, f'{W}r')
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = body_text
        
        heading_para.addnext(body_para)
        self._last_insert_at[after_index] = body_para
        return True
    
    def insert_manual_numbered_clause(self, after_index: int, number: Optional[int], 
                                       title: str, body: str, bold_title: bool = False) -> bool:
        """
        Insert a clause with manual number like '5. Title. Body...'
        
        If number is None, does NOT prepend number (assumes title has it).
        Also skips stripping if number is None.
        """
        paragraphs = self.body.findall(f'{W}p')
        if after_index < 0 or after_index >= len(paragraphs):
            return False
        
        target = paragraphs[after_index]
        
        if after_index in self._last_insert_at:
            last = self._last_insert_at[after_index]
            if last.getparent() is not None:
                target = last
        
        # Only strip if we are providing the number
        if number is not None:
            title = strip_leading_number(title)
        
        new_para = etree.Element(f'{W}p')
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        
        clause_text = ""
        if number is not None:
            clause_text = f"{number}. "
        
        if bold_title:
            # Bold number + title, normal body
            title_run = etree.SubElement(ins, f'{W}r')
            title_rPr = etree.SubElement(title_run, f'{W}rPr')
            etree.SubElement(title_rPr, f'{W}b')
            title_t = etree.SubElement(title_run, f'{W}t')
            title_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            title_t.text = f"{clause_text}{title}. "
            
            body_run = etree.SubElement(ins, f'{W}r')
            body_t = etree.SubElement(body_run, f'{W}t')
            body_t.text = body
        else:
            # All plain text - no bold
            run = etree.SubElement(ins, f'{W}r')
            t = etree.SubElement(run, f'{W}t')
            t.text = f"{clause_text}{title}. {body}"
        
        target.addnext(new_para)
        self._last_insert_at[after_index] = new_para
        return True
    
    def insert_clause(self, after_index: int, clause_num: Optional[int], 
                      title: str, body: str) -> bool:
        """
        Simplified clause insert - the primary method for the decision tree.
        
        If clause_num is provided, formats as "N. Title. Body"
        If clause_num is None, formats as "Title. Body" (Word handles numbering)
        
        Styling is minimal - the AI corrector will fix formatting later.
        """
        paragraphs = self.body.findall(f'{W}p')
        if after_index < 0 or after_index >= len(paragraphs):
            logger.warning(f"insert_clause: Index {after_index} out of bounds")
            return False
        
        target = paragraphs[after_index]
        
        if after_index in self._last_insert_at:
            last = self._last_insert_at[after_index]
            if last.getparent() is not None:
                target = last
        
        # Build content
        if clause_num is not None:
            content = f"{clause_num}. {title}. {body}"
        else:
            content = f"{title}. {body}" if title else body
        
        # Create paragraph with track changes
        new_para = etree.Element(f'{W}p')
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        run = etree.SubElement(ins, f'{W}r')
        t = etree.SubElement(run, f'{W}t')
        t.text = content
        
        target.addnext(new_para)
        self._last_insert_at[after_index] = new_para
        logger.info(f"insert_clause: Inserted after p{after_index}, num={clause_num}")
        return True
    
    def insert_with_format(self, after_index: int, format_label: str, content: str) -> bool:
        """
        Insert paragraph with specified format - ONE METHOD TO RULE THEM ALL.
        
        AI specifies format, system just applies it. No transformation, no guessing.
        
        Format labels:
        - BOLD: Apply bold to paragraph
        - BULLET: Add bullet formatting  
        - NUMBERED: Add to Word numbering sequence
        - NUMBERED_MANUAL: Insert as-is (AI includes number in content)
        - INDENTED: Apply paragraph indent
        - PLAIN: Just insert text
        - BOLD+INDENTED: Combine formats
        """
        paragraphs = self.body.findall(f'{W}p')
        if after_index < 0 or after_index >= len(paragraphs):
            logger.warning(f"insert_with_format: Index {after_index} out of bounds")
            return False
        
        target = paragraphs[after_index]
        
        # Handle multiple inserts at same position
        if after_index in self._last_insert_at:
            last = self._last_insert_at[after_index]
            if last.getparent() is not None:
                target = last
        
        # Parse combined format labels
        formats = format_label.upper().split('+')
        
        # Create paragraph
        new_para = etree.Element(f'{W}p')
        pPr = etree.SubElement(new_para, f'{W}pPr')
        
        # Apply INDENTED format
        if 'INDENTED' in formats:
            ind = etree.SubElement(pPr, f'{W}ind')
            ind.set(f'{W}left', '720')  # ~0.5 inch indent
        
        # Apply BULLET or NUMBERED format - copy numPr from target or nearby paragraph
        # Both use same OOXML mechanism (numPr), Word distinguishes by numId
        if 'BULLET' in formats or 'NUMBERED' in formats:
            # First try target paragraph
            numPr_found = None
            target_pPr = target.find(f'{W}pPr')
            if target_pPr is not None:
                numPr_found = target_pPr.find(f'{W}numPr')
            
            # If target doesn't have numPr, search nearby paragraphs
            if numPr_found is None:
                paragraphs = self.body.findall(f'{W}p')
                for search_idx in range(max(0, after_index - 3), min(len(paragraphs), after_index + 3)):
                    search_para = paragraphs[search_idx]
                    search_pPr = search_para.find(f'{W}pPr')
                    if search_pPr is not None:
                        numPr_found = search_pPr.find(f'{W}numPr')
                        if numPr_found is not None:
                            logger.info(f"insert_with_format: Found numPr at p{search_idx}")
                            break
            
            if numPr_found is not None:
                import copy
                pPr.append(copy.deepcopy(numPr_found))
                logger.info(f"insert_with_format: Applied numPr for {format_label}")
            else:
                logger.warning(f"insert_with_format: No numPr found for {format_label}, paragraph will be unnumbered")
        
        # Create track change wrapper
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        run = etree.SubElement(ins, f'{W}r')
        
        # Apply BOLD format to run
        if 'BOLD' in formats:
            rPr = etree.SubElement(run, f'{W}rPr')
            etree.SubElement(rPr, f'{W}b')
        
        # Add text
        t = etree.SubElement(run, f'{W}t')
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = content
        
        # Insert after target
        target.addnext(new_para)
        self._last_insert_at[after_index] = new_para
        logger.info(f"insert_with_format: Inserted {format_label} after p{after_index}")
        return True
    
    def insert_with_format_before(self, before_index: int, format_label: str, content: str) -> bool:
        """
        Insert paragraph with specified format BEFORE the target paragraph.
        
        Same formatting logic as insert_with_format, but uses addprevious().
        """
        paragraphs = self.body.findall(f'{W}p')
        if before_index < 0 or before_index >= len(paragraphs):
            logger.warning(f"insert_with_format_before: Index {before_index} out of bounds")
            return False
        
        target = paragraphs[before_index]
        
        # Parse combined format labels
        formats = format_label.upper().split('+')
        
        # Create paragraph
        new_para = etree.Element(f'{W}p')
        pPr = etree.SubElement(new_para, f'{W}pPr')
        
        # Apply INDENTED format
        if 'INDENTED' in formats:
            ind = etree.SubElement(pPr, f'{W}ind')
            ind.set(f'{W}left', '720')
        
        # Apply BULLET or NUMBERED format - copy numPr from target or nearby paragraph
        if 'BULLET' in formats or 'NUMBERED' in formats:
            numPr_found = None
            target_pPr = target.find(f'{W}pPr')
            if target_pPr is not None:
                numPr_found = target_pPr.find(f'{W}numPr')
            
            # If target doesn't have numPr, search nearby paragraphs
            if numPr_found is None:
                for search_idx in range(max(0, before_index - 3), min(len(paragraphs), before_index + 3)):
                    search_para = paragraphs[search_idx]
                    search_pPr = search_para.find(f'{W}pPr')
                    if search_pPr is not None:
                        numPr_found = search_pPr.find(f'{W}numPr')
                        if numPr_found is not None:
                            logger.info(f"insert_with_format_before: Found numPr at p{search_idx}")
                            break
            
            if numPr_found is not None:
                import copy
                pPr.append(copy.deepcopy(numPr_found))
                logger.info(f"insert_with_format_before: Applied numPr for {format_label}")
            else:
                logger.warning(f"insert_with_format_before: No numPr found for {format_label}")
        
        # Create track change wrapper
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        run = etree.SubElement(ins, f'{W}r')
        
        # Apply BOLD format to run
        if 'BOLD' in formats:
            rPr = etree.SubElement(run, f'{W}rPr')
            etree.SubElement(rPr, f'{W}b')
        
        # Add text
        t = etree.SubElement(run, f'{W}t')
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = content
        
        # Insert BEFORE target (key difference)
        target.addprevious(new_para)
        logger.info(f"insert_with_format_before: Inserted {format_label} before p{before_index}")
        return True
    
    def insert_section_with_body(self, after_index: int, heading: str, body: str) -> bool:
        """
        Simplified section insert for bullet-point documents.
        
        Inserts heading (uppercase with colon) followed by body paragraph.
        Styling is minimal - the AI corrector will fix formatting later.
        """
        paragraphs = self.body.findall(f'{W}p')
        if after_index < 0 or after_index >= len(paragraphs):
            logger.warning(f"insert_section_with_body: Index {after_index} out of bounds")
            return False
        
        target = paragraphs[after_index]
        
        if after_index in self._last_insert_at:
            last = self._last_insert_at[after_index]
            if last.getparent() is not None:
                target = last
        
        # Create heading paragraph
        heading_para = etree.Element(f'{W}p')
        ins1 = etree.SubElement(heading_para, f'{W}ins', self._create_tc_attrs())
        run1 = etree.SubElement(ins1, f'{W}r')
        t1 = etree.SubElement(run1, f'{W}t')
        t1.text = heading
        
        # Create body paragraph
        body_para = etree.Element(f'{W}p')
        ins2 = etree.SubElement(body_para, f'{W}ins', self._create_tc_attrs())
        run2 = etree.SubElement(ins2, f'{W}r')
        t2 = etree.SubElement(run2, f'{W}t')
        t2.text = body
        
        # Insert: heading first, then body
        target.addnext(heading_para)
        heading_para.addnext(body_para)
        
        self._last_insert_at[after_index] = body_para
        logger.info(f"insert_section_with_body: Inserted after p{after_index}, heading='{heading[:30]}...'")
        return True
    
    def insert_with_underline_title(self, after_index: int, title: str, body: str) -> bool:
        """Insert paragraph with underlined title and normal body."""
        target, original_para = self._get_insert_target(after_index)
        if target is None:
            return False
        
        # Strip leading numbers
        title = strip_leading_number(title)
        
        new_para = etree.Element(f'{W}p')
        
        safe_pPr = self._copy_safe_properties(original_para)
        if safe_pPr is not None:
            new_para.append(safe_pPr)
        
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        
        title_run = etree.SubElement(ins, f'{W}r')
        title_rPr = etree.SubElement(title_run, f'{W}rPr')
        u_elem = etree.SubElement(title_rPr, f'{W}u')
        u_elem.set(f'{W}val', 'single')
        title_t = etree.SubElement(title_run, f'{W}t')
        title_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        title_t.text = title + ". "
        
        body_run = etree.SubElement(ins, f'{W}r')
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = body
        
        target.addnext(new_para)
        self._last_insert_at[after_index] = new_para
        return True
    
    def smart_insert(self, after_index: int, title: str, body: str, 
                     context_info: dict = None) -> bool:
        """
        Smart insert that matches document style.
        
        CRITICAL FIX: Don't auto-use sub-clause numbering.
        The AI should specify new_role: SUB_CLAUSE explicitly if it wants sub-clauses.
        """
        context_info = context_info or {}
        
        # Strip leading numbers from title (AI shouldn't provide them)
        title = strip_leading_number(title)
        
        title_style = self.detect_inline_title_style(after_index)
        num_style, num_val = self.detect_numbering_style(after_index)
        
        # For Word auto-numbered lists - DON'T inherit numbering
        if num_style == 'word_list':
            if title_style == 'bold':
                return self.insert_with_bold_title(after_index, title, body)
            else:
                return self.insert_plain_paragraph(after_index, f"{title}. {body}")
        
        # For manual numbered documents - just match the title style
        # DON'T auto-create sub-clauses
        if title_style == 'underline':
            return self.insert_with_underline_title(after_index, title, body)
        elif title_style == 'bold':
            return self.insert_with_bold_title(after_index, title, body)
        else:
            return self.insert_plain_paragraph(after_index, f"{title}. {body}")
    
    def amend_text(self, old_text: str, new_text: str, paragraph_index: int = None) -> bool:
        """
        Amend text using diff-match-patch for SURGICAL character-level changes.
        
        Instead of deleting entire old_text and inserting entire new_text,
        this creates minimal track changes for each character difference.
        
        Example: "English law" → "the laws of England"
        Creates: DELETE "English law" + INSERT "the laws of England"
        NOT: DELETE entire clause + INSERT entire clause
        """
        # Find the target paragraph
        if paragraph_index is not None:
            paragraphs = self._get_paragraphs()
            if 0 <= paragraph_index < len(paragraphs):
                para = paragraphs[paragraph_index]
            else:
                return False
        else:
            result = self._find_para_containing(old_text)
            if not result:
                return False
            _, para = result
        
        full_text = self._get_para_text(para)
        
        # Find old_text position in full paragraph text
        match = re.search(re.escape(old_text), full_text, re.IGNORECASE)
        if not match:
            # Try normalized matching
            full_norm = self._normalize_text(full_text)
            old_norm = self._normalize_text(old_text)
            norm_start = full_norm.find(old_norm)
            if norm_start == -1:
                return False
            start_pos = norm_start
            end_pos = start_pos + len(old_text)
        else:
            start_pos = match.start()
            end_pos = match.end()
        
        actual_old = full_text[start_pos:end_pos]
        
        # Use WORD-LEVEL diff like lawyers do (not character-level)
        # This produces redlines at word boundaries, not mid-word
        diffs = self._diff_words(actual_old, new_text)
        
        logger.info(f"amend_text: Word-level diff from '{actual_old[:30]}...' to '{new_text[:30]}...'")
        for op, text in diffs:
            op_name = {0: 'EQUAL', -1: 'DELETE', 1: 'INSERT'}.get(op, 'UNKNOWN')
            logger.info(f"  {op_name}: '{text[:40]}...' " if len(text) > 40 else f"  {op_name}: '{text}'")
        
        # Build character positions mapping
        positions = []
        for run in para.findall(f'{W}r'):
            for t in run.findall(f'{W}t'):
                if t.text:
                    for i, char in enumerate(t.text):
                        positions.append((run, t, i))
        
        if start_pos >= len(positions):
            return False
        
        # Get target run and its properties
        start_run, start_t, start_i = positions[start_pos]
        rPr = start_run.find(f'{W}rPr')
        
        # Build new paragraph content with track changes
        # Strategy: Replace the text element content with surgical changes
        before_text = full_text[:start_pos]
        after_text = full_text[end_pos:]
        
        # Clear the paragraph's runs and rebuild
        for run in list(para.findall(f'{W}r')):
            para.remove(run)
        
        # Rebuild: before text (plain) + diffs (track changes) + after text (plain)
        
        # 1. Before text as plain run
        if before_text:
            before_run = etree.SubElement(para, f'{W}r')
            if rPr is not None:
                before_run.insert(0, deepcopy(rPr))
            before_t = etree.SubElement(before_run, f'{W}t')
            before_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            before_t.text = before_text
        
        # 2. Apply diffs with track changes
        for op, text in diffs:
            if not text:
                continue
                
            if op == 0:  # EQUAL - unchanged text
                equal_run = etree.SubElement(para, f'{W}r')
                if rPr is not None:
                    equal_run.insert(0, deepcopy(rPr))
                equal_t = etree.SubElement(equal_run, f'{W}t')
                equal_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                equal_t.text = text
                
            elif op == -1:  # DELETE
                del_elem = etree.SubElement(para, f'{W}del', self._create_tc_attrs())
                del_run = etree.SubElement(del_elem, f'{W}r')
                if rPr is not None:
                    del_run.insert(0, deepcopy(rPr))
                del_text = etree.SubElement(del_run, f'{W}delText')
                del_text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                del_text.text = text
                
            elif op == 1:  # INSERT
                ins_elem = etree.SubElement(para, f'{W}ins', self._create_tc_attrs())
                ins_run = etree.SubElement(ins_elem, f'{W}r')
                if rPr is not None:
                    ins_run.insert(0, deepcopy(rPr))
                ins_text = etree.SubElement(ins_run, f'{W}t')
                ins_text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                ins_text.text = text
        
        # 3. After text as plain run
        if after_text:
            after_run = etree.SubElement(para, f'{W}r')
            if rPr is not None:
                after_run.insert(0, deepcopy(rPr))
            after_t = etree.SubElement(after_run, f'{W}t')
            after_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            after_t.text = after_text
        
        logger.info(f"amend_text: Applied surgical changes successfully")
        return True
    
    def delete_paragraph(self, containing_text: str) -> bool:
        result = self._find_para_containing(containing_text)
        if not result:
            return False
        
        idx, para = result
        
        del_elem = etree.Element(f'{W}del', self._create_tc_attrs())
        
        for child in list(para):
            if child.tag == f'{W}r':
                for t in child.findall(f'{W}t'):
                    t.tag = f'{W}delText'
                del_elem.append(child)
        
        pPr = para.find(f'{W}pPr')
        if pPr is not None:
            pPr.addnext(del_elem)
        else:
            para.insert(0, del_elem)
        
        return True
    
    def delete_paragraph_by_index(self, para_index: int) -> bool:
        """Delete paragraph by index (0-based)."""
        paragraphs = self._get_paragraphs()
        if para_index < 0 or para_index >= len(paragraphs):
            logger.warning(f"delete_paragraph_by_index: Index {para_index} out of bounds")
            return False
        
        para = paragraphs[para_index]
        logger.info(f"Deleting paragraph {para_index}")
        
        del_elem = etree.Element(f'{W}del', self._create_tc_attrs())
        
        for child in list(para):
            if child.tag == f'{W}r':
                for t in child.findall(f'{W}t'):
                    t.tag = f'{W}delText'
                del_elem.append(child)
        
        pPr = para.find(f'{W}pPr')
        if pPr is not None:
            pPr.addnext(del_elem)
        else:
            para.insert(0, del_elem)
        
        logger.info(f"Successfully added w:del to paragraph {para_index}")
        return True
    
    def insert_paragraph_before_index(self, para_index: int, text: str) -> bool:
        """Insert paragraph BEFORE the given index."""
        paragraphs = self._get_paragraphs()
        if para_index < 0 or para_index >= len(paragraphs):
            return False
        
        target = paragraphs[para_index]
        
        # Strip leading numbers
        text = strip_leading_number(text)
        
        new_para = etree.Element(f'{W}p')
        
        safe_pPr = self._copy_safe_properties(target)
        if safe_pPr is not None:
            new_para.append(safe_pPr)
        
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        run = etree.SubElement(ins, f'{W}r')
        t = etree.SubElement(run, f'{W}t')
        t.text = text
        
        target.addprevious(new_para)
        return True
    
    def save(self) -> bytes:
        new_xml = etree.tostring(self.root, xml_declaration=True,
                                  encoding='UTF-8', standalone='yes')
        
        output = BytesIO()
        with zipfile.ZipFile(BytesIO(self.original_bytes), 'r') as zf_in:
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as zf_out:
                for item in zf_in.namelist():
                    if item == 'word/document.xml':
                        zf_out.writestr(item, new_xml)
                    else:
                        zf_out.writestr(item, zf_in.read(item))
        
        return output.getvalue()
    
    def save_to_file(self, file_path: str) -> None:
        """Save to file path."""
        with open(file_path, 'wb') as f:
            f.write(self.save())
