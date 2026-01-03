from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from enum import Enum

class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    ERROR = "error"

class Job(BaseModel):
    id: str
    status: JobStatus
    progress: int = 0
    current_phase: str = "Queued"
    operations_complete: int = 0
    operations_total: int = 0
    errors: List[str] = []
    warnings: List[str] = []
    
    # Internal fields (not exposed in status response by default, but useful for processing)
    original_file_path: Optional[str] = None
    original_filename: Optional[str] = None
    result_file_path: Optional[str] = None
    playbook_id: str = ""
    ai_provider: str = ""
    ai_model: str = ""
    api_key: Optional[str] = None
    created_at: Optional[datetime] = None

class PlaybookRule(BaseModel):
    condition: str
    action: str

class Playbook(BaseModel):
    id: str
    name: str
    description: str = ""
    playbookText: str = ""
    rules: List[PlaybookRule] = []

class OperationType(str, Enum):
    AMEND = "AMEND"
    DELETE = "DELETE"
    INSERT_CLAUSE = "INSERT_CLAUSE"  # Legacy: text-search based
    INSERT = "INSERT"                 # New: structure-aware
    INSERT_WITH_CHILDREN = "INSERT_WITH_CHILDREN"  # New: hierarchical

class AiOperation(BaseModel):
    type: OperationType
    clause_title: Optional[str] = None
    original_text: Optional[str] = None      # For AMEND
    replacement_text: Optional[str] = None   # For AMEND
    after_clause: Optional[str] = None       # For INSERT_CLAUSE (legacy)
    new_clause_title: Optional[str] = None   # For INSERT_CLAUSE (legacy)
    new_clause_body: Optional[str] = None    # For INSERT_CLAUSE (legacy)
    reason: str = ""


class StructureAwareOperation(BaseModel):
    """
    Structure-aware operation that references targets by section/clause/node_id
    instead of text search. This avoids "anchor not found" errors.
    """
    type: str  # AMEND, INSERT, INSERT_WITH_CHILDREN, DELETE
    
    # Target identification (use ONE of these)
    target_section: Optional[str] = None       # Section title: "BETWEEN", "OBLIGATIONS"
    target_clause_number: Optional[str] = None # Clause number: "1", "1.1", "2"
    target_node_id: Optional[str] = None       # Paragraph ID: "p0", "p3"
    target_text: Optional[str] = None          # Fallback text search
    
    # For INSERT operations
    position: str = "AFTER"                    # AFTER, BEFORE, AFTER_SECTION, LAST_CHILD
    new_role: Optional[str] = None             # SECTION_HEAD, CLAUSE, LIST_ITEM, etc.
    new_content: Optional[str] = None
    
    # For INSERT_WITH_CHILDREN (hierarchical inserts)
    content_tree: Optional[dict] = None        # {"role": "...", "text": "...", "children": [...]}
    
    # For AMEND
    old_text: Optional[str] = None
    new_text: Optional[str] = None
    
    # Metadata
    reason: str = ""

