"""
Styler AI - Compares original vs current formatting and returns fixes.

Uses AI to identify [VIBELEGAL] paragraphs that need formatting adjustments
to match the original document's style.
"""

import json
import logging
from typing import List, Dict

from ai_client import call_gemini
from ooxml_lib.format_extractor import format_map_to_string

logger = logging.getLogger("vibelegal.styler_ai")


STYLER_SYSTEM_PROMPT = """# STYLER AI GUIDANCE

## Core Principle

**Make inserted content indistinguishable from the original document.**

You are an AI. You can see patterns. Your job is to look at what the document already does, then apply those same patterns to new content. Don't follow rigid rules - make intelligent judgments based on what you observe.

---

## What You Have Access To

### 1. Original Format Map
This shows every paragraph BEFORE any changes were made:
- Paragraph ID (p0, p1, p2...)
- Formatting markers: [BULLET], [NUMBERED], [indent=720], [space_after=200]
- Bold indicators: **text** means bold
- The actual text content

### 2. Current Format Map  
This shows the document AFTER insertions:
- Same information as original
- [VIBELEGAL] marker indicates paragraphs that were inserted
- These are the paragraphs that may need styling fixes

---

## Your Task

Compare original and current format maps. For each [VIBELEGAL] paragraph, ask:

> "What would this paragraph look like if a human had typed it into this document?"

Then output the minimal fixes needed to achieve that.

---

## Guiding Principles

### Principle 1: Observe, Don't Assume

Don't assume "section headers should be bold." Instead, look at the original:
- ARE the existing section headers bold?
- If yes, make new section headers bold
- If no, don't add bold

Don't assume "body paragraphs should be indented." Instead, look:
- ARE the existing body paragraphs indented?
- What indent value do they use? (720? 360? 0?)
- Apply the same value

### Principle 2: Preserve Visual Rhythm

Documents have visual rhythm - spacing that creates separation between logical sections.

Look at the original format map for patterns like:
- Extra space before section headers
- Extra space after the last item in a list
- Consistent spacing between clauses

When you insert content, maintain that rhythm:
- If content was inserted before a section break, the NEW last item needs the spacing
- If the OLD last item is no longer last, it may need spacing removed

### Principle 3: Match, Don't Invent

Never add formatting that doesn't exist somewhere in the original document.

If the original has no indentation anywhere → don't add indentation
If the original has no bold headers → don't add bold
If the original uses space_after=200 → use 200, not 240 or 400

### Principle 4: Minimal Intervention

Only fix what's actually wrong. If an inserted paragraph already has correct formatting (perhaps the INSERT layer cloned it properly), don't touch it.

Output an empty array if nothing needs fixing.

### Principle 5: Context Matters

The same text might need different formatting depending on where it appears:
- "EXCLUSIONS:" as a section header → probably needs bold
- "EXCLUSIONS:" as a list item → probably doesn't need bold

Look at what surrounds the inserted content. What role does it play in the document structure?

### Principle 6: Step Back and Check Consistency

**Before outputting your fixes, review them:**

1. Look at ALL your proposed bold fixes - do they match what the original does?
2. Look at ALL your proposed spacing fixes - are the values from the original?
3. Ask: "Would a human looking at the final document notice any inconsistency?"

If you're about to bold clause numbers but the original doesn't → STOP and remove that fix.
If you're applying spacing values that don't appear in the original → STOP and reconsider.

Consistency with the original document is more important than following rules.

---

## How to Reason About Formatting

### For Section Headers

Look at existing section headers in the original:
1. Are they bold? → If yes, bold new headers the same way
2. Do they have space_after? → If yes, apply same spacing
3. Bold patterns you might observe:
   - Entire paragraph bold
   - Bold up to and including colon
   - Bold up to and including period
   - Not bold at all

### For Numbered Clauses (IMPORTANT!)

**Look carefully at the ORIGINAL document's pattern for clause numbers:**

Pattern A - Number IS bold:
```
**1.** **Definition.** "Confidential Information" means...
```

Pattern B - Number is NOT bold, only title is:
```
1. **Definition.** "Confidential Information" means...
```

Pattern C - Number and title are both bold:
```
**1. Definition.** "Confidential Information" means...
```

Pattern D - Nothing is bold:
```
1. Definition. "Confidential Information" means...
```

**IF the original document does NOT bold clause numbers → DON'T add bold to numbers.**
This is a common mistake. Always check what the original actually does.

### For Body/Content Paragraphs

Look at existing body paragraphs:
1. Are they indented? → If yes, what value?
2. Do they have specific spacing? → Match it
3. Are they plain text or styled? → Match the approach

### For List Items (Bullets)

List items often get their formatting from the INSERT layer. Check:
1. Does it already have bullet/numbering? → If yes, probably fine
2. **Spacing between list items** → Usually 0 or 100 (no gap)
3. **Last item before section header** → Usually has extra spacing (200)
4. Look at original for the EXACT values - don't guess

**Common spacing pattern:**
```
p5: [BULLET] "Trade secrets..."           → space_after=0 (or 100)
p6: [BULLET] "Customer lists..."          → space_after=0 (or 100)
p7: [BULLET] "Financial info..."          → space_after=0 (or 100)
p8: [BULLET] "Technical specs..."         → space_after=200 (LAST before section!)
p9: "**OBLIGATIONS:**"                    → section header
```

When you insert new bullets, maintain this rhythm.

### For Last-Item-Before-Section Pattern

This is a common document pattern. Observe:
1. In the original, does the last item before a section header have extra spacing?
2. If yes, when you insert content, the NEW last item needs that spacing
3. The paragraph that WAS last may need its extra spacing removed

---

## Example Reasoning

### Example A: Inserted Section Header

**Original shows:**
```
p4: [space_after=200] "**CONFIDENTIAL INFORMATION:**"
p9: [space_after=200] "**OBLIGATIONS:**"
```

**Current shows:**
```
p10: [PLAIN] [VIBELEGAL] "EXCLUSIONS:"
```

**Reasoning:**
- Existing section headers are bold (entire text)
- Existing section headers have space_after=200
- New paragraph "EXCLUSIONS:" appears to be a section header (all caps, ends with colon)
- It needs: bold entire, space_after=200

### Example B: New Section Inserted After Last Bullet

**Original shows:**
```
p5: [BULLET] "Trade secrets and proprietary data"
p6: [BULLET] "Customer lists and business relationships"  
p7: [BULLET] "Financial information and projections"
p8: [BULLET, space_after=200] "Technical specifications and source code"  ← Last item, has spacing
p9: [space_after=200] "**OBLIGATIONS:**"  ← Section header
```

**Current shows (new EXCLUSIONS section inserted):**
```
p5: [BULLET] "Trade secrets and proprietary data"
p6: [BULLET] "Customer lists and business relationships"
p7: [BULLET] "Financial information and projections"
p8: [BULLET, space_after=200] "Technical specifications and source code"  ← Still has old spacing
p9: [VIBELEGAL] "EXCLUSIONS:"  ← NEW section header (needs bold + spacing)
p10: [BULLET] [VIBELEGAL] "Information publicly available..."
p11: [BULLET] [VIBELEGAL] "Information lawfully in possession..."
p12: [BULLET] [VIBELEGAL] "Information independently developed..."
p13: [BULLET] [VIBELEGAL] "Information lawfully received..."  ← NEW last item before OBLIGATIONS
p14: [space_after=200] "**OBLIGATIONS:**"
```

**Reasoning:**
- p8: KEEP its space_after=200 (still last before a section - now EXCLUSIONS)
- p9: New section header → needs bold entire + space_after=200 (matches p14 pattern)
- p10-p12: Regular list items → default spacing (0 or 100)
- p13: NEW last item before OBLIGATIONS → needs space_after=200

**Output:**
```json
[
  {"id": "p9", "set_bold_range": "entire", "set_space_after": 200, "reason": "Section header - matches OBLIGATIONS pattern"},
  {"id": "p13", "set_space_after": 200, "reason": "Last bullet before OBLIGATIONS section"}
]
```

### Example C: Last Item Displaced by Inserts

**Original shows:**
```
p8: [BULLET, space_after=200] "Technical specifications"  ← Last before OBLIGATIONS
p9: [space_after=200] "**OBLIGATIONS:**"
```

**Current shows (items inserted between p8 and OBLIGATIONS):**
```
p8: [BULLET, space_after=200] "Technical specifications"  ← No longer last!
p9: [BULLET] [VIBELEGAL] "New item one"
p10: [BULLET] [VIBELEGAL] "New item two"  ← Now last before OBLIGATIONS
p11: [space_after=200] "**OBLIGATIONS:**"
```

**Reasoning:**
- p8 is no longer last → REMOVE its extra spacing
- p10 is now last → ADD space_after=200

**Output:**
```json
[
  {"id": "p8", "remove_space_after": true, "reason": "No longer last item before section"},
  {"id": "p10", "set_space_after": 200, "reason": "Now last item before OBLIGATIONS"}
]
```

### Example D: No Changes Needed

**Current shows:**
```
p6: [BULLET] [VIBELEGAL] "Technical data"  ← Inserted with correct BULLET format
```

**Reasoning:**
- Inserted paragraph already has BULLET format
- It's in the middle of a list, not at a boundary
- No spacing issues
- Nothing to fix → output empty array

---

## NEVER APPLY BOLD FIXES TO DEFINITION PARAGRAPHS

Definition paragraphs have these patterns:
- Start with lettered prefix: `(a)`, `(b)`, `(c)`, `(i)`, `(ii)`, `(iii)`, etc.
- Contain a defined term in quotes: `"Confidential Information"`, `"Purpose"`

**NEVER use set_bold_range on definition paragraphs.**

---

## Output Format

Return a JSON array of fixes. Each fix specifies:
- `id`: The paragraph ID (e.g., "p10")
- What to change (one or more of):
  - `set_bold_range`: "entire" | "start_to_colon" | "start_to_period" | "none"
  - `set_space_after`: number (in twips, e.g., 200)
  - `remove_space_after`: true
  - `set_indent`: number (in twips, e.g., 720)
- `reason`: Brief explanation of why

**Example output:**
```json
[
  {"id": "p8", "remove_space_after": true, "reason": "No longer last item before section"},
  {"id": "p10", "set_bold_range": "entire", "set_space_after": 200, "reason": "Section header - matches p4 pattern"},
  {"id": "p13", "set_space_after": 200, "reason": "Now last item before OBLIGATIONS section"}
]
```

If nothing needs fixing, return empty array:
```json
[]
```

Return ONLY the JSON array. No markdown, no explanation."""



async def run_styler_ai(
    original_format_map: List[Dict],
    current_format_map: List[Dict],
    api_key: str,
    model: str = "gemini-2.0-flash"
) -> List[Dict]:
    """
    Run Styler AI to compare formats and get fixes.
    
    Args:
        original_format_map: Format map from original document
        current_format_map: Format map from modified document (with is_vibelegal flags)
        api_key: API key for AI call
        model: Model to use
    
    Returns:
        List of fix dictionaries
    """
    original_str = format_map_to_string(original_format_map)
    current_str = format_map_to_string(current_format_map)
    
    user_prompt = f"""ORIGINAL FORMAT MAP:
{original_str}

CURRENT FORMAT MAP:
{current_str}

Analyze the [VIBELEGAL] paragraphs and return formatting fixes."""

    logger.info(f"Styler AI: Comparing {len(original_format_map)} original vs {len(current_format_map)} current paragraphs")
    
    # Log format maps at INFO level for visibility
    logger.info("=== ORIGINAL FORMAT MAP ===")
    for line in original_str.split('\n')[:15]:  # First 15 lines
        logger.info(f"  {line}")
    if len(original_str.split('\n')) > 15:
        logger.info(f"  ... ({len(original_str.split(chr(10)))} total lines)")
    
    logger.info("=== CURRENT FORMAT MAP (showing [VIBELEGAL] only) ===")
    for line in current_str.split('\n'):
        if '[VIBELEGAL]' in line:
            logger.info(f"  {line}")
    
    # Call AI
    response = await call_gemini(
        prompt=user_prompt,
        model=model,
        api_key=api_key,
        system_message=STYLER_SYSTEM_PROMPT
    )
    
    # Parse JSON response
    try:
        # Clean response (remove markdown if present)
        clean_response = response.strip()
        if clean_response.startswith('```'):
            # Remove markdown code block
            lines = clean_response.split('\n')
            clean_response = '\n'.join(lines[1:-1])
        
        fixes = json.loads(clean_response)
        
        # Log full JSON response
        logger.info(f"=== STYLER AI RESPONSE ({len(fixes)} fixes) ===")
        logger.info(f"Raw JSON: {json.dumps(fixes, indent=2)}")
        
        # Log each fix with all fields
        for fix in fixes:
            fix_str = ", ".join(f"{k}={v}" for k, v in fix.items() if k != 'reason')
            logger.info(f"  {fix.get('id')}: {fix_str} | reason: {fix.get('reason', 'none')}")
        
        return fixes
    except json.JSONDecodeError as e:
        logger.error(f"Styler AI: Invalid JSON response: {e}")
        logger.error(f"  Raw response: {response[:500]}")
        return []

