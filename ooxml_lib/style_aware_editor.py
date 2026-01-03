"""
Style-aware editor for legal documents.
Detects and matches formatting styles for inserts.
"""

from io import BytesIO
from copy import deepcopy
from lxml import etree
import zipfile
import re
from datetime import datetime

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class StyleAwareEditor:
    
    def __init__(self, docx_bytes: bytes, author: str = "VibeLegal"):
        self.original_bytes = docx_bytes
        self.author = author
        
        with zipfile.ZipFile(BytesIO(docx_bytes), 'r') as zf:
            self.document_xml = zf.read('word/document.xml')
        
        self.root = etree.fromstring(self.document_xml)
        self.body = self.root.find(f'{W}body')
        self.next_id = self._find_max_id() + 1
        
        # Track last inserted paragraph for each anchor index
        # Ensures proper ordering when multiple inserts target same anchor
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
        Handles:
        - Curly quotes → straight quotes
        - Curly apostrophes → straight apostrophe  
        - Em/en dashes → regular hyphen
        - Multiple spaces → single space
        - Leading/trailing whitespace
        """
        if not text:
            return ""
        
        # Normalize quotes
        text = text.replace('"', '"').replace('"', '"')  # Curly double quotes
        text = text.replace("'", "'").replace("'", "'")  # Curly single quotes/apostrophes
        text = text.replace("'", "'")                     # Another apostrophe variant
        
        # Normalize dashes
        text = text.replace("–", "-").replace("—", "-")  # En-dash, em-dash
        
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
    
    def detect_inline_title_style(self, para_index: int) -> str:
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
        paragraphs = self._get_paragraphs()
        if after_index < 0 or after_index >= len(paragraphs):
            return False
        
        # Use last inserted paragraph if available, otherwise use the anchor
        if after_index in self._last_insert_at:
            target = self._last_insert_at[after_index]
        else:
            target = paragraphs[after_index]
        
        new_para = etree.Element(f'{W}p')
        
        # Copy properties from ORIGINAL paragraph (not the tracked one)
        original_para = paragraphs[after_index]
        src_pPr = original_para.find(f'{W}pPr')
        if src_pPr is not None:
            new_pPr = etree.Element(f'{W}pPr')
            # NOTE: Don't copy 'numPr' - it causes duplicate numbering
            for prop in ['ind', 'spacing']:
                elem = src_pPr.find(f'{W}{prop}')
                if elem is not None:
                    new_pPr.append(deepcopy(elem))
            if len(new_pPr) > 0:
                new_para.append(new_pPr)
        
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        run = etree.SubElement(ins, f'{W}r')
        t = etree.SubElement(run, f'{W}t')
        t.text = text
        
        target.addnext(new_para)
        
        # Track this insert for subsequent inserts at same anchor
        self._last_insert_at[after_index] = new_para
        return True
    
    def insert_with_bold_title(self, after_index: int, title: str, body: str) -> bool:
        paragraphs = self._get_paragraphs()
        if after_index < 0 or after_index >= len(paragraphs):
            return False
        
        # Use last inserted paragraph if available, otherwise use the anchor
        if after_index in self._last_insert_at:
            target = self._last_insert_at[after_index]
        else:
            target = paragraphs[after_index]
        
        new_para = etree.Element(f'{W}p')
        
        # Copy properties from ORIGINAL paragraph
        original_para = paragraphs[after_index]
        src_pPr = original_para.find(f'{W}pPr')
        if src_pPr is not None:
            new_pPr = etree.Element(f'{W}pPr')
            # NOTE: Don't copy 'numPr' - it causes duplicate numbering
            for prop in ['ind', 'spacing']:
                elem = src_pPr.find(f'{W}{prop}')
                if elem is not None:
                    new_pPr.append(deepcopy(elem))
            if len(new_pPr) > 0:
                new_para.append(new_pPr)
        
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        
        title_run = etree.SubElement(ins, f'{W}r')
        title_rPr = etree.SubElement(title_run, f'{W}rPr')
        etree.SubElement(title_rPr, f'{W}b')
        title_t = etree.SubElement(title_run, f'{W}t')
        title_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        title_t.text = title + ". "
        
        body_run = etree.SubElement(ins, f'{W}r')
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = body
        
        target.addnext(new_para)
        
        # Track this insert for subsequent inserts at same anchor
        self._last_insert_at[after_index] = new_para
        return True
    
    def insert_with_underline_title(self, after_index: int, title: str, body: str) -> bool:
        paragraphs = self._get_paragraphs()
        if after_index < 0 or after_index >= len(paragraphs):
            return False
        
        # Use last inserted paragraph if available
        if after_index in self._last_insert_at:
            target = self._last_insert_at[after_index]
        else:
            target = paragraphs[after_index]
        
        new_para = etree.Element(f'{W}p')
        
        original_para = paragraphs[after_index]
        src_pPr = original_para.find(f'{W}pPr')
        if src_pPr is not None:
            new_pPr = etree.Element(f'{W}pPr')
            for prop in ['ind', 'spacing']:
                elem = src_pPr.find(f'{W}{prop}')
                if elem is not None:
                    new_pPr.append(deepcopy(elem))
            if len(new_pPr) > 0:
                new_para.append(new_pPr)
        
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
    
    def insert_sub_clause(self, after_index: int, parent_num: str, sub_num: int, 
                          title: str, body: str) -> bool:
        paragraphs = self._get_paragraphs()
        if after_index < 0 or after_index >= len(paragraphs):
            return False
        
        # Use last inserted paragraph if available
        if after_index in self._last_insert_at:
            target = self._last_insert_at[after_index]
        else:
            target = paragraphs[after_index]
        
        new_para = etree.Element(f'{W}p')
        
        original_para = paragraphs[after_index]
        src_pPr = original_para.find(f'{W}pPr')
        if src_pPr is not None:
            new_pPr = etree.Element(f'{W}pPr')
            for prop in ['ind', 'spacing']:
                elem = src_pPr.find(f'{W}{prop}')
                if elem is not None:
                    new_pPr.append(deepcopy(elem))
            if len(new_pPr) > 0:
                new_para.append(new_pPr)
        
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        
        num_run = etree.SubElement(ins, f'{W}r')
        num_rPr = etree.SubElement(num_run, f'{W}rPr')
        u_elem = etree.SubElement(num_rPr, f'{W}u')
        u_elem.set(f'{W}val', 'single')
        num_t = etree.SubElement(num_run, f'{W}t')
        num_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        num_t.text = f"{parent_num}.{sub_num} {title}. "
        
        body_run = etree.SubElement(ins, f'{W}r')
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = body
        
        target.addnext(new_para)
        self._last_insert_at[after_index] = new_para
        return True
    
    def smart_insert(self, after_index: int, title: str, body: str, 
                     context_info: dict = None) -> bool:
        context_info = context_info or {}
        
        title_style = self.detect_inline_title_style(after_index)
        num_style, num_val = self.detect_numbering_style(after_index)
        
        if num_style == 'word_list':
            if title_style == 'bold':
                return self.insert_with_bold_title(after_index, title, body)
            else:
                return self.insert_plain_paragraph(after_index, f"{title}. {body}")
        
        elif num_style == 'sub':
            parent = num_val.split('.')[0]
            existing_sub = int(num_val.split('.')[1])
            next_sub = existing_sub + 1
            return self.insert_sub_clause(after_index, parent, next_sub, title, body)
        
        elif num_style == 'manual':
            if context_info.get('use_sub_clauses'):
                parent_num = num_val.rstrip('.')
                sub_num = context_info.get('sub_number', 1)
                return self.insert_sub_clause(after_index, parent_num, sub_num, title, body)
            elif title_style == 'underline':
                return self.insert_with_underline_title(after_index, title, body)
            elif title_style == 'bold':
                return self.insert_with_bold_title(after_index, title, body)
            else:
                return self.insert_plain_paragraph(after_index, f"{title}. {body}")
        
        else:
            if title_style == 'bold':
                return self.insert_with_bold_title(after_index, title, body)
            elif title_style == 'underline':
                return self.insert_with_underline_title(after_index, title, body)
            elif title_style == 'none':
                return self.insert_plain_paragraph(after_index, f"{title}. {body}")
            else:
                return self.insert_plain_paragraph(after_index, f"{title}. {body}")
    
    def amend_text(self, old_text: str, new_text: str) -> bool:
        """
        Amend text in document with normalized matching.
        Handles quote/apostrophe variants automatically.
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
            
            # Map normalized position to original position
            # Walk through original text counting characters
            norm_idx = 0
            start_pos = None
            
            for i, char in enumerate(full_text):
                if start_pos is None and norm_idx >= norm_start:
                    start_pos = i
                
                # Check if we've found enough normalized characters
                norm_char = char.lower()
                if norm_char.isspace():
                    norm_idx += 1
                elif norm_char:
                    norm_idx += 1
            
            if start_pos is None:
                return False
            
            # Estimate end position based on original text length
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
        """Insert paragraph BEFORE the given index (0-based)."""
        paragraphs = self._get_paragraphs()
        if para_index < 0 or para_index >= len(paragraphs):
            return False
        
        target = paragraphs[para_index]
        new_para = etree.Element(f'{W}p')
        
        # Copy paragraph properties from target
        src_pPr = target.find(f'{W}pPr')
        if src_pPr is not None:
            new_pPr = etree.Element(f'{W}pPr')
            for prop in ['ind', 'spacing']:
                elem = src_pPr.find(f'{W}{prop}')
                if elem is not None:
                    new_pPr.append(deepcopy(elem))
            if len(new_pPr) > 0:
                new_para.append(new_pPr)
        
        # Add content as track-changed insert
        ins = etree.SubElement(new_para, f'{W}ins', self._create_tc_attrs())
        run = etree.SubElement(ins, f'{W}r')
        t = etree.SubElement(run, f'{W}t')
        t.text = text
        
        # Insert before target
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
