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
from ooxml_lib import DocumentEditor, StyleAwareEditor
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
## OPERATION SELECTION RULES - CRITICAL!

### Rule 1: PREFER AMEND OVER INSERT
When adding content to an existing clause, use AMEND to append - don't INSERT a new paragraph.

WRONG - Adding exclusions as separate clause:
  INSERT "EXCLUSIONS: Confidential Information shall not include..."
  Creates floating paragraph outside definition list

CORRECT - AMEND the definition to include exclusions:
  AMEND old: "marked as confidential."
  AMEND new: "marked as confidential. Exclusions: CI shall not include (a) public info..."
  Keeps exclusions as part of the definition

### Rule 2: MINIMAL REDLINING
Only redline what actually changes. Don't delete and re-insert unchanged text.

WRONG - Over-redlines:
  old: "the Receiving Party shall be liable for any breach by its Representatives."
  new: "the Receiving Party shall be liable for any breach by its Representatives. May also disclose..."
  Strikes through and re-underlines unchanged text unnecessarily

CORRECT - Minimal:
  old: "by its Representatives."
  new: "by its Representatives. The Receiving Party may also disclose..."
  Only adds the new sentence

### Rule 3: REMOVING CLAUSES - Use [RESERVED]
When removing a clause, change heading to [RESERVED] and DELETE body only.

WRONG:
  DELETE heading "NON-SOLICITATION"
  DELETE body
  INSERT "[Intentionally deleted.]"
  Removes clause number, creates floating text

CORRECT:
  AMEND heading: "NON-SOLICITATION" to "[RESERVED]"
  DELETE body paragraph only
  Clause number remains, strikethrough IS the redline

### Rule 4: AMEND EXISTING CLAUSES, DON'T INSERT REPLACEMENTS
When playbook says to change text (like "AMEND if unlimited"), find and amend existing text.

WRONG - Inserting new liability clause:
  INSERT "Limitation of Liability. Total liability shall not exceed 50000..."
  Creates new floating clause when one already exists

CORRECT - AMEND existing clause:
  AMEND old: "liability under this Agreement shall be unlimited"
  new: "total liability under this Agreement shall not exceed 50000"
  Modifies existing clause in place

### Decision Tree
1. Does similar text already exist? AMEND it, don't INSERT new
2. Adding to end of existing clause? AMEND with minimal old text (just the ending)
3. Removing a clause? AMEND heading to [RESERVED], DELETE body
4. Adding truly NEW clause that has no similar existing text? INSERT

---

## TARGETING RULES

### How to Reference Targets
1. target_clause_number - for numbered clauses: "1", "2", "1.1"
2. target_section - for section headings: "BETWEEN", "OBLIGATIONS"
3. target_node_id - for specific paragraphs: "p3"

### Position Values (for INSERT only)
- "AFTER_SECTION" - after ALL children/body (USE THIS for new clauses!)
- "AFTER" - immediately after target paragraph only
- "LAST_CHILD" - as last item inside a section

### Don't Include Numbers in new_content
System auto-numbers. Do NOT include clause numbers:
WRONG: "5. Return of Information..."
RIGHT: "Return of Information..."

---

## OPERATION FORMATS

**AMEND** (change text - PREFERRED!):
{
  "type": "AMEND",
  "target_clause_number": "1",
  "old_text": "just the ending text.",
  "new_text": "just the ending text. Plus new appended content here.",
  "reason": "Adding exclusions to definition"
}

**AMEND for heading change** (clause removal):
{
  "type": "AMEND",
  "target_clause_number": "6",
  "old_text": "NON-SOLICITATION",
  "new_text": "[RESERVED]",
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
    
    # Parse role
    new_role = None
    if op_dict.get('new_role'):
        try:
            new_role = NodeRole[op_dict['new_role']]
        except KeyError:
            logger.warning(f"Unknown role '{op_dict.get('new_role')}'")
    
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
        old_text=op_dict.get('old_text') or op_dict.get('original_text'),
        new_text=op_dict.get('new_text') or op_dict.get('replacement_text'),
        reason=op_dict.get('reason', '')
    )


def parse_ai_response(response_text: str) -> List[StructureOperation]:
    """Parse AI response text into list of StructureOperations"""
    
    # Strip markdown code blocks
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0].strip()
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0].strip()
    
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
    inserts = [op for op in operations if op.type in ("INSERT", "INSERT_WITH_CHILDREN", "INSERT_CLAUSE")]
    
    success = 0
    failed = 0
    
    # --- Execute AMENDs first (order doesn't matter) ---
    for op in amends:
        resolved = resolver.resolve(op)
        if resolved and resolved.old_text and resolved.new_text:
            result = style_editor.amend_text(resolved.old_text, resolved.new_text)
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
        if op.type == "INSERT_WITH_CHILDREN" and op.content_tree:
            resolved_list = resolver.resolve_hierarchical(op)
            for r in resolved_list:
                resolved_inserts.append((r, op))
        elif op.type == "INSERT_CLAUSE":
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
            
            # Strip any leading numbers from title (AI sometimes includes "5. Title")
            title = strip_leading_number(title) if title else title
            
            # Build context for sub-clauses
            context = {}
            num_style, num_val = style_editor.detect_numbering_style(resolved.paragraph_index)
            if num_style == 'manual' and num_val:
                context['use_sub_clauses'] = True
                context['sub_number'] = 1
                context['parent_number'] = num_val.rstrip('.')
            
            # Detect document type and use appropriate INSERT method
            if resolved.insert_after:
                if title and body:
                    # Document type detection priority:
                    # 1. Bullet sections (section headings + bullet lists) → insert_section_heading
                    # 2. Manual numbering without Word numPr → insert_manual_numbered_clause
                    # 3. Word auto-numbered lists → insert_numbered_clause
                    # 4. Heading styles (Heading2, etc.) → insert_styled_clause
                    # 5. Fallback → smart_insert
                    
                    if structure.has_bullet_sections:
                        # Bullet-point documents with section headings (OBLIGATIONS:)
                        # Create uppercase heading with colon
                        heading = title.upper()
                        if not heading.endswith(':'):
                            heading += ':'
                        result = style_editor.insert_section_heading(
                            resolved.paragraph_index,
                            heading,
                            body,
                            spacing_after=False
                        )
                    elif structure.has_manual_numbering and not structure.has_word_numbering:
                        # Manual numbered docs (1., 2., 3.) - not Word auto-numbered
                        # Detect if original uses bold titles
                        bold_titles = _detect_bold_titles(style_editor, structure)
                        
                        # Calculate the next clause number with auto-increment
                        # Use counter to track multiple INSERTs
                        base_num = _calculate_next_manual_clause_number(
                            resolved.paragraph_index, structure
                        )
                        
                        # Check if we've already inserted at this position
                        # Use para_index as key - multiple inserts at same index increment
                        counter_key = resolved.paragraph_index
                        if counter_key in insert_clause_counter:
                            # Increment from last used number
                            clause_num = insert_clause_counter[counter_key] + 1
                        else:
                            # First insert at this position - use calculated base
                            clause_num = base_num
                        
                        # Update counter for next INSERT
                        insert_clause_counter[counter_key] = clause_num
                        logger.info(f"  Manual numbered INSERT: using clause #{clause_num}")
                        
                        result = style_editor.insert_manual_numbered_clause(
                            resolved.paragraph_index,
                            clause_num,
                            title,
                            body,
                            bold_title=bold_titles
                        )
                    elif structure.has_word_numbering:
                        # Word auto-numbered lists - copy numPr to join list
                        result = style_editor.insert_numbered_clause(
                            resolved.paragraph_index,
                            title,
                            body
                        )
                    elif structure.has_heading_styles:
                        # Documents with heading styles and manual numbers
                        heading_style = structure.heading_style_name or "Heading2"
                        result = style_editor.insert_styled_clause(
                            resolved.paragraph_index,
                            title,
                            body,
                            heading_style=heading_style
                        )
                    else:
                        # Fallback to smart_insert for other document types
                        result = style_editor.smart_insert(
                            resolved.paragraph_index,
                            title,
                            body,
                            context
                        )
                else:
                    # No clear title - insert as plain paragraph
                    result = style_editor.insert_plain_paragraph(
                        resolved.paragraph_index,
                        content
                    )
            else:
                # For BEFORE inserts, use plain paragraph (smart_insert only does after)
                result = style_editor.insert_paragraph_before_index(
                    resolved.paragraph_index,
                    content
                )
            
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
    
    # Log the request
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
        
        # Log detailed AI operations for debugging
        logger.info("=== AI Operations ===")
        for i, op in enumerate(operations):
            logger.info(f"  [{i+1}] {op.type} | target_clause={op.target_clause_number} | "
                       f"target_section={op.target_section} | target_node_id={op.target_node_id} | "
                       f"position={op.position} | reason={op.reason[:50] if op.reason else ''}...")
            # Additional logging for hierarchical inserts
            if op.type == "INSERT_WITH_CHILDREN":
                logger.info(f"      content_tree: {op.content_tree is not None}")
                if op.content_tree:
                    children_count = len(op.content_tree.get('children', [])) if isinstance(op.content_tree, dict) else 0
                    logger.info(f"      children count: {children_count}")
        logger.info("=== End AI Operations ===")
        
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

def save_document(style_editor: StyleAwareEditor, job_id: str) -> str:
    """Save the document using StyleAwareEditor (which has all the changes)."""
    processed_dir = os.path.join(os.getcwd(), "ProcessedDocs")
    os.makedirs(processed_dir, exist_ok=True)
    result_path = os.path.join(processed_dir, f"redlined_{job_id}.docx")
    
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
            style_editor = StyleAwareEditor(f.read())
        
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
        
        # Phase 8: Save result
        update_job(phase="Saving Result", progress=90)
        result_path = save_document(style_editor, job.id)
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
