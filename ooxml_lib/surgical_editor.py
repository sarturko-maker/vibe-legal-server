"""
OOXML Surgical Editor v0.4

Makes surgical edits directly to Word document XML.
Preserves all formatting that isn't explicitly modified.

Key features:
- Track changes (insertions, deletions, amendments)
- Flexible text matching (handles spacing differences)
- Multiple insert ordering (maintains correct sequence)
"""

import zipfile
import re
from pathlib import Path
from typing import Union, Optional, List, Tuple
from lxml import etree
from io import BytesIO
from datetime import datetime
from copy import deepcopy

# OOXML namespace
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _normalize_text(text: str) -> str:
    """
    Normalize text for flexible matching.
    - Collapse multiple spaces/whitespace to single space
    - Standardize smart quotes to regular quotes
    - Strip leading/trailing whitespace
    """
    # Collapse multiple spaces/whitespace to single space
    text = re.sub(r'\s+', ' ', text)
    # Standardize quotes
    text = text.replace('"', '"').replace('"', '"')
    text = text.replace("'", "'").replace("'", "'")
    return text.strip()


class DocumentEditor:
    """
    Edit Word documents surgically by modifying the original XML.
    
    Usage:
        editor = DocumentEditor('/path/to/document.docx')
        editor.amend_text("old text", "new text")
        editor.insert_paragraph_after("after this text", "new paragraph text")
        editor.save('/path/to/output.docx')
    """
    
    def __init__(self, source: Union[str, Path, bytes, BytesIO]):
        """Load a document for editing"""
        # Read the original docx
        if isinstance(source, (str, Path)):
            with open(source, 'rb') as f:
                self.original_bytes = f.read()
        elif isinstance(source, bytes):
            self.original_bytes = source
        else:
            source.seek(0)
            self.original_bytes = source.read()
        
        # Extract document.xml
        with zipfile.ZipFile(BytesIO(self.original_bytes), 'r') as zf:
            self.document_xml = zf.read('word/document.xml')
        
        # Parse it
        self.root = etree.fromstring(self.document_xml)
        self.body = self.root.find(f'{W}body')
        
        if self.body is None:
            raise ValueError("No document body found")
        
        # Find highest existing track change ID
        self.next_id = self._find_max_id() + 1
        
        # Author for track changes
        self.author = "VibeLegal"
        
        # Track last insertion point for each anchor (maintains order for multiple inserts)
        self._last_insert_after: dict = {}
    
    def _find_max_id(self) -> int:
        """Find the highest w:id used in the document"""
        max_id = -1
        for elem in self.root.iter():
            id_attr = elem.get(f'{W}id')
            if id_attr is not None:
                try:
                    max_id = max(max_id, int(id_attr))
                except ValueError:
                    pass
        return max_id
    
    def _get_next_id(self) -> int:
        """Get and increment the next track change ID"""
        id = self.next_id
        self.next_id += 1
        return id
    
    def _get_paragraph_text(self, p: etree._Element) -> str:
        """Extract plain text from a paragraph"""
        texts = []
        for t in p.iter(f'{W}t'):
            if t.text:
                texts.append(t.text)
        return ''.join(texts)
    
    def _get_paragraphs(self) -> List[etree._Element]:
        """Get all paragraphs in the document"""
        return self.body.findall(f'{W}p')
    
    def _find_paragraph_containing(self, text: str) -> Optional[Tuple[int, etree._Element]]:
        """Find the first paragraph containing the given text (with flexible matching)"""
        normalized_search = _normalize_text(text)
        
        for i, p in enumerate(self._get_paragraphs()):
            para_text = self._get_paragraph_text(p)
            normalized_para = _normalize_text(para_text)
            
            # Try normalized match first (handles spacing differences)
            if normalized_search in normalized_para:
                return (i, p)
            
            # Fall back to exact match
            if text in para_text:
                return (i, p)
        
        return None
    
    def _create_track_change_attrs(self) -> dict:
        """Create attributes for a track change element"""
        return {
            f'{W}id': str(self._get_next_id()),
            f'{W}author': self.author,
            f'{W}date': datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    
    def amend_text(self, original: str, replacement: str) -> bool:
        """
        Find and replace text with track changes.
        
        The original text is marked as deleted, the replacement as inserted.
        Works within a single paragraph.
        
        Returns True if the text was found and replaced.
        """
        result = self._find_paragraph_containing(original)
        if result is None:
            return False
        
        idx, paragraph = result
        
        # Find the run(s) containing the text and do the replacement
        return self._amend_in_paragraph(paragraph, original, replacement)
    
    def _amend_in_paragraph(self, paragraph: etree._Element, original: str, replacement: str) -> bool:
        """Perform amendment within a single paragraph"""
        
        # Build a map of character positions to runs
        runs = []
        for elem in paragraph:
            if elem.tag == f'{W}r':
                runs.append(('normal', elem, None))
            elif elem.tag == f'{W}ins':
                for r in elem.findall(f'{W}r'):
                    runs.append(('ins', r, elem))
            elif elem.tag == f'{W}del':
                for r in elem.findall(f'{W}r'):
                    runs.append(('del', r, elem))
        
        # Get full paragraph text
        full_text = self._get_paragraph_text(paragraph)
        
        # Try exact match first
        if original in full_text:
            start_pos = full_text.find(original)
            end_pos = start_pos + len(original)
            actual_deleted_text = original
        else:
            # Try normalized match
            normalized_full = _normalize_text(full_text)
            normalized_original = _normalize_text(original)
            
            if normalized_original not in normalized_full:
                return False
            
            # Find the actual text in the document that matches
            start_pos = None
            for i in range(len(full_text)):
                remaining = full_text[i:]
                for end in range(1, len(remaining) + 1):
                    chunk = remaining[:end]
                    if _normalize_text(chunk) == normalized_original:
                        start_pos = i
                        end_pos = i + end
                        actual_deleted_text = chunk
                        break
                if start_pos is not None:
                    break
            
            if start_pos is None:
                return False
        
        # Find which runs contain the target text
        char_pos = 0
        affected_runs = []
        
        for run_type, run, parent in runs:
            run_text = ''.join(t.text or '' for t in run.findall(f'{W}t'))
            run_start = char_pos
            run_end = char_pos + len(run_text)
            
            # Check for overlap
            if run_end > start_pos and run_start < end_pos:
                affected_runs.append({
                    'type': run_type,
                    'run': run,
                    'parent': parent,
                    'text': run_text,
                    'start': run_start,
                    'end': run_end
                })
            
            char_pos = run_end
        
        if not affected_runs:
            return False
        
        # Get the first affected run's properties as template
        first_run = affected_runs[0]['run']
        rPr_template = first_run.find(f'{W}rPr')
        
        # Create deletion wrapper
        del_elem = etree.Element(f'{W}del', self._create_track_change_attrs())
        del_run = etree.SubElement(del_elem, f'{W}r')
        if rPr_template is not None:
            del_run.append(deepcopy(rPr_template))
        del_text = etree.SubElement(del_run, f'{W}delText')
        del_text.text = actual_deleted_text
        
        # Create insertion wrapper
        ins_elem = etree.Element(f'{W}ins', self._create_track_change_attrs())
        ins_run = etree.SubElement(ins_elem, f'{W}r')
        if rPr_template is not None:
            ins_run.append(deepcopy(rPr_template))
        ins_text = etree.SubElement(ins_run, f'{W}t')
        ins_text.text = replacement
        
        # Now surgically modify the paragraph
        first_affected = affected_runs[0]
        
        if first_affected['type'] == 'normal':
            ref_elem = first_affected['run']
        else:
            ref_elem = first_affected['parent']
        
        # Text before the target (in the first run)
        before_text = first_affected['text'][:start_pos - first_affected['start']]
        
        # Text after the target (in the last run)
        last_affected = affected_runs[-1]
        after_text = last_affected['text'][end_pos - last_affected['start']:]
        
        # Insert before run if there's text before
        if before_text:
            before_run = etree.Element(f'{W}r')
            if rPr_template is not None:
                before_run.append(deepcopy(rPr_template))
            before_t = etree.SubElement(before_run, f'{W}t')
            before_t.text = before_text
            if before_text.startswith(' ') or before_text.endswith(' '):
                before_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            ref_elem.addprevious(before_run)
        
        # Insert deletion
        ref_elem.addprevious(del_elem)
        
        # Insert insertion
        ref_elem.addprevious(ins_elem)
        
        # Insert after run if there's text after
        if after_text:
            after_run = etree.Element(f'{W}r')
            if rPr_template is not None:
                after_run.append(deepcopy(rPr_template))
            after_t = etree.SubElement(after_run, f'{W}t')
            after_t.text = after_text
            if after_text.startswith(' ') or after_text.endswith(' '):
                after_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            ref_elem.addprevious(after_run)
        
        # Remove original affected runs
        for affected in affected_runs:
            if affected['type'] == 'normal':
                paragraph.remove(affected['run'])
            else:
                parent = affected['parent']
                parent.remove(affected['run'])
                if len(parent.findall(f'{W}r')) == 0:
                    paragraph.remove(parent)
        
        return True
    
    def insert_paragraph_after(self, after_text: str, new_text: str, 
                                copy_formatting: bool = True) -> bool:
        """
        Insert a new paragraph after the paragraph containing the given text.
        
        The new paragraph is marked as an insertion.
        Multiple inserts at the same anchor maintain correct order.
        """
        result = self._find_paragraph_containing(after_text)
        if result is None:
            return False
        
        idx, target_para = result
        
        # Check if we've already inserted something after this anchor
        normalized_anchor = _normalize_text(after_text)
        if normalized_anchor in self._last_insert_after:
            insert_after_para = self._last_insert_after[normalized_anchor]
        else:
            insert_after_para = target_para
        
        # Create new paragraph by copying the target (preserves all formatting)
        if copy_formatting:
            new_para = deepcopy(target_para)
            for child in list(new_para):
                if child.tag != f'{W}pPr':
                    new_para.remove(child)
        else:
            new_para = etree.Element(f'{W}p')
        
        # Create insertion wrapper with the new text
        ins_elem = etree.SubElement(new_para, f'{W}ins', self._create_track_change_attrs())
        ins_run = etree.SubElement(ins_elem, f'{W}r')
        ins_text = etree.SubElement(ins_run, f'{W}t')
        ins_text.text = new_text
        
        # Insert after the appropriate paragraph
        insert_after_para.addnext(new_para)
        
        # Track this insertion for future inserts at the same anchor
        self._last_insert_after[normalized_anchor] = new_para
        
        return True
    
    def delete_paragraph(self, containing_text: str) -> bool:
        """Mark an entire paragraph as deleted."""
        result = self._find_paragraph_containing(containing_text)
        if result is None:
            return False
        
        idx, paragraph = result
        
        # Wrap all existing runs in w:del elements
        del_elem = etree.Element(f'{W}del', self._create_track_change_attrs())
        
        # Move all runs into the deletion
        for child in list(paragraph):
            if child.tag == f'{W}r':
                for t in child.findall(f'{W}t'):
                    t.tag = f'{W}delText'
                del_elem.append(child)
        
        # Add the deletion element to the paragraph
        pPr = paragraph.find(f'{W}pPr')
        if pPr is not None:
            pPr.addnext(del_elem)
        else:
            paragraph.insert(0, del_elem)
        
        return True
    
    def save(self, output: Union[str, Path, BytesIO]) -> None:
        """Save the modified document"""
        
        new_xml = etree.tostring(
            self.root, 
            xml_declaration=True, 
            encoding='UTF-8', 
            standalone='yes'
        )
        
        if isinstance(output, (str, Path)):
            out_file = open(output, 'wb')
            close_file = True
        else:
            out_file = output
            close_file = False
        
        try:
            with zipfile.ZipFile(BytesIO(self.original_bytes), 'r') as zf_in:
                with zipfile.ZipFile(out_file, 'w', zipfile.ZIP_DEFLATED) as zf_out:
                    for item in zf_in.namelist():
                        if item == 'word/document.xml':
                            zf_out.writestr(item, new_xml)
                        else:
                            zf_out.writestr(item, zf_in.read(item))
        finally:
            if close_file:
                out_file.close()
    
    def get_paragraphs_text(self) -> List[str]:
        """Get text of all paragraphs (for debugging)"""
        return [self._get_paragraph_text(p) for p in self._get_paragraphs()]
    
    def insert_paragraph_after_with_bold_title(self, after_text: str, title: str, body: str) -> bool:
        """
        Insert a new paragraph where the title portion is bold.
        Creates: "Title. Body text..." with "Title." in bold.
        """
        result = self._find_paragraph_containing(after_text)
        if result is None:
            return False
        
        idx, target_para = result
        
        # Check if we've already inserted something after this anchor
        normalized_anchor = _normalize_text(after_text)
        if normalized_anchor in self._last_insert_after:
            insert_after_para = self._last_insert_after[normalized_anchor]
        else:
            insert_after_para = target_para
        
        # Deep copy the target paragraph (preserves pPr with numbering, style, etc.)
        new_para = deepcopy(target_para)
        
        # Remove all content but keep pPr
        for child in list(new_para):
            if child.tag != f'{W}pPr':
                new_para.remove(child)
        
        # Get run properties from original for styling
        bold_rPr = None
        normal_rPr = None
        
        for run in target_para.iter(f'{W}r'):
            rPr = run.find(f'{W}rPr')
            if rPr is not None:
                has_bold = rPr.find(f'{W}b') is not None
                if has_bold and bold_rPr is None:
                    bold_rPr = deepcopy(rPr)
                elif not has_bold and normal_rPr is None:
                    normal_rPr = deepcopy(rPr)
        
        # If no bold template found, create one
        if bold_rPr is None:
            bold_rPr = etree.Element(f'{W}rPr')
            etree.SubElement(bold_rPr, f'{W}b')
            etree.SubElement(bold_rPr, f'{W}bCs')
        
        # Create insertion wrapper
        ins_elem = etree.SubElement(new_para, f'{W}ins', self._create_track_change_attrs())
        
        # Bold title run
        title_run = etree.SubElement(ins_elem, f'{W}r')
        title_run.append(deepcopy(bold_rPr))
        title_t = etree.SubElement(title_run, f'{W}t')
        title_t.text = title + " "
        title_t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        
        # Normal body run
        body_run = etree.SubElement(ins_elem, f'{W}r')
        if normal_rPr is not None:
            body_run.append(deepcopy(normal_rPr))
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = body
        
        # Insert after the appropriate paragraph
        insert_after_para.addnext(new_para)
        
        # Track this insertion
        self._last_insert_after[normalized_anchor] = new_para
        
        return True
    
    def insert_clause_with_heading(self, after_heading_text: str, 
                                    new_heading: str, new_body: str) -> bool:
        """
        Insert a new clause with a separate heading paragraph and body paragraph.
        Used for documents with heading+body structure (not inline).
        """
        paragraphs = self._get_paragraphs()
        
        # Find the heading paragraph
        heading_idx = None
        for i, p in enumerate(paragraphs):
            text = self._get_paragraph_text(p)
            normalized_text = _normalize_text(text)
            normalized_search = _normalize_text(after_heading_text)
            
            if normalized_search in normalized_text or after_heading_text in text:
                heading_idx = i
                break
        
        if heading_idx is None:
            return False
        
        heading_para = paragraphs[heading_idx]
        
        # The body paragraph should be right after the heading
        if heading_idx + 1 >= len(paragraphs):
            return False
        
        body_para = paragraphs[heading_idx + 1]
        
        # Check if we've already inserted something after this anchor
        normalized_anchor = _normalize_text(after_heading_text)
        if normalized_anchor in self._last_insert_after:
            insert_after_para = self._last_insert_after[normalized_anchor]
        else:
            insert_after_para = body_para
        
        # Create new heading paragraph
        new_heading_para = deepcopy(heading_para)
        for child in list(new_heading_para):
            if child.tag != f'{W}pPr':
                new_heading_para.remove(child)
        
        # Add heading text as insertion
        heading_ins = etree.SubElement(new_heading_para, f'{W}ins', self._create_track_change_attrs())
        heading_run = etree.SubElement(heading_ins, f'{W}r')
        
        for run in heading_para.iter(f'{W}r'):
            rPr = run.find(f'{W}rPr')
            if rPr is not None:
                heading_run.append(deepcopy(rPr))
                break
        
        heading_t = etree.SubElement(heading_run, f'{W}t')
        heading_t.text = new_heading
        
        # Create new body paragraph
        new_body_para = deepcopy(body_para)
        for child in list(new_body_para):
            if child.tag != f'{W}pPr':
                new_body_para.remove(child)
        
        # Add body text as insertion
        body_ins = etree.SubElement(new_body_para, f'{W}ins', self._create_track_change_attrs())
        body_run = etree.SubElement(body_ins, f'{W}r')
        
        for run in body_para.iter(f'{W}r'):
            rPr = run.find(f'{W}rPr')
            if rPr is not None:
                body_run.append(deepcopy(rPr))
                break
        
        body_t = etree.SubElement(body_run, f'{W}t')
        body_t.text = new_body
        
        # Insert (body first, then heading, so heading ends up before body)
        insert_after_para.addnext(new_body_para)
        insert_after_para.addnext(new_heading_para)
        
        # Track this insertion
        self._last_insert_after[normalized_anchor] = new_body_para
        
        return True
    
    def insert_paragraph_after_index(self, index: int, text: str, 
                                      copy_formatting: bool = True,
                                      role: str = None) -> bool:
        """
        Insert a new paragraph after the paragraph at the given index.
        
        This is the structure-aware insert method - uses paragraph index
        instead of text search, avoiding "anchor not found" errors.
        
        Args:
            index: Paragraph index (0-based)
            text: Text content to insert
            copy_formatting: If True, copy pPr from target paragraph
            role: Role hint for formatting (SECTION_HEAD, CLAUSE, etc.)
        
        Returns:
            True if successful, False if index out of range
        """
        paragraphs = self._get_paragraphs()
        
        if index < 0 or index >= len(paragraphs):
            return False
        
        target_para = paragraphs[index]
        
        # Create new paragraph
        if copy_formatting:
            new_para = deepcopy(target_para)
            # Remove all content but keep pPr (formatting)
            for child in list(new_para):
                if child.tag != f'{W}pPr':
                    new_para.remove(child)
        else:
            new_para = etree.Element(f'{W}p')
        
        # Get run properties from target for styling
        rPr_template = None
        for run in target_para.iter(f'{W}r'):
            rPr = run.find(f'{W}rPr')
            if rPr is not None:
                rPr_template = deepcopy(rPr)
                break
        
        # Create insertion wrapper with track changes
        ins_elem = etree.SubElement(new_para, f'{W}ins', self._create_track_change_attrs())
        ins_run = etree.SubElement(ins_elem, f'{W}r')
        
        if rPr_template is not None:
            ins_run.append(rPr_template)
        
        ins_text = etree.SubElement(ins_run, f'{W}t')
        ins_text.text = text
        
        # Preserve whitespace if needed
        if text.startswith(' ') or text.endswith(' '):
            ins_text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        
        # Insert after target paragraph
        target_para.addnext(new_para)
        
        return True
    
    def insert_paragraph_before_index(self, index: int, text: str,
                                       copy_formatting: bool = True) -> bool:
        """
        Insert a new paragraph before the paragraph at the given index.
        
        Args:
            index: Paragraph index (0-based)
            text: Text content to insert
            copy_formatting: If True, copy pPr from target paragraph
        
        Returns:
            True if successful, False if index out of range
        """
        paragraphs = self._get_paragraphs()
        
        if index < 0 or index >= len(paragraphs):
            return False
        
        target_para = paragraphs[index]
        
        # Create new paragraph
        if copy_formatting:
            new_para = deepcopy(target_para)
            for child in list(new_para):
                if child.tag != f'{W}pPr':
                    new_para.remove(child)
        else:
            new_para = etree.Element(f'{W}p')
        
        # Get run properties
        rPr_template = None
        for run in target_para.iter(f'{W}r'):
            rPr = run.find(f'{W}rPr')
            if rPr is not None:
                rPr_template = deepcopy(rPr)
                break
        
        # Create insertion wrapper
        ins_elem = etree.SubElement(new_para, f'{W}ins', self._create_track_change_attrs())
        ins_run = etree.SubElement(ins_elem, f'{W}r')
        
        if rPr_template is not None:
            ins_run.append(rPr_template)
        
        ins_text = etree.SubElement(ins_run, f'{W}t')
        ins_text.text = text
        
        if text.startswith(' ') or text.endswith(' '):
            ins_text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        
        # Insert before target
        target_para.addprevious(new_para)
        
        return True
    
    def delete_paragraph_by_index(self, index: int) -> bool:
        """
        Mark a paragraph as deleted by its index.
        
        Args:
            index: Paragraph index (0-based)
        
        Returns:
            True if successful, False if index out of range
        """
        paragraphs = self._get_paragraphs()
        
        if index < 0 or index >= len(paragraphs):
            return False
        
        paragraph = paragraphs[index]
        
        # Wrap all existing runs in w:del elements
        del_elem = etree.Element(f'{W}del', self._create_track_change_attrs())
        
        # Move all runs into the deletion
        for child in list(paragraph):
            if child.tag == f'{W}r':
                for t in child.findall(f'{W}t'):
                    t.tag = f'{W}delText'
                del_elem.append(child)
        
        # Add the deletion element to the paragraph
        pPr = paragraph.find(f'{W}pPr')
        if pPr is not None:
            pPr.addnext(del_elem)
        else:
            paragraph.insert(0, del_elem)
        
        return True
