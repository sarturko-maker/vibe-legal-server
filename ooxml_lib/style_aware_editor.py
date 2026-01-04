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
from datetime import datetime

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
    
    def insert_manual_numbered_clause(self, after_index: int, number: int, 
                                       title: str, body: str, bold_title: bool = False) -> bool:
        """
        Insert a clause with manual number like '5. Title. Body...'
        
        Use this for documents with manual text numbering (no Word numPr).
        Set bold_title based on whether original document uses bold for clause titles.
        """
        paragraphs = self.body.findall(f'{W}p')
        if after_index < 0 or after_index >= len(paragraphs):
            return False
        
        target = paragraphs[after_index]
        
        if after_index in self._last_insert_at:
            last = self._last_insert_at[after_index]
            if last.getparent() is not None:
                target = last
        
        # Strip any AI-provided numbers
        title = strip_leading_number(title)
        
        new_para = etree.Element(f'{W}p')
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        
        if bold_title:
            # Bold number + title, normal body
            title_run = etree.SubElement(ins, f'{W}r')
            title_rPr = etree.SubElement(title_run, f'{W}rPr')
            etree.SubElement(title_rPr, f'{W}b')
            title_t = etree.SubElement(title_run, f'{W}t')
            title_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            title_t.text = f"{number}. {title}. "
            
            body_run = etree.SubElement(ins, f'{W}r')
            body_t = etree.SubElement(body_run, f'{W}t')
            body_t.text = body
        else:
            # All plain text - no bold
            run = etree.SubElement(ins, f'{W}r')
            t = etree.SubElement(run, f'{W}t')
            t.text = f"{number}. {title}. {body}"
        
        target.addnext(new_para)
        self._last_insert_at[after_index] = new_para
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
    
    def amend_text(self, old_text: str, new_text: str) -> bool:
        """
        Amend text in document with normalized matching.
        """
        result = self._find_para_containing(old_text)
        if not result:
            return False
        
        idx, para = result
        full_text = self._get_para_text(para)
        
        # Try exact match first
        match = re.search(re.escape(old_text), full_text, re.IGNORECASE)
        if match:
            start_pos = match.start()
            end_pos = match.end()
        else:
            # Try normalized matching
            full_norm = self._normalize_text(full_text)
            old_norm = self._normalize_text(old_text)
            
            norm_start = full_norm.find(old_norm)
            if norm_start == -1:
                return False
            
            # Map normalized position back to original
            norm_idx = 0
            start_pos = None
            
            for i, char in enumerate(full_text):
                if start_pos is None and norm_idx >= norm_start:
                    start_pos = i
                
                norm_char = char.lower()
                if norm_char.isspace():
                    norm_idx += 1
                elif norm_char:
                    norm_idx += 1
            
            if start_pos is None:
                return False
            
            end_pos = min(start_pos + len(old_text), len(full_text))
        
        actual_old = full_text[start_pos:end_pos]
        
        # Build character positions
        positions = []
        for run in para.findall(f'{W}r'):
            for t in run.findall(f'{W}t'):
                if t.text:
                    for i, char in enumerate(t.text):
                        positions.append((run, t, i))
        
        if start_pos >= len(positions) or end_pos > len(positions):
            return False
        
        start_run, start_t, start_i = positions[start_pos]
        end_run, end_t, end_i = positions[end_pos - 1]
        
        if start_t == end_t:
            original = start_t.text
            before = original[:start_i]
            after = original[end_i + 1:]
            
            start_t.text = before
            
            rPr = start_run.find(f'{W}rPr')
            
            del_elem = etree.Element(f'{W}del', self._create_tc_attrs())
            del_run = etree.SubElement(del_elem, f'{W}r')
            if rPr is not None:
                del_run.insert(0, deepcopy(rPr))
            del_text = etree.SubElement(del_run, f'{W}delText')
            del_text.text = actual_old
            
            ins_elem = etree.Element(f'{W}ins', self._create_tc_attrs())
            ins_run = etree.SubElement(ins_elem, f'{W}r')
            if rPr is not None:
                ins_run.insert(0, deepcopy(rPr))
            ins_text = etree.SubElement(ins_run, f'{W}t')
            ins_text.text = new_text
            
            after_run = None
            if after:
                after_run = etree.Element(f'{W}r')
                if rPr is not None:
                    after_run.insert(0, deepcopy(rPr))
                after_t = etree.SubElement(after_run, f'{W}t')
                after_t.text = after
            
            parent = start_run.getparent()
            run_idx = list(parent).index(start_run)
            
            if after_run is not None:
                parent.insert(run_idx + 1, after_run)
            parent.insert(run_idx + 1, ins_elem)
            parent.insert(run_idx + 1, del_elem)
            
            return True
        
        return False
    
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
            return False
        
        para = paragraphs[para_index]
        
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
