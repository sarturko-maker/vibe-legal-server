"""
Word Position Map - Per-paragraph word-to-formatting mapping.

Builds a map of words in a paragraph with their character positions and
formatting (rPr). Used by amend_text() to preserve formatting when
replacing words.

Example:
    Paragraph: "The SELLER shall pay"
    Word 0: "The"    (chars 0-3,   no bold)
    Word 1: "SELLER" (chars 4-10,  BOLD)
    Word 2: "shall"  (chars 11-16, no bold)
    Word 3: "pay"    (chars 17-20, no bold)

When replacing "SELLER" with "VENDOR", we look up SELLER's formatting
and apply it to VENDOR, so VENDOR becomes bold.
"""

from dataclasses import dataclass
from typing import List, Optional
from copy import deepcopy
from lxml import etree
import re
import logging

logger = logging.getLogger(__name__)

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


@dataclass
class WordEntry:
    """
    Represents a word in a paragraph with its position and formatting.
    
    Attributes:
        text: The word itself (e.g., "SELLER")
        position: Word index in paragraph (0, 1, 2...)
        char_start: Character offset where word starts
        char_end: Character offset where word ends
        rPr: Run properties element (bold, italic, etc.) - may be None
    """
    text: str
    position: int
    char_start: int
    char_end: int
    rPr: Optional[etree._Element]


def build_word_map_for_paragraph(para: etree._Element) -> List[WordEntry]:
    """
    Build a word-by-word map of a paragraph with formatting info.
    
    This function:
    1. Collects all text segments with their rPr from runs
    2. Builds a char-to-rPr mapping
    3. Tokenizes text into words using r'\\S+' pattern
    4. Records each word's position, char offsets, and rPr
    
    Tokenization rules:
    - Split on whitespace (r'\\S+' matches non-whitespace sequences)
    - Punctuation stays attached: "claims." is one word
    - Hyphens stay together: "co-operation" is one word
    - Apostrophes stay together: "SELLER's" is one word
    
    Args:
        para: The w:p paragraph element
        
    Returns:
        List of WordEntry objects in order of appearance
    """
    # 1. Collect text segments with their rPr
    text_segments: List[tuple] = []
    
    for run in para.findall(f'.//{W}r'):
        rPr = run.find(f'{W}rPr')
        for t_elem in run.findall(f'{W}t'):
            text = t_elem.text or ""
            if text:
                text_segments.append((text, rPr))
    
    if not text_segments:
        return []
    
    # 2. Build char-to-rPr mapping
    char_to_rPr: List[Optional[etree._Element]] = []
    for text, rPr in text_segments:
        for _ in text:
            char_to_rPr.append(rPr)
    
    # 3. Concatenate to get full paragraph text
    full_text = "".join(seg[0] for seg in text_segments)
    
    # 4. Tokenize into words and record positions
    word_map: List[WordEntry] = []
    
    for i, match in enumerate(re.finditer(r'\S+', full_text)):
        word_text = match.group()
        char_start = match.start()
        char_end = match.end()
        
        # Get rPr from first character of word
        word_rPr = None
        if char_start < len(char_to_rPr):
            source_rPr = char_to_rPr[char_start]
            if source_rPr is not None:
                word_rPr = deepcopy(source_rPr)
        
        word_map.append(WordEntry(
            text=word_text,
            position=i,
            char_start=char_start,
            char_end=char_end,
            rPr=word_rPr
        ))
    
    logger.debug(f"Built word map with {len(word_map)} words")
    for entry in word_map:
        has_bold = entry.rPr is not None and entry.rPr.find(f'{W}b') is not None
        logger.debug(f"  Word {entry.position}: '{entry.text}' chars {entry.char_start}-{entry.char_end} bold={has_bold}")
    
    return word_map


def get_formatting_for_char_position(
    word_map: List[WordEntry], 
    char_pos: int
) -> Optional[etree._Element]:
    """
    Look up the rPr for a given character position in the paragraph.
    
    Logic:
    1. Find WordEntry where char_start <= char_pos < char_end
    2. If char_pos is in a space (between words), return previous word's rPr
    3. If char_pos is before first word, return first word's rPr
    4. If char_pos is after last word, return last word's rPr
    
    Args:
        word_map: List of WordEntry from build_word_map_for_paragraph
        char_pos: Character position to look up
        
    Returns:
        Deep copy of rPr element, or None if no formatting
    """
    if not word_map:
        return None
    
    # 1. Check if char_pos is inside a word
    for word in word_map:
        if word.char_start <= char_pos < word.char_end:
            return deepcopy(word.rPr) if word.rPr is not None else None
    
    # 2. char_pos is in a space (between words) or outside
    # Find which word it's closest to
    for i, word in enumerate(word_map):
        if char_pos < word.char_start:
            # Position is before this word - use previous word's rPr
            if i > 0:
                prev_rPr = word_map[i - 1].rPr
                return deepcopy(prev_rPr) if prev_rPr is not None else None
            else:
                # Before first word - use first word's rPr
                return deepcopy(word_map[0].rPr) if word_map[0].rPr is not None else None
    
    # 3. Position is after all words - use last word's rPr
    last_rPr = word_map[-1].rPr
    return deepcopy(last_rPr) if last_rPr is not None else None


def get_formatting_for_word_position(
    word_map: List[WordEntry],
    word_pos: int
) -> Optional[etree._Element]:
    """
    Look up the rPr for a given word position (index).
    
    Args:
        word_map: List of WordEntry from build_word_map_for_paragraph
        word_pos: Word index (0, 1, 2...)
        
    Returns:
        Deep copy of rPr element, or None if no formatting
    """
    if not word_map:
        return None
    
    for word in word_map:
        if word.position == word_pos:
            return deepcopy(word.rPr) if word.rPr is not None else None
    
    # Position out of range - return last word's formatting
    last_rPr = word_map[-1].rPr
    return deepcopy(last_rPr) if last_rPr is not None else None


def rebuild_runs_for_text_range(
    para: etree._Element,
    word_map: List[WordEntry],
    text: str,
    char_start: int,
    char_end: int
) -> None:
    """
    Rebuild runs for a text range, preserving per-word formatting.
    
    SIMPLIFIED ALGORITHM:
    1. For each character in `text`, look up its formatting from word_map
    2. Group adjacent characters with same formatting
    3. Create one run per group
    
    Args:
        para: The paragraph element to append runs to
        word_map: Word map with formatting info
        text: The text to add
        char_start: Start position in original paragraph (used for formatting lookup)
        char_end: End position in original paragraph
    """
    if not text:
        return
    
    if not word_map:
        # No word map - just add plain run
        run = etree.SubElement(para, f'{W}r')
        t = etree.SubElement(run, f'{W}t')
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = text
        return
    
    # SIMPLE APPROACH: Build char-to-rPr array for this text range
    # Each index in the array corresponds to a character in `text`
    char_formats = []
    for i, char in enumerate(text):
        original_pos = char_start + i  # Position in original paragraph
        rPr = get_formatting_for_char_position(word_map, original_pos)
        char_formats.append(rPr)
    
    # Group adjacent characters with same formatting
    segments = []
    current_text = ""
    current_rPr = None
    current_rPr_set = False
    
    for i, char in enumerate(text):
        char_rPr = char_formats[i]
        
        if not current_rPr_set:
            # First character
            current_text = char
            current_rPr = char_rPr
            current_rPr_set = True
        elif _rPr_equals(current_rPr, char_rPr):
            # Same formatting - append to current segment
            current_text += char
        else:
            # Formatting changed - save current segment and start new one
            if current_text:
                segments.append((current_text, current_rPr))
            current_text = char
            current_rPr = char_rPr
    
    # Don't forget the last segment
    if current_text:
        segments.append((current_text, current_rPr))
    
    # Create runs for each segment
    for seg_text, seg_rPr in segments:
        if not seg_text:
            continue
        run = etree.SubElement(para, f'{W}r')
        if seg_rPr is not None:
            run.insert(0, deepcopy(seg_rPr))
        t = etree.SubElement(run, f'{W}t')
        t.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        t.text = seg_text


def _rPr_equals(rPr1: Optional[etree._Element], rPr2: Optional[etree._Element]) -> bool:
    """
    Compare two rPr elements for equality.
    """
    if rPr1 is None and rPr2 is None:
        return True
    if rPr1 is None or rPr2 is None:
        return False
    
    # Compare key formatting properties
    bold1 = rPr1.find(f'{W}b') is not None
    bold2 = rPr2.find(f'{W}b') is not None
    if bold1 != bold2:
        return False
    
    italic1 = rPr1.find(f'{W}i') is not None
    italic2 = rPr2.find(f'{W}i') is not None
    if italic1 != italic2:
        return False
    
    underline1 = rPr1.find(f'{W}u') is not None
    underline2 = rPr2.find(f'{W}u') is not None
    if underline1 != underline2:
        return False
    
    return True

