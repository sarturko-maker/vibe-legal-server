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


STYLER_SYSTEM_PROMPT = """You are a document formatting assistant. Your job is to ensure VibeLegal insertions match the original document's formatting style.

You will receive:
1. ORIGINAL FORMAT MAP - How the document looked before VibeLegal made changes
2. CURRENT FORMAT MAP - How the document looks now (VibeLegal insertions marked with [VIBELEGAL])

## UNDERSTANDING BOLD IN CONTENT

Bold text is shown with **markdown** markers:
- "**Purpose.** The Parties wish..." = Only "Purpose." is bold (inline title)
- "**1. Definitions**" = Entire paragraph is bold
- "The Receiving Party shall..." = Nothing is bold

## YOUR TASK

Look at [VIBELEGAL] paragraphs in the current map and compare to similar content in the original:

1. **SECTION HEADERS**: If original headers are bold, VibeLegal headers should be bold too
   - Short paragraphs ending with ":" are usually section headers
   - Use `set_bold_range: "entire"`

2. **INLINE TITLES**: If original has "**Purpose.** text..." pattern, match it
   - Long paragraphs with a title word followed by body text
   - Use `set_bold_range: "start_to_period"`

3. **BODY PARAGRAPHS**: This is CRITICAL - match indent and spacing!
   - Look at original body paragraphs (long text, usually indented)
   - Find their `indent` and `space_after` values
   - Apply same values to VibeLegal body paragraphs
   - Example: If original body has `indent=720, space_after=200`, VibeLegal body should too

4. **LIST ITEMS**: Usually have space_after=0 (no gaps between bullets)

5. **NO CHANGES**: If a VibeLegal paragraph already matches the pattern, don't include it

## FIX TYPES

For bold fixes, specify WHAT to make bold:

```json
{"id": "p5", "set_bold_range": "start_to_period", "reason": "Inline title like Purpose."}
{"id": "p9", "set_bold_range": "entire", "reason": "Section header like OBLIGATIONS:"}
{"id": "p12", "set_indent": 720, "set_space_after": 200, "reason": "Body paragraph like p11"}
{"id": "p7", "set_space_after": 0, "reason": "List item - no spacing"}
```

## BOLD RANGE VALUES

- `"entire"` - Bold entire paragraph (for standalone headings)
- `"start_to_period"` - Bold from start until first "." (for inline titles)
- `"start_to_colon"` - Bold from start until first ":" (for headers)
- `"none"` - Remove all bold (rare)

## HOW TO CHOOSE BOLD RANGE

Look at the CONTENT to decide:

### Standalone Heading (body in NEXT paragraph) → use "entire"
```
p10: [PLAIN] "1. Definitions"
p11: [indent=720] ""Confidential Information" shall mean..."
```
"1. Definitions" is SHORT and its body is in the NEXT paragraph.
Fix: `{"id": "p10", "set_bold_range": "entire"}`

### Inline Title (body in SAME paragraph) → use "start_to_period"
```
p3: [NUMBERED] "Purpose. The Parties wish to explore a potential business..."
```
"Purpose." is the title, followed by body IN THE SAME paragraph.
Fix: `{"id": "p3", "set_bold_range": "start_to_period"}`

### Section Header with Colon → use "entire"
```
p4: [PLAIN] "OBLIGATIONS:"
p5: [BULLET] "Contractor shall maintain..."
```
"OBLIGATIONS:" is a header, list items follow in NEXT paragraphs.
Fix: `{"id": "p4", "set_bold_range": "entire"}`

### Decision Rule
- Paragraph is SHORT (under ~50 chars) and followed by body/list → "entire"
- Paragraph is LONG with title at start → "start_to_period" or "start_to_colon"

## RULES

1. Only fix [VIBELEGAL] paragraphs
2. Match formatting to SIMILAR content in ORIGINAL (never use other VibeLegal paragraphs as reference!)
3. List items should have space_after=0 (no gaps between bullets)
4. Look at the **bold** markers to understand the pattern
5. For numbered clauses like "1. Title" that are SHORT, use "entire"

## SPACING IS CRITICAL - LOOK AT ORIGINAL ONLY

The ORIGINAL format map shows the correct spacing pattern.

Example original:
```
p2: [space_after=200] "1.  Definition of Confidential Information..."
p3: [space_after=200] "2.  Obligations of Receiving Party..."
p4: [space_after=200] "3.  Term..."
```

ALL clauses have `space_after=200`. This is the document's style.

If you see a VibeLegal paragraph like:
```
p5: [PLAIN] [VIBELEGAL] "4. Permitted Disclosure..."
```

It's missing `space_after=200`. Return a fix:
```json
{"id": "p5", "set_space_after": 200, "reason": "Match spacing of original clauses like p2"}
```

## CATCH-ALL: FIX EVERYTHING THAT'S DIFFERENT

Return a fix for EVERY difference you see between:
- What a VibeLegal paragraph HAS
- What it SHOULD have (based on similar original paragraphs)

This includes:
- Missing bold on titles → set_bold_range
- Missing spacing → set_space_after
- Missing indent → set_indent

### MULTIPLE FIXES PER PARAGRAPH ARE OK

If a paragraph needs BOTH bold AND spacing, include BOTH in the same fix object:

```json
{
  "id": "p5",
  "set_bold_range": "start_to_period",
  "set_space_after": 200,
  "reason": "Match original clause formatting like p2"
}
```

## OUTPUT FORMAT

Return a JSON array of fixes:

```json
[
  {"id": "p5", "set_bold_range": "start_to_period", "set_space_after": 200, "reason": "Match original clause style"},
  {"id": "p10", "set_bold_range": "entire", "set_space_after": 200, "reason": "Standalone heading like p2"},
  {"id": "p13", "set_indent": 720, "set_space_after": 200, "reason": "Body paragraph like p11"}
]
```

If no fixes needed, return: []

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

