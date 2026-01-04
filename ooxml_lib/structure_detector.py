"""
Structure detection for legal documents.
Identifies sections, clauses, sub-clauses, lists, and hierarchy.

v1.1 - Added missing methods for structure_operations.py compatibility:
  - StructureMap.max_depth property
  - StructureMap.find_node_by_id() alias
  - StructureMap.find_section_by_title()
  - StructureMap.find_clause_by_number()
  - StructureMap.find_nodes_by_role()
  - StructureNode.number_value property
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from lxml import etree
import zipfile
from io import BytesIO
import re

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


class NodeRole(Enum):
    SECTION_HEAD = "SECTION_HEAD"
    ARTICLE = "ARTICLE"
    CLAUSE = "CLAUSE"
    SUB_CLAUSE = "SUB_CLAUSE"
    LIST_ITEM = "LIST_ITEM"
    DEFINITION = "DEFINITION"
    BODY = "BODY"
    UNKNOWN = "UNKNOWN"


@dataclass
class StructureNode:
    id: str
    paragraph_index: int
    text: str
    role: NodeRole
    level: int = 0
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    numbering_info: Optional[dict] = None
    format_label: str = "PLAIN"  # BOLD, BULLET, NUMBERED, NUMBERED_MANUAL, INDENTED, HEADING1, HEADING2, PLAIN
    is_bold: bool = False
    is_indented: bool = False
    
    @property
    def text_preview(self) -> str:
        """First 60 chars of text."""
        return self.text[:60] + "..." if len(self.text) > 60 else self.text
    
    @property
    def number_value(self) -> Optional[str]:
        """
        Extract number from numbering_info if present.
        Returns the clause/article number (e.g., '1', '1.1', 'I').
        """
        if self.numbering_info:
            return self.numbering_info.get('number')
        return None


@dataclass
class StructureMap:
    nodes: List[StructureNode]
    structure_type: str
    confidence: float
    has_sections: bool
    has_manual_numbering: bool
    has_word_numbering: bool
    has_heading_styles: bool = False
    heading_style_name: Optional[str] = None
    has_bullet_sections: bool = False  # Bold section headings followed by bullet lists
    
    # =========================================================================
    # BASIC NODE ACCESS
    # =========================================================================
    
    def get_node_by_id(self, node_id: str) -> Optional[StructureNode]:
        """Get node by its ID (e.g., 'p0', 'p1')."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None
    
    def get_node_by_index(self, para_index: int) -> Optional[StructureNode]:
        """Get node by paragraph index."""
        for node in self.nodes:
            if node.paragraph_index == para_index:
                return node
        return None
    
    # =========================================================================
    # HIERARCHY NAVIGATION
    # =========================================================================
    
    def get_children(self, node: StructureNode) -> List[StructureNode]:
        """Get all direct children of a node."""
        return [self.get_node_by_id(cid) for cid in node.children_ids if self.get_node_by_id(cid)]
    
    def get_parent(self, node: StructureNode) -> Optional[StructureNode]:
        """Get parent node."""
        if node.parent_id:
            return self.get_node_by_id(node.parent_id)
        return None
    
    def get_siblings(self, node: StructureNode) -> List[StructureNode]:
        """Get sibling nodes (same parent)."""
        if not node.parent_id:
            return [n for n in self.nodes if n.parent_id is None and n.id != node.id]
        parent = self.get_parent(node)
        if parent:
            return [self.get_node_by_id(cid) for cid in parent.children_ids if cid != node.id]
        return []
    
    def get_last_descendant(self, node: StructureNode) -> StructureNode:
        """
        Get the last descendant (deepest last child) of a node.
        Used to find the END of a section for inserting after it.
        """
        if not node.children_ids:
            return node
        last_child_id = node.children_ids[-1]
        last_child = self.get_node_by_id(last_child_id)
        if last_child:
            return self.get_last_descendant(last_child)
        return node
    
    # =========================================================================
    # SEARCH METHODS (for structure_operations.py compatibility)
    # =========================================================================
    
    @property
    def max_depth(self) -> int:
        """Maximum nesting depth in document."""
        if not self.nodes:
            return 0
        return max(n.level for n in self.nodes)
    
    def get_last_paragraph_of_clause(self, clause_node: 'StructureNode') -> int:
        """
        Find the last paragraph belonging to a clause.
        
        For headed documents where clause heading and body are separate paragraphs:
        - Given: clause "4. Governing Law" at p16 (heading)
        - Returns: p17 (last body paragraph before clause 5)
        
        Walks forward from the heading until hitting the next numbered clause.
        """
        if not self.nodes:
            return clause_node.paragraph_index
        
        last_body_idx = clause_node.paragraph_index
        current_idx = clause_node.paragraph_index + 1
        max_idx = max(n.paragraph_index for n in self.nodes)
        
        while current_idx <= max_idx:
            current_node = self.get_node_by_index(current_idx)
            if current_node:
                # Stop if we hit another numbered clause heading
                text_preview = current_node.text[:10] if current_node.text else ''
                if re.match(r'^\d+\.', text_preview):
                    break
                # This paragraph belongs to our clause
                last_body_idx = current_idx
            current_idx += 1
        
        return last_body_idx
    
    def find_node_by_id(self, node_id: str) -> Optional[StructureNode]:
        """
        Alias for get_node_by_id.
        Provided for compatibility with structure_operations.py.
        """
        return self.get_node_by_id(node_id)
    
    def find_section_by_title(self, title: str) -> Optional[StructureNode]:
        """
        Find section heading containing title text (case-insensitive).
        
        Examples:
            find_section_by_title("DEFINITIONS") 
            find_section_by_title("BETWEEN")
            find_section_by_title("OBLIGATIONS")
        """
        title_lower = title.lower().strip()
        
        for node in self.nodes:
            if node.role == NodeRole.SECTION_HEAD:
                node_text = node.text.lower().strip()
                
                # Exact match (ignoring case)
                if title_lower == node_text.rstrip(':'):
                    return node
                
                # Title is contained in node text
                if title_lower in node_text:
                    return node
                
                # Node text starts with title (handles "DEFINITIONS:" matching "DEFINITIONS")
                if node_text.startswith(title_lower):
                    return node
        
        return None
    
    def find_clause_by_number(self, number: str) -> Optional[StructureNode]:
        """
        Find clause by its number (e.g., '1', '1.1', '2.3.1', 'I', 'II').
        
        Checks both:
        - numbering_info from detection
        - Text patterns for manual numbering
        
        Examples:
            find_clause_by_number("1")     → finds "1. Definitions..."
            find_clause_by_number("1.1")   → finds "1.1 Sub-clause..."
            find_clause_by_number("2")     → finds "2. Obligations..."
        """
        number = number.strip().rstrip('.')
        
        for node in self.nodes:
            # Check numbering_info first (most reliable)
            if node.numbering_info:
                node_num = str(node.numbering_info.get('number', '')).strip().rstrip('.')
                if node_num == number:
                    return node
            
            # Check text for manual numbering patterns
            text = node.text.strip()
            
            # Build patterns to match
            escaped_num = re.escape(number)
            patterns = [
                rf'^{escaped_num}\.\s',      # "1. " with space
                rf'^{escaped_num}\.',        # "1." 
                rf'^{escaped_num}\s',        # "1 " (space after number)
            ]
            
            for pattern in patterns:
                if re.match(pattern, text):
                    return node
            
            # Also check for ARTICLE patterns
            article_patterns = [
                rf'^ARTICLE\s+{escaped_num}\b',    # "ARTICLE 1"
                rf'^Article\s+{escaped_num}\b',    # "Article 1"
            ]
            for pattern in article_patterns:
                if re.match(pattern, text, re.IGNORECASE):
                    return node
        
        return None
    
    def find_nodes_by_role(self, role: NodeRole) -> List[StructureNode]:
        """
        Find all nodes with the given role.
        
        Examples:
            find_nodes_by_role(NodeRole.SECTION_HEAD)
            find_nodes_by_role(NodeRole.CLAUSE)
            find_nodes_by_role(NodeRole.LIST_ITEM)
        """
        return [n for n in self.nodes if n.role == role]
    
    def find_nodes_containing_text(self, text: str) -> List[StructureNode]:
        """
        Find all nodes containing the given text (case-insensitive).
        Useful for fallback text search.
        """
        text_lower = text.lower()
        return [n for n in self.nodes if text_lower in n.text.lower()]


# =============================================================================
# DETECTION FUNCTIONS
# =============================================================================

def get_paragraph_text(p) -> str:
    """Extract text from paragraph element."""
    texts = []
    for t in p.iter(f'{W}t'):
        if t.text:
            texts.append(t.text)
    return ''.join(texts)


def get_numbering_info(p, numbering_xml) -> Optional[dict]:
    """Extract Word numbering info from paragraph."""
    pPr = p.find(f'{W}pPr')
    if pPr is None:
        return None
    numPr = pPr.find(f'{W}numPr')
    if numPr is None:
        return None
    ilvl = numPr.find(f'{W}ilvl')
    numId = numPr.find(f'{W}numId')
    if ilvl is not None and numId is not None:
        return {
            'level': int(ilvl.get(f'{W}val', '0')),
            'numId': numId.get(f'{W}val', '0')
        }
    return None


def detect_manual_numbering(text: str) -> Optional[dict]:
    """Detect manual numbering patterns in text."""
    text = text.strip()
    
    # ARTICLE X pattern
    article_match = re.match(r'^ARTICLE\s+(\d+|[IVXLC]+)\s*[-–—:]?\s*', text, re.IGNORECASE)
    if article_match:
        return {'type': 'article', 'number': article_match.group(1), 'level': 0}
    
    # Sub-clause pattern (1.1, 2.3.1, etc.)
    sub_match = re.match(r'^(\d+(?:\.\d+)+)\.?\s+', text)
    if sub_match:
        parts = sub_match.group(1).split('.')
        return {'type': 'sub_clause', 'number': sub_match.group(1), 'level': len(parts) - 1}
    
    # Main clause pattern (1., 2., etc.)
    main_match = re.match(r'^(\d+)\.?\s+', text)
    if main_match:
        return {'type': 'clause', 'number': main_match.group(1), 'level': 0}
    
    # Letter pattern (a), (b), (i), (ii)
    letter_match = re.match(r'^\(([a-z]|[ivxlc]+)\)\s+', text, re.IGNORECASE)
    if letter_match:
        return {'type': 'letter', 'number': letter_match.group(1), 'level': 1}
    
    return None


def is_section_heading(text: str, is_bold: bool, is_all_caps: bool, text_length: int) -> bool:
    """Determine if text is a section heading."""
    text_stripped = text.strip()
    
    # Short bold/caps text is likely a heading
    if text_length < 100 and (is_bold or is_all_caps):
        return True
    
    # Ends with colon
    if text_stripped.endswith(':') and text_length < 80:
        return True
    
    # Common heading patterns
    heading_patterns = [
        r'^(ARTICLE|SECTION|PART|SCHEDULE|EXHIBIT|ANNEX)\s+',
        r'^(DEFINITIONS?|INTERPRETATION|RECITALS?|BACKGROUND|PARTIES)',
        r'^(TERMS|CONDITIONS|OBLIGATIONS|REPRESENTATIONS|WARRANTIES)',
        r'^(CONFIDENTIAL|NON-DISCLOSURE|GOVERNING LAW|JURISDICTION)',
    ]
    
    for pattern in heading_patterns:
        if re.match(pattern, text_stripped, re.IGNORECASE):
            return True
    
    return False


def classify_paragraph_role(text, numbering_info, manual_numbering, is_bold, is_all_caps, prev_role, indent_level) -> NodeRole:
    """Classify the role of a paragraph."""
    text_stripped = text.strip()
    text_length = len(text_stripped)
    
    if not text_stripped:
        return NodeRole.UNKNOWN
    
    # Word-numbered list item
    if numbering_info:
        return NodeRole.LIST_ITEM
    
    # Manual numbering
    if manual_numbering:
        num_type = manual_numbering['type']
        if num_type == 'article':
            return NodeRole.ARTICLE
        elif num_type == 'clause':
            return NodeRole.CLAUSE if manual_numbering['level'] == 0 else NodeRole.SUB_CLAUSE
        elif num_type == 'sub_clause':
            return NodeRole.SUB_CLAUSE
        elif num_type == 'letter':
            return NodeRole.LIST_ITEM
    
    # Section heading
    if is_section_heading(text_stripped, is_bold, is_all_caps, text_length):
        return NodeRole.SECTION_HEAD
    
    # Definition (starts with quoted term)
    if re.match(r'^["\'].*["\'].*means', text_stripped, re.IGNORECASE):
        return NodeRole.DEFINITION
    
    # Body text (follows a heading or clause)
    if prev_role in (NodeRole.SECTION_HEAD, NodeRole.ARTICLE, NodeRole.CLAUSE):
        if indent_level > 0 or text_length > 100:
            return NodeRole.BODY
    
    # Default for long text
    if text_length > 150:
        return NodeRole.BODY
    
    return NodeRole.UNKNOWN


def detect_format_label(text: str, para_style: Optional[str], numbering_info: Optional[dict], 
                        manual_numbering: Optional[dict], is_bold: bool, indent_level: int) -> str:
    """
    Detect format label for a paragraph based on priority:
    1. HEADING1/HEADING2 (if has heading style)
    2. BULLET (if has bullet)
    3. NUMBERED (if has Word auto-numbering)
    4. NUMBERED_MANUAL (if text starts with number pattern)
    5. BOLD (if bold but none of above)
    6. INDENTED (if indented but none of above)
    7. PLAIN (default)
    
    Can combine: BOLD+INDENTED when both present
    """
    labels = []
    
    # Check heading styles first (highest priority)
    if para_style:
        style_lower = para_style.lower()
        if style_lower == 'heading1' or style_lower.startswith('heading 1'):
            return "HEADING1"
        elif style_lower == 'heading2' or style_lower.startswith('heading 2'):
            return "HEADING2"
        elif style_lower.startswith('heading'):
            return "HEADING2"  # Default to HEADING2 for other heading levels
    
    # Check for bullet
    if numbering_info:
        # Word numbering format - check if bullet or numbered
        num_format = numbering_info.get('format', '')
        if num_format == 'bullet' or num_format == '':
            return "BULLET"
        else:
            return "NUMBERED"
    
    # Check for manual numbering in text
    if manual_numbering:
        return "NUMBERED_MANUAL"
    
    # Build combined label for remaining formats
    if is_bold:
        labels.append("BOLD")
    
    if indent_level > 0:
        labels.append("INDENTED")
    
    if labels:
        return "+".join(labels)
    
    return "PLAIN"


def detect_structure(docx_bytes: bytes) -> StructureMap:
    """Detect structure from document bytes."""
    with zipfile.ZipFile(BytesIO(docx_bytes), 'r') as zf:
        document_xml = zf.read('word/document.xml')
        try:
            numbering_xml = zf.read('word/numbering.xml')
        except KeyError:
            numbering_xml = None
    
    root = etree.fromstring(document_xml)
    body = root.find(f'{W}body')
    paragraphs = body.findall(f'{W}p')
    
    nodes = []
    has_sections = False
    has_manual_numbering = False
    has_word_numbering = False
    has_heading_styles = False
    heading_style_name = None
    
    prev_role = None
    parent_stack = []  # Stack of (node_id, level, role)
    
    for idx, p in enumerate(paragraphs):
        text = get_paragraph_text(p)
        if not text.strip():
            continue
        
        # Get formatting info
        pPr = p.find(f'{W}pPr')
        is_bold = False
        is_all_caps = False
        indent_level = 0
        para_style = None
        
        if pPr is not None:
            # Check for paragraph style (Heading1, Heading2, etc.)
            pStyle = pPr.find(f'{W}pStyle')
            if pStyle is not None:
                para_style = pStyle.get(f'{W}val')
                if para_style and para_style.lower().startswith('heading'):
                    has_heading_styles = True
                    if heading_style_name is None:
                        heading_style_name = para_style
            
            rPr = pPr.find(f'{W}rPr')
            if rPr is not None:
                is_bold = rPr.find(f'{W}b') is not None
            
            ind = pPr.find(f'{W}ind')
            if ind is not None:
                left = ind.get(f'{W}left', '0')
                if left.isdigit():
                    indent_level = int(left) // 720
        
        # Check first run for bold
        first_run = p.find(f'{W}r')
        if first_run is not None:
            rPr = first_run.find(f'{W}rPr')
            if rPr is not None and rPr.find(f'{W}b') is not None:
                is_bold = True
        
        # Check if all caps
        is_all_caps = text.strip().isupper() and len(text.strip()) > 3
        
        # Get numbering
        numbering_info = get_numbering_info(p, numbering_xml)
        manual_numbering = detect_manual_numbering(text)
        
        if numbering_info:
            has_word_numbering = True
        if manual_numbering:
            has_manual_numbering = True
        
        # Classify role
        role = classify_paragraph_role(
            text, numbering_info, manual_numbering,
            is_bold, is_all_caps, prev_role, indent_level
        )
        
        if role == NodeRole.SECTION_HEAD:
            has_sections = True
        
        # Determine level and parent
        level = 0
        parent_id = None
        
        if numbering_info:
            level = numbering_info['level'] + 1
        elif manual_numbering:
            level = manual_numbering['level'] + 1
        elif role == NodeRole.BODY:
            level = 1
        elif role == NodeRole.DEFINITION:
            level = 2
        
        # Find parent from stack
        while parent_stack and parent_stack[-1][1] >= level:
            parent_stack.pop()
        
        if parent_stack:
            parent_id = parent_stack[-1][0]
        
        # Create node with format_label
        node_id = f"p{idx}"
        format_label = detect_format_label(
            text, para_style, numbering_info, manual_numbering, is_bold, indent_level
        )
        node = StructureNode(
            id=node_id,
            paragraph_index=idx,
            text=text,
            role=role,
            level=level,
            parent_id=parent_id,
            numbering_info=numbering_info or manual_numbering,
            format_label=format_label,
            is_bold=is_bold,
            is_indented=(indent_level > 0)
        )
        
        nodes.append(node)
        
        # Update parent's children
        if parent_id:
            for n in nodes:
                if n.id == parent_id:
                    n.children_ids.append(node_id)
                    break
        
        # Push to stack if this can be a parent
        if role in (NodeRole.SECTION_HEAD, NodeRole.ARTICLE, NodeRole.CLAUSE, NodeRole.SUB_CLAUSE):
            parent_stack.append((node_id, level, role))
        
        prev_role = role
    
    # Determine structure type
    if has_sections and has_word_numbering:
        structure_type = "SECTIONED"
        confidence = 0.9
    elif has_manual_numbering:
        has_sub_clauses = any(
            n.role == NodeRole.SUB_CLAUSE or 
            (n.numbering_info and n.numbering_info.get('type') == 'sub_clause')
            for n in nodes
        )
        if has_sub_clauses:
            structure_type = "HIERARCHICAL"
            confidence = 0.85
        else:
            structure_type = "NUMBERED"
            confidence = 0.8
    elif has_sections:
        structure_type = "SECTIONED"
        confidence = 0.85
    else:
        structure_type = "SIMPLE"
        confidence = 0.6
    
    # Detect bullet sections: section headings (no numPr) followed by bullet items (with numPr)
    # This is a common pattern where OBLIGATIONS: is a heading, followed by bullet points
    has_bullet_sections = False
    if has_sections and has_word_numbering:
        # Check if section headings are NOT numbered but followed by numbered items
        section_nodes = [n for n in nodes if n.role == NodeRole.SECTION_HEAD]
        bullet_nodes = [n for n in nodes if n.role == NodeRole.LIST_ITEM]
        if section_nodes and bullet_nodes:
            # Sections exist and bullets exist - likely bullet section pattern
            # Additional check: ensure sections don't have manual numbering
            section_has_manual_num = any(
                n.numbering_info and n.numbering_info.get('type') in ('clause', 'article')
                for n in section_nodes
            )
            if not section_has_manual_num:
                has_bullet_sections = True
    
    return StructureMap(
        nodes=nodes,
        structure_type=structure_type,
        confidence=confidence,
        has_sections=has_sections,
        has_manual_numbering=has_manual_numbering,
        has_word_numbering=has_word_numbering,
        has_heading_styles=has_heading_styles,
        heading_style_name=heading_style_name,
        has_bullet_sections=has_bullet_sections
    )


def detect_structure_from_docx(file_path: str) -> StructureMap:
    """Detect structure from a .docx file path."""
    with open(file_path, 'rb') as f:
        return detect_structure(f.read())
