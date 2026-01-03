"""
Logging utilities for VibeLegal server.
Shows what AI returns and what the server does.
"""

import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger('vibelegal')


def setup_logging(level=logging.INFO):
    """Configure logging for the server."""
    logging.basicConfig(
        level=level,
        format='%(asctime)s | %(levelname)s | %(message)s',
        datefmt='%H:%M:%S'
    )


def log_separator(title: str, char: str = "="):
    """Print a visual separator."""
    logger.info(f"\n{char*60}")
    logger.info(f" {title}")
    logger.info(f"{char*60}")


def log_ai_request(prompt: str, model: str):
    """Log what we're sending to AI."""
    log_separator("AI REQUEST")
    logger.info(f"Model: {model}")
    logger.info(f"Prompt length: {len(prompt)} chars")
    
    # Show key parts of prompt
    if "NUMBERING:" in prompt:
        for line in prompt.split("\n"):
            if "NUMBERING:" in line or "CRITICAL AMEND" in line:
                logger.info(f"  {line[:70]}...")
                break


def log_ai_response(raw_response: str, operations: List[Dict]):
    """Log what AI returned."""
    log_separator("AI RESPONSE")
    logger.info(f"Raw response: {len(raw_response)} chars")
    logger.info(f"Parsed operations: {len(operations)}")
    
    for i, op in enumerate(operations):
        op_type = op.get('type', '?')
        
        if op_type == 'AMEND':
            orig = op.get('original_text', '')[:40]
            repl = op.get('replacement_text', '')[:40]
            logger.info(f"  [{i+1}] AMEND")
            logger.info(f"       Find: '{orig}...'")
            logger.info(f"       Replace: '{repl}...'")
            
        elif op_type == 'DELETE':
            orig = op.get('original_text', '')[:40]
            logger.info(f"  [{i+1}] DELETE: '{orig}...'")
            
        elif op_type == 'INSERT_CLAUSE':
            after = op.get('after_clause', '')[:30]
            title = op.get('new_clause_title', '')
            logger.info(f"  [{i+1}] INSERT after '{after}...'")
            logger.info(f"       Title: '{title}'")


def log_operation_result(index: int, op_type: str, success: bool, detail: str = ""):
    """Log result of applying one operation."""
    status = "✓" if success else "✗"
    logger.info(f"  {status} [{index}] {op_type}: {detail[:50]}")


def log_processing_summary(success: int, failed: int, output_path: str):
    """Log final summary."""
    log_separator("COMPLETE")
    logger.info(f"Success: {success}, Failed: {failed}")
    logger.info(f"Output: {output_path}")
