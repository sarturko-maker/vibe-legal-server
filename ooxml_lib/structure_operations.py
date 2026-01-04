"""
Structure-Aware Operations

Translates high-level structure operations into paragraph-level operations.
This module bridges between:
- AI operations (which reference structure roles and positions)
- Editor operations (which work on paragraph indices)

Example:
  AI says: "Insert new section after BETWEEN section"
  This module: Finds BETWEEN section, finds its last child, returns paragraph index

Supports HIERARCHICAL operations for multi-level inserts:
  AI says: "Add DEFINITIONS section with 3 definition items"
  Returns: Parent insert + children inserts in correct order
"""

from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
import re
import logging

try:
    from .structure_detector import StructureMap, StructureNode, NodeRole
except ImportError:
    from structure_detector import StructureMap, StructureNode, NodeRole


class InsertPosition(str, Enum):
    """Where to insert relative to a target"""
    BEFORE = "BEFORE"                    # Before the target
    AFTER = "AFTER"                      # After the target (single paragraph)
    AFTER_SECTION = "AFTER_SECTION"      # After all children of a section
    FIRST_CHILD = "FIRST_CHILD"          # As first child of target
    LAST_CHILD = "LAST_CHILD"            # As last child of target


@dataclass
class ContentNode:
    """
    A node in a hierarchical content tree.
    Used for INSERT operations.
    """
    role: NodeRole
    text: str
    children: List['ContentNode'] = field(default_factory=list)
    
    @classmethod
    def from_dict(cls, d: Dict) -> 'ContentNode':
        """Create from dictionary (parsed from AI JSON)"""
        children = [cls.from_dict(c) for c in d.get('children', [])]
        role = NodeRole[d['role']] if isinstance(d['role'], str) else d['role']
        return cls(
            role=role,
            text=d['text'],
            children=children
        )


@dataclass
class StructureOperation:
    """
    A structure-aware operation to perform.
    AI generates these, code executes them.
    """
    type: str                            # INSERT, AMEND, DELETE, INSERT
    
    # Target identification (multiple ways to find target)
    target_section: Optional[str] = None      # Section title to find
    target_clause_number: Optional[str] = None # Clause number like "1.1"
    target_node_id: Optional[str] = None      # Direct node ID
    target_text: Optional[str] = None         # Text search fallback
    
    # For INSERT operations
    position: InsertPosition = InsertPosition.AFTER
    new_role: Optional[NodeRole] = None       # What role the new content should have
    new_content: Optional[str] = None
    format: Optional[str] = None              # BOLD, BULLET, NUMBERED, PLAIN, etc.
    
    # For INSERT operations (hierarchical)
    content_tree: Optional[ContentNode] = None
    
    # For AMEND operations  
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    
    # Metadata
    reason: Optional[str] = None              # Why this change


@dataclass
class ResolvedOperation:
    """
    An operation resolved to actual paragraph indices.
    Ready for the DocumentEditor to execute.
    """
    type: str                            # INSERT, AMEND, DELETE
    paragraph_index: int                 # Which paragraph to operate on
    
    # For INSERT
    insert_after: bool = True            # Insert after (True) or before (False)
    content: Optional[str] = None
    copy_formatting_from: Optional[int] = None  # Paragraph to copy style from
    new_role: Optional[NodeRole] = None  # Role for formatting decisions
    
    # For AMEND
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    
    # Debug info
    resolution_method: str = ""          # How we found the target
    target_description: str = ""         # Human-readable target


class OperationResolver:
    """
    Resolves structure operations to paragraph-level operations.
    """
    
    def __init__(self, structure: StructureMap):
        self.structure = structure
    
    def resolve(self, op: StructureOperation) -> Optional[ResolvedOperation]:
        """
        Resolve a structure operation to a paragraph operation.
        
        Returns None if target cannot be found.
        """
        if op.type == "INSERT":
            return self._resolve_insert(op)
        elif op.type == "AMEND":
            return self._resolve_amend(op)
        elif op.type == "DELETE":
            return self._resolve_delete(op)
        else:
            return None
    
    def resolve_hierarchical(self, op: StructureOperation) -> List[ResolvedOperation]:
        """
        Resolve a hierarchical INSERT operation.
        
        Returns a LIST of ResolvedOperations in the order they should be executed.
        Each operation's index accounts for previous insertions.
        
        Example:
            Input: Insert DEFINITIONS section with 3 items after BETWEEN
            Output: [
                ResolvedOp(index=3, content="DEFINITIONS:"),
                ResolvedOp(index=4, content="Term A..."),  # +1 from previous
                ResolvedOp(index=5, content="Term B..."),  # +2 from previous
                ResolvedOp(index=6, content="Term C..."),  # +3 from previous
            ]
        """
        if op.type != "INSERT" or op.content_tree is None:
            # Fallback to single operation
            single = self.resolve(op)
            return [single] if single else []
        
        # Find the insertion anchor point
        target, method = self._find_target(op)
        if target is None:
            return []
        
        # Determine base insertion point
        if op.position == InsertPosition.AFTER_SECTION:
            last = self.structure.get_last_descendant(target)
            base_index = last.paragraph_index
        else:
            base_index = target.paragraph_index
        
        # Build list of operations with incrementing indices
        operations = []
        current_index = base_index
        
        def add_node(node: ContentNode, depth: int = 0):
            nonlocal current_index
            
            # Find formatting source for this role
            format_source = self._find_format_source_by_role(node.role)
            
            operations.append(ResolvedOperation(
                type="INSERT",
                paragraph_index=current_index,
                insert_after=True,
                content=node.text,
                copy_formatting_from=format_source,
                new_role=node.role,
                resolution_method=method,
                target_description=f"Hierarchical insert depth {depth}"
            ))
            current_index += 1
            
            # Recursively add children
            for child in node.children:
                add_node(child, depth + 1)
        
        # Start with the root content node
        add_node(op.content_tree)
        
        return operations
    
    def _find_target(self, op: StructureOperation) -> Tuple[Optional[StructureNode], str]:
        """
        Find the target node using various identification methods.
        Returns (node, method_used) or (None, error_message).
        """
        # Method 1: Direct node ID
        if op.target_node_id:
            node = self.structure.find_node_by_id(op.target_node_id)
            if node:
                return node, "node_id"
        
        # Method 2: Section title
        if op.target_section:
            node = self.structure.find_section_by_title(op.target_section)
            if node:
                return node, "section_title"
        
        # Method 3: Clause number
        if op.target_clause_number:
            node = self.structure.find_clause_by_number(op.target_clause_number)
            if node:
                return node, "clause_number"
        
        # Method 4: Text search (fallback)
        if op.target_text:
            for node in self.structure.nodes:
                if op.target_text.lower() in node.text.lower():
                    return node, "text_search"
        
        return None, "not_found"
    
    def _resolve_insert(self, op: StructureOperation) -> Optional[ResolvedOperation]:
        """Resolve an INSERT operation."""
        target, method = self._find_target(op)
        
        # Debug logging
        if target:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"  _resolve_insert: Found target '{target.id}' at para {target.paragraph_index} "
                       f"text='{target.text[:30] if target.text else ''}...' via {method}")
        else:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"  _resolve_insert: Target NOT found for clause={op.target_clause_number} "
                       f"section={op.target_section} node_id={op.target_node_id}")
        
        if target is None:
            return None
        
        # Determine insertion point based on position
        if op.position == InsertPosition.BEFORE:
            para_index = target.paragraph_index
            insert_after = False
        
        elif op.position == InsertPosition.AFTER:
            para_index = target.paragraph_index
            insert_after = True
        
        elif op.position == InsertPosition.AFTER_SECTION:
            # For headed documents (manual numbering without Word auto-numbering),
            # we need to find the LAST paragraph belonging to this clause
            if self.structure.has_manual_numbering and not self.structure.has_word_numbering:
                # Check if target is a clause heading (has number like "4.")
                is_clause_heading = (
                    target.role == NodeRole.CLAUSE or 
                    target.role == NodeRole.ARTICLE or
                    re.match(r'^\d+\.', target.text[:10] if target.text else '')
                )
                if is_clause_heading:
                    # Use helper to find last body paragraph
                    para_index = self.structure.get_last_paragraph_of_clause(target)
                    logger = logging.getLogger(__name__)
                    logger.info(f"  AFTER_SECTION: clause heading at {target.paragraph_index}, "
                               f"last body at {para_index}")
                else:
                    para_index = target.paragraph_index
            else:
                # Original logic for Word auto-numbered documents
                last = self.structure.get_last_descendant(target)
                para_index = last.paragraph_index
            insert_after = True
        
        elif op.position == InsertPosition.FIRST_CHILD:
            para_index = target.paragraph_index
            insert_after = True  # Right after parent = first child position
        
        elif op.position == InsertPosition.LAST_CHILD:
            if target.children_ids:
                last_child = self.structure.find_node_by_id(target.children_ids[-1])
                para_index = last_child.paragraph_index
            else:
                para_index = target.paragraph_index
            insert_after = True
        
        else:
            para_index = target.paragraph_index
            insert_after = True
        
        # Cap para_index to document bounds
        if self.structure.nodes:
            max_index = max(n.paragraph_index for n in self.structure.nodes)
            if para_index > max_index:
                logger = logging.getLogger(__name__)
                logger.warning(f"  para_index {para_index} exceeds max {max_index}, capping to max")
                para_index = max_index
        
        # Find a paragraph to copy formatting from
        format_source = self._find_format_source(op.new_role, target)
        
        return ResolvedOperation(
            type="INSERT",
            paragraph_index=para_index,
            insert_after=insert_after,
            content=op.new_content,
            copy_formatting_from=format_source,
            new_role=op.new_role,
            resolution_method=method,
            target_description=f"{target.role.value}: {target.text_preview}"
        )
    
    def _resolve_amend(self, op: StructureOperation) -> Optional[ResolvedOperation]:
        """Resolve an AMEND operation."""
        target, method = self._find_target(op)
        
        if target is None:
            return None
        
        return ResolvedOperation(
            type="AMEND",
            paragraph_index=target.paragraph_index,
            old_text=op.old_text,
            new_text=op.new_text,
            resolution_method=method,
            target_description=f"{target.role.value}: {target.text_preview}"
        )
    
    def _resolve_delete(self, op: StructureOperation) -> Optional[ResolvedOperation]:
        """Resolve a DELETE operation."""
        target, method = self._find_target(op)
        
        if target is None:
            return None
        
        return ResolvedOperation(
            type="DELETE",
            paragraph_index=target.paragraph_index,
            resolution_method=method,
            target_description=f"{target.role.value}: {target.text_preview}"
        )
    
    def _find_format_source(self, new_role: Optional[NodeRole], near: StructureNode) -> Optional[int]:
        """
        Find a paragraph to copy formatting from.
        Looks for a sibling with the same role, or falls back to the target.
        """
        if new_role is None:
            return near.paragraph_index
        
        # Look for a sibling with the same role
        siblings = self.structure.get_siblings(near)
        for sibling in siblings:
            if sibling.role == new_role:
                return sibling.paragraph_index
        
        # Look for any node with the same role
        same_role = self.structure.find_nodes_by_role(new_role)
        if same_role:
            return same_role[0].paragraph_index
        
        # Fallback to target
        return near.paragraph_index
    
    def _find_format_source_by_role(self, role: NodeRole) -> Optional[int]:
        """Find any paragraph with the given role to copy formatting from."""
        same_role = self.structure.find_nodes_by_role(role)
        if same_role:
            return same_role[0].paragraph_index
        return None


# =============================================================================
# AI PROMPT GENERATION
# =============================================================================

def generate_structure_prompt(structure: StructureMap) -> str:
    """
    Generate the structure section of an AI prompt.
    Tells the AI how the document is structured and how to reference it.
    """
    lines = [
        "## DOCUMENT STRUCTURE",
        "",
        f"Structure type: {structure.structure_type}",
        f"Maximum depth: {structure.max_depth}",
        "",
        "### SECTIONS AND CLAUSES",
        ""
    ]
    
    def format_node(node: StructureNode, indent: int = 0) -> str:
        prefix = "  " * indent
        role_label = node.role.value
        num = f" [{node.number_value}]" if node.number_value else ""
        return f"{prefix}- {role_label}{num}: \"{node.text_preview}\" (id: {node.id})"
    
    def add_node_recursive(node: StructureNode, indent: int = 0):
        lines.append(format_node(node, indent))
        for child_id in node.children_ids:
            child = structure.find_node_by_id(child_id)
            if child:
                add_node_recursive(child, indent + 1)
    
    # Add top-level nodes
    root_nodes = [n for n in structure.nodes if n.parent_id is None and n.role != NodeRole.UNKNOWN]
    for node in root_nodes:
        add_node_recursive(node)
    
    lines.extend([
        "",
        "### HOW TO REFERENCE TARGETS",
        "",
        "When specifying operations, use ONE of these methods:",
        "",
        "1. **Section title** - for section headings:",
        "   `\"target_section\": \"BETWEEN\"` or `\"target_section\": \"OBLIGATIONS\"`",
        "",
        "2. **Clause number** - for numbered clauses:",
        "   `\"target_clause_number\": \"1.1\"` or `\"target_clause_number\": \"2\"`",
        "",
        "3. **Node ID** - for specific paragraphs (from structure above):",
        "   `\"target_node_id\": \"p3\"`",
        "",
        "### OPERATION TYPES",
        "",
        "**AMEND** - Change text within an existing paragraph",
        "**INSERT** - Add new paragraph(s) - use multiple INSERTs for sections with multiple items",
        "**DELETE** - Remove a paragraph",
        "",
        "### INSERT POSITIONS",
        "",
        "- `\"position\": \"AFTER\"` - immediately after target paragraph",
        "- `\"position\": \"BEFORE\"` - immediately before target paragraph", 
        "- `\"position\": \"AFTER_SECTION\"` - after ALL children of a section",
        "- `\"position\": \"LAST_CHILD\"` - as last item inside a section",
        "",
        "### EXAMPLES",
        "",
        "**Simple insert** - Add new section after BETWEEN:",
        "```json",
        "{",
        "  \"type\": \"INSERT\",",
        "  \"target_section\": \"BETWEEN\",",
        "  \"position\": \"AFTER_SECTION\",",
        "  \"new_role\": \"SECTION_HEAD\",",
        "  \"new_content\": \"DEFINITIONS:\"",
        "}",
        "```",
        "",
        "**Multiple inserts** - Add section with items (all target same paragraph):",
        "```json",
        "[",
        "  {\"type\": \"INSERT\", \"target_node_id\": \"p8\", \"position\": \"AFTER\", \"new_content\": \"EXCLUSIONS:\"},",
        "  {\"type\": \"INSERT\", \"target_node_id\": \"p8\", \"position\": \"AFTER\", \"new_content\": \"is or becomes publicly available...\"},",
        "  {\"type\": \"INSERT\", \"target_node_id\": \"p8\", \"position\": \"AFTER\", \"new_content\": \"was lawfully in possession...\"}",
        "]",
        "```",
        "(System processes in reverse order so content appears in correct order)",
        "",
        "**Amend text:**",
        "```json",
        "{",
        "  \"type\": \"AMEND\",",
        "  \"target_section\": \"OBLIGATIONS\",",
        "  \"old_text\": \"strict confidentiality\",",
        "  \"new_text\": \"strict confidentiality at all times\"",
        "}",
        "```",
        ""
    ])
    
    return "\n".join(lines)


def generate_full_ai_prompt(structure: StructureMap, document_text: str, instructions: str) -> str:
    """
    Generate a complete AI prompt for redlining with structure awareness.
    """
    structure_section = generate_structure_prompt(structure)
    
    return f"""You are a legal document editor. Your task is to suggest changes to a contract.

{structure_section}

## DOCUMENT TEXT

{document_text}

## YOUR INSTRUCTIONS

{instructions}

## OUTPUT FORMAT

Return a JSON object with an "operations" array:

```json
{{
  "operations": [
    {{
      "type": "AMEND",
      "target_section": "OBLIGATIONS",
      "old_text": "exact text to find",
      "new_text": "replacement text",
      "reason": "Why this change"
    }},
    {{
      "type": "INSERT",
      "target_section": "BETWEEN",
      "position": "AFTER_SECTION",
      "content_tree": {{
        "role": "SECTION_HEAD",
        "text": "DEFINITIONS:",
        "children": [
          {{"role": "LIST_ITEM", "text": "Definition 1...", "children": []}},
          {{"role": "LIST_ITEM", "text": "Definition 2...", "children": []}}
        ]
      }},
      "reason": "Adding definitions section"
    }}
  ]
}}
```

RULES:
1. For AMEND: Only change the specific text that needs changing
2. For INSERT: Use multiple INSERT operations targeting same paragraph for multi-item sections
3. Always reference targets using section title, clause number, or node ID
4. Include a reason for each change
5. Roles must be: SECTION_HEAD, ARTICLE, CLAUSE, SUB_CLAUSE, LIST_ITEM, BODY, DEFINITION
"""
