"""
Job Processor v2.0 - Structure-Aware Processing

Key improvements:
1. Detects document structure BEFORE AI call
2. Includes structure info in AI prompt
3. AI returns operations with structure references (not text search)
4. Resolves operations to paragraph indices using StructureMap
5. Executes operations in correct order (AMENDs first, INSERTs in reverse)
"""

import asyncio
import logging
import os
import json
import re
from datetime import datetime
from typing import List, Dict, Any, Optional

from models import Job, JobStatus, AiOperation, StructureAwareOperation
from ooxml_lib import DocumentEditor, StyleAwareEditor, Styler
from ooxml_lib.format_extractor import extract_format_map
from ooxml_lib.styler_ai import run_styler_ai
from ooxml_lib.format_applier import apply_styler_fixes
from ooxml_lib import (
    detect_structure_from_docx,
    generate_structure_prompt,
    StructureMap,
    OperationResolver,
    InsertPosition,
    NodeRole,
    ContentNode,
    StructureOperation
)
from document_extractor import extract_document
from ai_client import call_gemini
from style_detector import detect_document_style, get_ai_prompt_additions, DocumentStyle
from logging_utils import (
    log_separator, log_ai_request, log_ai_response, 
    log_operation_result, log_processing_summary
)

logger = logging.getLogger("vibelegal")

# =============================================================================
# PROMPTS
# =============================================================================

BASE_PROMPT = """You are a legal document reviewer. Analyze this contract against the playbook rules.

Return ONLY a JSON array of operations. No markdown, no explanation."""

STRUCTURE_AWARE_RULES = """
=============================================================
REDLINING PRINCIPLES - ACT LIKE A LAWYER WITH A RED PEN
=============================================================

1. PREFER AMEND OVER INSERT
   - If you can achieve the change by modifying existing text, use AMEND
   - Only use INSERT when adding entirely NEW clauses that have no existing equivalent
   - Example: Adding exclusions to a definition? AMEND the definition paragraph
   - Example: Adding a new "Limitation of Liability" clause? Use INSERT

2. MAKE SURGICAL, TARGETED CHANGES
   - Change ONLY what needs changing
   - Do NOT rewrite entire clauses when only a phrase needs adjustment
   - Do NOT delete and replace sections when you can amend specific sentences
   - Each operation should be the MINIMUM change needed

3. AMEND OPERATION RULES
   - ALWAYS provide target_node_id (the paragraph ID like "p11")
   - The "find" text must match the document EXACTLY
   - Keep "find" text SHORT - just enough to be unique within that paragraph
   - The "replace" text contains the new version of that text

   EXAMPLE - Adding a sentence to end of paragraph:
   
   Original p17: "The Receiving Party shall not disclose without prior written consent."
   
   BAD (too much text in find):
   {
     "operation": "AMEND",
     "target_node_id": "p17",
     "find": "The Receiving Party shall not disclose without prior written consent.",
     "replace": "The Receiving Party shall not disclose without prior written consent. Disclosure is permitted if required by law."
   }
   This strikes through the ENTIRE sentence unnecessarily.
   
   GOOD (minimal find):
   {
     "operation": "AMEND",
     "target_node_id": "p17",
     "find": "written consent.",
     "replace": "written consent. Disclosure is permitted if required by law."
   }
   This only strikes through "written consent." - much cleaner.

4. FIND TEXT PRECISION
   - Find the SHORTEST text that uniquely identifies where to make the change
   - If adding to end of paragraph, find just the last few words + punctuation
   - If changing a specific phrase, find just that phrase
   
   EXAMPLES:
   
   Changing "five years" to "three years":
   find: "five years"
   replace: "three (3) years"
   
   Adding jurisdiction clause after governing law:
   find: "England and Wales."
   replace: "England and Wales. The parties submit to the exclusive jurisdiction of the English courts."
   
   Narrowing a definition:
   find: "all information, whether written, oral, or electronic"
   replace: "information that is marked as confidential in writing"

5. WHEN TO USE INSERT vs AMEND - CRITICAL DISTINCTION
   
   **AMEND** = CHANGE existing text in document
   - REQUIRES: old_text (text that exists) AND new_text (replacement)
   - Use when: Modifying wording, adding to end of existing paragraph
   - Example: Change "five years" → "three years"
   
   **INSERT** = ADD new content that doesn't exist yet
   - REQUIRES: new_content (the new text to add)
   - Use when: Adding new clauses, sections, definitions
   - NO old_text needed - you're adding, not replacing
   
   ⚠️ COMMON MISTAKE: Using AMEND with reason "Inserting..." 
   If you're inserting new content, use INSERT, not AMEND!
   
   USE AMEND when:
   - Changing wording within an existing clause
   - Adding a sentence to an existing paragraph (extend the last sentence)
   - Narrowing or expanding an existing definition
   
   USE INSERT when:
   - Adding a completely new numbered clause
   - Adding a new section with its own heading
   - The document has no existing equivalent for what you're adding

6. DO NOT
   - Rewrite clauses that only need small tweaks
   - Delete entire sections just to add a sentence
   - Use INSERT when AMEND would work
   - Provide find text that doesn't exist exactly in the document
   - Include more context in "find" than necessary

---

## TARGETING RULES

### For INSERT Operations - ALWAYS USE target_node_id
You can see the full structure map. Pick the SPECIFIC paragraph to insert after.
DO NOT use target_section for INSERTs - use target_node_id directly.

Example: To insert after a section, target the LAST paragraph of that section:
```
Structure map shows:
  p4: SECTION_HEAD: CONFIDENTIAL INFORMATION INCLUDES:
  p5: LIST_ITEM: Trade secrets...
  p6: LIST_ITEM: Customer lists...
  p7: LIST_ITEM: Financial information...
  p8: LIST_ITEM: Technical specifications...  ← target this (last item)
  p9: SECTION_HEAD: OBLIGATIONS:

Use: {"target_node_id": "p8", ...}  NOT: {"target_section": "CONFIDENTIAL INFORMATION"}
```

### For AMEND/DELETE Operations
1. target_clause_number - for numbered clauses: "1", "2", "1.1"
2. target_section - for section headings: "BETWEEN", "OBLIGATIONS"
3. target_node_id - for specific paragraphs: "p3"

### Position Values (for INSERT only)
- "AFTER" - immediately after target paragraph (USE THIS!)

### INSERT ORDER - IMPORTANT!
When inserting multiple items at the same paragraph, send them in DISPLAY ORDER:
  - The order you send them is the order they will appear
  - Example: To insert "EXCLUSIONS:", "(i)...", "(ii)...", "(iii)..."
    Send: EXCLUSIONS first, then (i), then (ii), then (iii)
  - System handles the insertion mechanics automatically

### CLAUSE NUMBERING RULES

For documents with MANUAL numbering (plain text like "1.", "2."):
- Include the clause number in your INSERT content (e.g., "5. Limitation of Liability...")
- You MUST provide AMEND operations to renumber all subsequent clauses
- **CRITICAL: PLAN ALL RENUMBERING FIRST - SEE BELOW**

For documents with WORD auto-numbering (detected automatically):
- Do NOT include clause numbers in your content
- Do NOT try to renumber existing clauses
- Word will update numbering automatically

### MANUAL NUMBERING - PLAN ALL CHANGES FIRST (CRITICAL!)

When inserting multiple clauses in a manually-numbered document, you MUST plan ahead.

**THE PROBLEM WITH INCREMENTAL RENUMBERING:**

BAD approach (causes garbage text):
- Insert Clause 2 → Send AMEND: "2. Obligations" → "3. Obligations"
- Insert Clause 4 → Send AMEND: "3. Obligations" → "4. Obligations"  ← WRONG! It's already "3."!

Each AMEND changes the document. Later find text won't match!

**THE CORRECT APPROACH:**

1. FIRST: Decide ALL insertions you will make
2. SECOND: Calculate the FINAL number for each existing clause
3. THIRD: Send ONE renumber operation per clause with the FINAL number

**EXAMPLE:**

Original document:
- 1. Definitions
- 2. Obligations  
- 3. Term
- 4. Return
- 5. Governing Law

You want to insert 2 new clauses after "1. Definitions" and "2. Obligations"

Calculate FINAL numbers:
- "1. Definitions" → stays 1
- NEW: "2. Exclusions" (insert)
- "2. Obligations" → becomes 3 (was 2, +1)
- NEW: "4. Permitted Disclosure" (insert)
- "3. Term" → becomes 5 (was 3, +2)
- "4. Return" → becomes 6 (was 4, +2)
- "5. Governing Law" → becomes 7 (was 5, +2)

Send operations with FINAL numbers:
```json
[
  {"type": "AMEND", "target_node_id": "p3", "find": "2. Obligations", "replace": "3. Obligations"},
  {"type": "AMEND", "target_node_id": "p4", "find": "3. Term", "replace": "5. Term"},
  {"type": "AMEND", "target_node_id": "p5", "find": "4. Return", "replace": "6. Return"},
  {"type": "AMEND", "target_node_id": "p6", "find": "5. Governing Law", "replace": "7. Governing Law"},
  {"type": "INSERT", "target_node_id": "p1", "position": "AFTER", "format": "NUMBERED", "new_content": "2. Exclusions..."},
  {"type": "INSERT", "target_node_id": "p3", "position": "AFTER", "format": "NUMBERED", "new_content": "4. Permitted Disclosure..."}
]
```

**KEY RULE: Each existing clause gets ONE renumber operation with its FINAL number.**


### CLAUSE PLACEMENT RULES

When inserting new clauses, place them in LOGICAL positions:

1. Definition-related clauses (Exclusions, Marking Requirements)
   → Insert AFTER the Definitions clause

2. Obligation-related clauses (Permitted Disclosure, Legal Compulsion)
   → Insert AFTER the Obligations clause

3. Procedural clauses (Return/Destruction, Notice)
   → Insert BEFORE Governing Law

4. Risk allocation (Limitation of Liability, Indemnification)
   → Insert BEFORE Governing Law

5. Governing Law and Jurisdiction
   → Should remain LAST

Do NOT cluster all new clauses at the end after Governing Law.

### INSERT STRUCTURE RULES

**Match the document's existing structure.**

Look at the format labels in the structure map. When inserting new content:

1. OBSERVE how existing clauses are structured in THIS document
2. MATCH that pattern with your inserts
3. Each INSERT creates ONE paragraph
4. Send multiple INSERTs if the pattern requires multiple paragraphs
5. Include the format field matching what you see

---

## COMMON DOCUMENT STRUCTURES AND HOW TO INSERT

### Structure 1: Inline Numbered (Simple Contracts)

Document shows:
[p3] NUMBERED+BOLD_START: Purpose. The Parties wish to explore a potential business relationship.
[p4] NUMBERED+BOLD_START: Confidentiality. Each Party agrees to keep confidential all information.

Pattern: Number + Bold title + Body all in ONE paragraph

To insert new clause:
```json
{"type": "INSERT", "target_node_id": "p4", "position": "AFTER", "format": "NUMBERED", "new_content": "Exclusions. Confidential Information shall not include information that is publicly available."}
```

---

### Structure 2: Separate Heading + Indented Body (Formal NDAs)

Document shows:
[p10] NUMBERED+BOLD: 1. Definitions
[p11] INDENTED: "Confidential Information" shall mean all information disclosed by the Disclosing Party.

Pattern: Bold numbered heading, then indented body paragraph

To insert new clause - send TWO inserts:
```json
{"type": "INSERT", "target_node_id": "p11", "position": "AFTER", "format": "NUMBERED", "new_content": "2. Exclusions"}
{"type": "INSERT", "target_node_id": "p11", "position": "AFTER", "format": "INDENTED", "new_content": "Confidential Information shall not include information that: (i) is publicly available; or (ii) was lawfully in possession before disclosure."}
```

---

### Structure 3: Heading + Numbered Sub-Clauses (Complex Agreements)

Document shows:
[p10] BOLD: 1. DEFINITIONS
[p11] NUMBERED: 1.1 "Agreement" means this Non-Disclosure Agreement.
[p12] NUMBERED: 1.2 "Confidential Information" means all information disclosed.

To add new definition:
```json
{"type": "INSERT", "target_node_id": "p12", "position": "AFTER", "format": "NUMBERED", "new_content": "1.3 \"Excluded Information\" means information that is publicly available or independently developed."}
```

To add new section:
```json
{"type": "INSERT", "target_node_id": "p12", "position": "AFTER", "format": "BOLD", "new_content": "2. EXCLUSIONS"}
{"type": "INSERT", "target_node_id": "p12", "position": "AFTER", "format": "NUMBERED", "new_content": "2.1 Confidential Information shall not include information that is or becomes publicly available."}
```

---

### Structure 4: Bullet Sections (Informal Agreements)

Document shows:
[p4] BOLD: CONFIDENTIAL INFORMATION INCLUDES:
[p5] BULLET: Trade secrets and proprietary data
[p6] BULLET: Customer lists and business relationships

To insert new section after p6:
```json
{"type": "INSERT", "target_node_id": "p6", "position": "AFTER", "format": "BOLD", "new_content": "EXCLUSIONS:"}
{"type": "INSERT", "target_node_id": "p6", "position": "AFTER", "format": "BULLET", "new_content": "Information that is or becomes publicly available"}
{"type": "INSERT", "target_node_id": "p6", "position": "AFTER", "format": "BULLET", "new_content": "Information lawfully in possession before disclosure"}
```

---

### Structure 5: Lettered Sub-Clauses (Detailed Provisions)

Document shows:
[p15] NUMBERED+BOLD_START: 5. Permitted Disclosures. The Receiving Party may disclose Confidential Information:
[p16] LETTERED+INDENTED: (a) to employees who need to know;
[p17] LETTERED+INDENTED: (b) to professional advisers;

To add new sub-item:
```json
{"type": "INSERT", "target_node_id": "p17", "position": "AFTER", "format": "LETTERED", "new_content": "(c) to potential acquirers in connection with a bona fide corporate transaction."}
```

---

## FORMAT QUICK REFERENCE

| Format | When to Use |
|--------|-------------|
| `NUMBERED` | Numbered clauses (1. 2. 3. or 1.1 1.2 etc.) |
| `BULLET` | Bullet point items |
| `LETTERED` | Lettered items (a) (b) (c) or (A) (B) (C) |
| `ROMAN` | Roman numeral items (i) (ii) (iii) |
| `BOLD` | Bold headings or defined terms |
| `INDENTED` | Indented body text under headings |
| `HEADING1` | Major section headings (Articles, Schedules) |
| `HEADING2` | Sub-section headings |
| `PLAIN` | Plain text with no special formatting |

Combine formats when needed: `NUMBERED+BOLD_START`, `LETTERED+INDENTED`

---

## KEY RULES

1. **ONE paragraph per INSERT** - never combine multiple paragraphs in one insert
2. **Send INSERTs in DISPLAY ORDER** - the order you send them is the order they appear
3. **Match the pattern you see** - if document uses BOLD headings + INDENTED body, do the same
4. **Include format field** - always specify the format for each INSERT
5. **NEVER use newlines in new_content** - each INSERT is one paragraph

---

## OPERATION FORMATS

**AMEND** (change text - PREFERRED!):
{
  "type": "AMEND",
  "target_node_id": "p12",  # REQUIRED
  "find": "just the text to find",
  "replace": "replacement text",
  "reason": "Adding exclusions to definition"
}

**AMEND for heading change** (clause removal):
{
  "type": "AMEND",
  "target_clause_number": "6",
  "find": "NON-SOLICITATION",
  "replace": "[RESERVED]",
  "reason": "Removing non-solicitation clause"
}

**DELETE** (remove body paragraph):
{
  "type": "DELETE",
  "target_clause_number": "6",
  "reason": "Removing body of reserved clause"
}

**INSERT** (ONLY for truly new content with no existing text to amend):
{
  "type": "INSERT",
  "target_clause_number": "4",
  "position": "AFTER_SECTION",
  "new_role": "CLAUSE",
  "new_content": "Return of Information. Upon termination...",
  "reason": "Adding new clause not covered by existing text"
}

### Role Values
SECTION_HEAD, ARTICLE, CLAUSE, SUB_CLAUSE, LIST_ITEM, BODY, DEFINITION
"""


# =============================================================================
# HELPER FUNCTIONS FOR DOCUMENT TYPE DETECTION
# =============================================================================

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def _detect_bold_titles(style_editor, structure) -> bool:
    """
    Detect if clause titles in manual-numbered docs use bold.
    Checks first few numbered clauses for bold formatting.
    """
    for node in structure.nodes[:10]:
        if re.match(r'^\d+\.', node.text[:10] if len(node.text) > 10 else node.text):
            # Found a numbered clause - check if bold
            paragraphs = style_editor.body.findall(f'{W}p')
            if node.paragraph_index < len(paragraphs):
                para = paragraphs[node.paragraph_index]
                first_run = para.find(f'{W}r')
                if first_run is not None:
                    rPr = first_run.find(f'{W}rPr')
                    if rPr is not None and rPr.find(f'{W}b') is not None:
                        return True
                return False  # Found numbered clause, not bold
    return False  # Default


def _calculate_next_manual_clause_number(after_index: int, structure) -> int:
    """
    Calculate the next clause number for manual-numbered documents.
    Finds the highest numbered clause at or before after_index and returns +1.
    """
    last_num_before = 0
    
    for node in structure.nodes:
        if node.paragraph_index <= after_index:
            match = re.match(r'^(\d+)\.', node.text[:10] if len(node.text) > 10 else node.text)
            if match:
                num = int(match.group(1))
                if num > last_num_before:
                    last_num_before = num
    
    return last_num_before + 1


def strip_leading_number(text: str) -> str:
    """
    Remove leading clause numbers like '5. ' or '5.1 ' from text.
    AI sometimes includes numbers that Word/system should generate.
    """
    if not text:
        return text
    # Pattern: "5. ", "5.1 ", "5.1.2 ", "(a) ", "(i) "
    cleaned = re.sub(r'^[\d\.]+\s+', '', text)  # "5. " or "5.1. "
    cleaned = re.sub(r'^\([a-z]+\)\s+', '', cleaned)  # "(a) "
    cleaned = re.sub(r'^\([ivxlc]+\)\s+', '', cleaned, flags=re.IGNORECASE)  # "(i) "
    return cleaned.strip()


# =============================================================================
# PARSING AI OPERATIONS
# =============================================================================

def parse_ai_operation(op_dict: dict) -> StructureOperation:
    """Convert AI response dict to StructureOperation"""
    
    # Parse content_tree if present
    content_tree = None
    if op_dict.get('content_tree'):
        content_tree = ContentNode.from_dict(op_dict['content_tree'])
    
    # Parse position
    position = InsertPosition.AFTER
    if op_dict.get('position'):
        try:
            position = InsertPosition[op_dict['position']]
        except KeyError:
            logger.warning(f"Unknown position '{op_dict.get('position')}', defaulting to AFTER")
    
    # Parse role (AI may use 'new_role' or 'format' field)
    new_role = None
    role_str = op_dict.get('new_role') or op_dict.get('format')
    if role_str:
        try:
            new_role = NodeRole[role_str]
            logger.info(f"  Parsed role: {role_str} -> {new_role}")
        except KeyError:
            logger.warning(f"Unknown role '{role_str}'")
    
    return StructureOperation(
        type=op_dict.get('type', 'AMEND').upper(),
        target_section=op_dict.get('target_section'),
        target_clause_number=op_dict.get('target_clause_number'),
        target_node_id=op_dict.get('target_node_id'),
        target_text=op_dict.get('target_text') or op_dict.get('after_clause'),  # Fallback for legacy
        position=position,
        new_role=new_role,
        new_content=op_dict.get('new_content') or op_dict.get('new_clause_body'),
        content_tree=content_tree,
        old_text=op_dict.get('old_text') or op_dict.get('original_text') or op_dict.get('find'),
        new_text=op_dict.get('new_text') or op_dict.get('replacement_text') or op_dict.get('replace'),
        reason=op_dict.get('reason', '')
    )


def parse_ai_response(response_text: str) -> List[StructureOperation]:
    """Parse AI response text into list of StructureOperations"""
    
    # Strip markdown code blocks
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0].strip()
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0].strip()
    
    # Sanitize control characters inside JSON strings (AI sometimes returns literal newlines)
    # Use regex to find string values and clean control chars inside them
    import re
    
    def clean_control_chars_in_strings(match):
        """Replace control chars in a matched JSON string with spaces"""
        s = match.group(0)
        # Replace ALL control chars (including newlines) inside the string
        # JSON strings cannot contain literal newlines - they must be escaped
        result = []
        for char in s:
            if ord(char) < 32:  # All control characters
                result.append(' ')  # Replace with space
            else:
                result.append(char)
        return ''.join(result)
    
    # Regex to find JSON strings (respecting escapes)
    # This matches: " followed by (escaped chars or non-quote chars) followed by "
    string_pattern = r'"(?:[^"\\]|\\.)*"'
    
    # Debug: count control chars before
    ctrl_before = sum(1 for c in response_text if ord(c) < 32 and c != '\n' and c != '\r' and c != '\t')
    logger.info(f"DEBUG: Control chars (excluding newlines) before clean: {ctrl_before}")
    
    response_text = re.sub(string_pattern, clean_control_chars_in_strings, response_text, flags=re.DOTALL)
    
    # Debug: count control chars after  
    ctrl_after = sum(1 for c in response_text if ord(c) < 32 and c != '\n' and c != '\r' and c != '\t')
    logger.info(f"DEBUG: Control chars (excluding newlines) after clean: {ctrl_after}")
    
    try:
        data = json.loads(response_text)
        
        # Handle both array and object with operations key
        if isinstance(data, list):
            operations_data = data
        elif isinstance(data, dict) and 'operations' in data:
            operations_data = data['operations']
        else:
            logger.error("AI response is not a valid operations array or object")
            return []
        
        operations = []
        for op_dict in operations_data:
            try:
                op = parse_ai_operation(op_dict)
                operations.append(op)
            except Exception as e:
                logger.warning(f"Failed to parse operation {op_dict}: {e}")
        
        return operations
    
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse AI response as JSON: {e}")
        return []


def parse_title_body(content: str) -> tuple[str, str]:
    """
    Parse content into title and body.
    
    Handles formats like:
    - "Title. Body text here..."
    - "1.1 Title. Body text here..."
    - Just "Body text" (no title)
    
    Returns (title, body)
    """
    if not content:
        return "", ""
    
    content = content.strip()
    
    # Look for "Title. Body" pattern
    # Match: optional number, then title ending with period, then body
    import re
    match = re.match(r'^((?:\d+\.?\d*\s+)?[^.]+)\.\s+(.+)$', content, re.DOTALL)
    if match:
        title = match.group(1).strip()
        body = match.group(2).strip()
        return title, body
    
    # No clear title/body separation - treat first sentence as title
    if '. ' in content:
        parts = content.split('. ', 1)
        return parts[0], parts[1] if len(parts) > 1 else ""
    
    # Just body, no title
    return "", content


# =============================================================================
# APPLYING OPERATIONS
# =============================================================================

def apply_operations_in_order(
    operations: List[StructureOperation],
    editor: DocumentEditor,
    style_editor: StyleAwareEditor,
    structure: StructureMap,
    style: DocumentStyle = None
) -> tuple[int, int]:
    """
    Execute operations in correct order to avoid index shifting problems.
    
    Order:
    1. AMEND operations first (don't change paragraph count)
    2. DELETE operations in REVERSE index order (highest first)
    3. INSERT operations in REVERSE index order (highest first)
    
    Returns (success_count, failed_count)
    """
    resolver = OperationResolver(structure)
    
    # Separate by type
    amends = [op for op in operations if op.type == "AMEND"]
    deletes = [op for op in operations if op.type == "DELETE"]
    inserts = [op for op in operations if op.type in ("INSERT", "INSERT_CLAUSE")]
    
    success = 0
    failed = 0
    
    # --- Execute AMENDs first (order doesn't matter) ---
    for op in amends:
        resolved = resolver.resolve(op)
        if resolved and resolved.old_text and resolved.new_text:
            result = style_editor.amend_text(resolved.old_text, resolved.new_text, paragraph_index=resolved.paragraph_index)
            if result:
                success += 1
                log_operation_result(success + failed, "AMEND", True, resolved.old_text[:30])
            else:
                failed += 1
                log_operation_result(success + failed, "AMEND", False, f"'{resolved.old_text[:30]}...' not found")
        elif op.old_text and op.new_text:
            # Fallback to text search if no structure target
            result = style_editor.amend_text(op.old_text, op.new_text)
            if result:
                success += 1
                log_operation_result(success + failed, "AMEND", True, op.old_text[:30])
            else:
                failed += 1
                log_operation_result(success + failed, "AMEND", False, f"'{op.old_text[:30]}...' not found")
        else:
            failed += 1
            log_operation_result(success + failed, "AMEND", False, "Missing old_text or new_text")
    
    # --- Resolve all inserts to get indices ---
    resolved_inserts = []
    for op in inserts:
        if op.type == "INSERT_CLAUSE":
            # Legacy text-search insert - handle separately
            resolved_inserts.append((None, op))
        else:
            resolved = resolver.resolve(op)
            if resolved:
                logger.info(f"  Resolved INSERT: para_index={resolved.paragraph_index}, "
                           f"insert_after={resolved.insert_after}, "
                           f"content={resolved.content[:40] if resolved.content else 'None'}...")
                resolved_inserts.append((resolved, op))
            else:
                failed += 1
                target = op.target_section or op.target_clause_number or op.target_node_id or "?"
                log_operation_result(success + failed, "INSERT", False, f"Target '{target}' not found")
    
    # Sort by index DESCENDING (highest first) for structure-aware inserts
    # Legacy inserts (None) go last
    resolved_inserts.sort(
        key=lambda x: x[0].paragraph_index if x[0] else -1,
        reverse=True
    )
    
    # Track clause numbers for auto-increment when multiple INSERTs target same area
    # Key: para_index, Value: last used clause number
    insert_clause_counter: dict = {}
    
    # --- Execute inserts ---
    for resolved, original_op in resolved_inserts:
        if resolved is None:
            # Legacy INSERT_CLAUSE using text search
            if style and style.insert_method == "insert_clause_with_heading":
                result = editor.insert_clause_with_heading(
                    original_op.target_text or "",
                    original_op.new_content or "",
                    ""
                )
            else:
                # Parse title and body from new_content if possible
                content = original_op.new_content or ""
                result = editor.insert_paragraph_after(
                    original_op.target_text or "",
                    content
                )
            if result:
                success += 1
                log_operation_result(success + failed, "INSERT_CLAUSE", True, content[:30])
            else:
                failed += 1
                log_operation_result(success + failed, "INSERT_CLAUSE", False, f"Anchor not found")
        else:
            # Structure-aware insert using StyleAwareEditor for style matching
            content = resolved.content or ""
            
            # Parse title and body from content (format: "Title. Body text...")
            title, body = parse_title_body(content)
            
            # If manual numbering is used, we might want to keep the number if AI provided it
            # Otherwise strip it
            if not (structure.has_manual_numbering and not structure.has_word_numbering):
                title = strip_leading_number(title) if title else title
            
            # Build context for sub-clauses
            context = {}
            num_style, num_val = style_editor.detect_numbering_style(resolved.paragraph_index)
            if num_style == 'manual' and num_val:
                context['use_sub_clauses'] = True
                context['sub_number'] = 1
                context['parent_number'] = num_val.rstrip('.')
            
            # SIMPLIFIED INSERT: AI specifies role, we map to format_label
            # Get role from resolved operation (parsed from AI's new_role or format field)
            format_label = None
            if resolved.new_role:
                # Map role to format label
                role_to_format = {
                    NodeRole.SECTION_HEAD: 'BOLD',
                    NodeRole.CLAUSE: 'NUMBERED',
                    NodeRole.SUB_CLAUSE: 'NUMBERED',
                    NodeRole.LIST_ITEM: 'BULLET',
                    NodeRole.BODY: 'PLAIN',
                    NodeRole.ARTICLE: 'BOLD',
                }
                format_label = role_to_format.get(resolved.new_role, 'PLAIN')
                logger.info(f"  Using AI-specified role {resolved.new_role} -> format={format_label}")
            
            # AUTO-DETECT: If AI didn't provide format, detect from target paragraph
            if not format_label:
                # Check if target paragraph has Word numbering (numPr)
                W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                paragraphs = style_editor.body.findall(f'{W}p')
                if 0 <= resolved.paragraph_index < len(paragraphs):
                    # First try target paragraph
                    target = paragraphs[resolved.paragraph_index]
                    target_pPr = target.find(f'{W}pPr')
                    if target_pPr is not None:
                        target_numPr = target_pPr.find(f'{W}numPr')
                        if target_numPr is not None:
                            format_label = "NUMBERED"
                            logger.info(f"  Auto-detected format=NUMBERED from target p{resolved.paragraph_index}")
                    
                    # If target has no numPr, check PREVIOUS paragraphs (body paragraphs don't have numPr)
                    if not format_label:
                        for prev_idx in range(resolved.paragraph_index - 1, max(0, resolved.paragraph_index - 5), -1):
                            prev_para = paragraphs[prev_idx]
                            prev_pPr = prev_para.find(f'{W}pPr')
                            if prev_pPr is not None:
                                prev_numPr = prev_pPr.find(f'{W}numPr')
                                if prev_numPr is not None:
                                    format_label = "NUMBERED"
                                    logger.info(f"  Auto-detected format=NUMBERED from nearby p{prev_idx}")
                                    break
                
                # Default to PLAIN if still no format
                if not format_label:
                    format_label = "PLAIN"
            
            if resolved.insert_after:
                result = style_editor.insert_with_format(
                    resolved.paragraph_index,
                    format_label,
                    content
                )
                logger.info(f"  INSERT AFTER with format={format_label}: {content[:40]}...")
            else:
                # For BEFORE inserts, also use format_label (same formatting as AFTER)
                result = style_editor.insert_with_format_before(
                    resolved.paragraph_index,
                    format_label,
                    content
                )
                logger.info(f"  INSERT BEFORE with format={format_label}: {content[:40]}...")
            
            if result:
                success += 1
                log_operation_result(success + failed, "INSERT", True, title[:30] if title else content[:30])
            else:
                failed += 1
                log_operation_result(success + failed, "INSERT", False, f"Index {resolved.paragraph_index} failed")
    
    # --- Resolve and execute deletes in reverse order ---
    resolved_deletes = []
    for op in deletes:
        resolved = resolver.resolve(op)
        if resolved:
            resolved_deletes.append((resolved, op))
        else:
            failed += 1
            target = op.target_section or op.target_clause_number or op.target_node_id or "?"
            log_operation_result(success + failed, "DELETE", False, f"Target '{target}' not found")
    
    # Sort by index descending
    resolved_deletes.sort(key=lambda x: x[0].paragraph_index, reverse=True)
    
    for resolved, original_op in resolved_deletes:
        result = style_editor.delete_paragraph_by_index(resolved.paragraph_index)
        if result:
            success += 1
            log_operation_result(success + failed, "DELETE", True, resolved.target_description[:30])
        else:
            failed += 1
            log_operation_result(success + failed, "DELETE", False, f"Index {resolved.paragraph_index} failed")
    
    return success, failed


# =============================================================================
# AI ANALYSIS
# =============================================================================

async def analyze_document_with_structure(
    doc_content: Dict[str, Any],
    structure: StructureMap,
    playbook: Any,
    job: Job,
    style: DocumentStyle = None
) -> List[StructureOperation]:
    """
    Analyze document with AI using structure-aware prompts.
    """
    logger.info(f"Analyzing document with {job.ai_provider} ({job.ai_model})")
    
    # Build playbook text
    playbook_text = ""
    if hasattr(playbook, "playbookText") and playbook.playbookText:
        playbook_text = playbook.playbookText
    elif hasattr(playbook, "rules") and playbook.rules:
        playbook_text = "\n".join([f"- IF {r.condition} THEN {r.action}" for r in playbook.rules])
    elif isinstance(playbook, dict):
        playbook_text = playbook.get("playbookText", "") or str(playbook)
    
    document_text = doc_content.get("text", "")
    
    # Build style additions
    style_instructions = ""
    if style:
        style_instructions = get_ai_prompt_additions(style)
    
    # Build structure prompt
    structure_info = generate_structure_prompt(structure)
    
    # Full system prompt
    system_prompt = f"""{BASE_PROMPT}

{STRUCTURE_AWARE_RULES}

{style_instructions}

{structure_info}

PLAYBOOK:
{playbook_text}"""

    prompt = f"DOCUMENT:\n{document_text}"
    
    # Log the FULL request (not shortened)
    log_separator("FULL AI REQUEST")
    logger.info(f"System Prompt Length: {len(system_prompt)} chars")
    logger.info(f"User Prompt Length: {len(prompt)} chars")
    logger.info("--- SYSTEM PROMPT START ---")
    logger.info(system_prompt)
    logger.info("--- SYSTEM PROMPT END ---")
    logger.info("--- USER PROMPT START ---")
    logger.info(prompt)
    logger.info("--- USER PROMPT END ---")
    log_ai_request(system_prompt + "\n\n" + prompt, job.ai_model)
    
    # Call AI
    try:
        if job.ai_provider.lower() == "gemini" or "gemini" in job.ai_model.lower():
            if not job.api_key:
                logger.error("No API Key provided for Gemini")
                return []
            
            response_text = await call_gemini(
                prompt=prompt,
                model=job.ai_model,
                api_key=job.api_key,
                system_message=system_prompt
            )
        else:
            logger.warning(f"Provider {job.ai_provider} not fully integrated, returning empty.")
            return []
        
        # Parse response
        operations = parse_ai_response(response_text)
        
        # Log FULL AI response (not shortened)
        log_separator("FULL AI RESPONSE")
        logger.info(f"Raw response: {len(response_text)} chars")
        logger.info("--- RAW RESPONSE START ---")
        logger.info(response_text)
        logger.info("--- RAW RESPONSE END ---")
        
        # Log EACH operation in full detail separately
        log_separator("PARSED OPERATIONS (FULL DETAIL)")
        for i, op in enumerate(operations):
            logger.info(f"\n=== OPERATION {i+1} ===")
            logger.info(f"  Type: {op.type}")
            logger.info(f"  target_clause_number: {op.target_clause_number}")
            logger.info(f"  target_section: {op.target_section}")
            logger.info(f"  target_node_id: {op.target_node_id}")
            logger.info(f"  position: {op.position}")
            logger.info(f"  format: {op.format}")
            logger.info(f"  new_content: {op.new_content}")
            logger.info(f"  old_text: {op.old_text}")
            logger.info(f"  new_text: {op.new_text}")
            logger.info(f"  reason: {op.reason}")
        logger.info(f"\n=== Total: {len(operations)} operations ===")
        
        # Log
        log_ai_response(response_text, [{"type": op.type, "reason": op.reason} for op in operations])
        
        logger.info(f"AI returned {len(operations)} operations")
        return operations
    
    except Exception as e:
        logger.error(f"AI Analysis failed: {e}")
        job.errors.append(f"AI Analysis failed: {str(e)}")
        return []


# =============================================================================
# DOCUMENT SAVING
# =============================================================================

def save_document(style_editor: StyleAwareEditor, job_id: str, final_bytes: bytes = None) -> str:
    """Save the document using StyleAwareEditor or provided bytes.
    
    Args:
        style_editor: The editor with changes applied
        job_id: Job ID for filename
        final_bytes: If provided, write these bytes instead of editor output
    """
    processed_dir = os.path.join(os.getcwd(), "ProcessedDocs")
    os.makedirs(processed_dir, exist_ok=True)
    result_path = os.path.join(processed_dir, f"redlined_{job_id}.docx")
    
    if final_bytes:
        with open(result_path, 'wb') as f:
            f.write(final_bytes)
    else:
        style_editor.save_to_file(result_path)
    return result_path


# =============================================================================
# MAIN PROCESSING LOOP
# =============================================================================

async def process_job(job: Job, jobs_store: Dict[str, Job], playbooks_store: Dict[str, Any] = None):
    """
    Main job processing loop with structure-aware operations.
    
    Flow:
    1. Load document
    2. Detect structure using StructureDetector
    3. Build AI prompt with structure info
    4. Call AI
    5. Parse operations into StructureOperations
    6. Resolve operations using OperationResolver
    7. Sort: AMENDs first, then INSERTs/DELETEs in reverse index order
    8. Execute operations
    9. Save
    """
    try:
        log_separator("STARTING JOB")
        logger.info(f"Job ID: {job.id}")
        logger.info(f"Document: {job.original_file_path}")
        
        # Helper to update job status
        def update_job(phase: str, progress: int, status: JobStatus = JobStatus.PROCESSING):
            job.current_phase = phase
            job.progress = progress
            job.status = status
            logger.info(f"Job {job.id}: {phase} ({progress}%)")

        # Phase 1: Initializing
        update_job(phase="Initializing", progress=5)
        await asyncio.sleep(0.5)
        
        # Load playbook
        if playbooks_store and job.playbook_id in playbooks_store:
            playbook = playbooks_store[job.playbook_id]
            logger.info(f"Loaded playbook: {playbook.name}")
        else:
            playbook = {"id": job.playbook_id, "playbookText": "Review this document."}
            logger.warning(f"Playbook {job.playbook_id} not found, using default.")
        
        # Phase 2: Load Document & Detect Structure
        update_job(phase="Detecting Structure", progress=10)
        editor = DocumentEditor(job.original_file_path)
        
        # Create StyleAwareEditor for style-matching inserts
        with open(job.original_file_path, 'rb') as f:
            doc_bytes = f.read()
            style_editor = StyleAwareEditor(doc_bytes)
            
        # Extract original format map for Styler AI (NEW!)
        # Captures formatting BEFORE any changes
        original_format_map = extract_format_map(doc_bytes, check_vibelegal=False)
        logger.info(f"Extracted original format map: {len(original_format_map)} paragraphs")
        
        # Detect document structure (NEW!)
        log_separator("STRUCTURE DETECTION")
        structure = detect_structure_from_docx(job.original_file_path)
        
        logger.info(f"Structure type: {structure.structure_type}")
        logger.info(f"Sections found: {structure.has_sections}")
        logger.info(f"Numbering: manual={structure.has_manual_numbering}, word={structure.has_word_numbering}")
        logger.info(f"Heading styles: {structure.has_heading_styles} (name={structure.heading_style_name})")
        logger.info(f"Bullet sections: {structure.has_bullet_sections}")
        logger.info(f"Max depth: {structure.max_depth}")
        logger.info(f"Total nodes: {len(structure.nodes)}")
        
        # Log first 10 nodes
        for node in structure.nodes[:10]:
            logger.info(f"  [{node.id}] {node.role.value}: {node.text_preview}")
        
        # Phase 3: Detect Style
        log_separator("STYLE DETECTION")
        style = detect_document_style(job.original_file_path)
        
        # Override insert_method if structure indicates bullet sections (Style Detector Fix)
        if structure.has_bullet_sections:
            logger.info("  Overriding insert_method: insert_section_heading (bullet sections detected)")
            style = style._replace(insert_method="insert_section_heading")
        
        # Phase 4: Extract Content
        update_job(phase="Extracting Content", progress=20)
        doc_content = extract_document(editor)
        
        # Phase 5: Analyze with AI (with structure)
        update_job(phase="Analyzing with AI", progress=30)
        operations = await analyze_document_with_structure(doc_content, structure, playbook, job, style)
        job.operations_total = len(operations)
        
        # Phase 6-7: Apply Operations (structured order with style awareness)
        log_separator("APPLYING OPERATIONS")
        update_job(phase="Applying Redlines", progress=60)
        
        success_count, failed_count = apply_operations_in_order(
            operations, editor, style_editor, structure, style
        )
        
        job.operations_complete = success_count + failed_count
        
        # Phase 7.5: Run Styler (post-processing)
        log_separator("STYLER")
        update_job(phase="Styling", progress=80)
        
        redlined_bytes = style_editor.save()
        
        # Step 1: Deterministic numbering fixes (keep existing Styler for this)
        log_separator("STYLER - NUMBERING FIXES")
        styler = Styler(redlined_bytes)
        numbering_fixes = styler.fix_double_numbering()
        numbering_fixes.extend(styler.fix_manual_numbering())
        for fix in numbering_fixes:
            logger.info(f"Numbering fix: {fix}")
        redlined_bytes = styler.save()
        
        # Step 2: Extract current format map (after redlining)
        current_format_map = extract_format_map(redlined_bytes, check_vibelegal=True)
        logger.info(f"Extracted current format map: {len(current_format_map)} paragraphs")
        
        # Step 3: Styler AI - compare formats and get fixes
        log_separator("STYLER - AI FORMATTING")
        ai_fixes = await run_styler_ai(
            original_format_map,
            current_format_map,
            api_key=job.api_key,
            model="gemini-2.0-flash"
        )
        
        # Step 4: Apply formatting fixes
        final_bytes = apply_styler_fixes(redlined_bytes, ai_fixes)
        
        # Phase 8: Save result
        update_job(phase="Saving Result", progress=90)
        result_path = save_document(style_editor, job.id, final_bytes)
        job.result_file_path = result_path
        
        # Log summary
        log_processing_summary(success_count, failed_count, result_path)
        
        # Phase 9: Complete
        update_job(phase="Complete", progress=100, status=JobStatus.COMPLETE)
        logger.info(f"Job {job.id} completed successfully")
    
    except Exception as e:
        logger.error(f"Job {job.id} failed: {e}", exc_info=True)
        job.errors.append(str(e))
        job.status = JobStatus.ERROR
        job.current_phase = "Failed"
