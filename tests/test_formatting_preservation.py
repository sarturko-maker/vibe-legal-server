"""
Tests for formatting preservation in amend_text().

Run with: python -m pytest tests/test_formatting_preservation.py -v
Or directly: python tests/test_formatting_preservation.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lxml import etree
from ooxml_lib.word_position_map import (
    WordEntry, 
    build_word_map_for_paragraph, 
    get_formatting_for_char_position
)

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def create_test_paragraph(segments: list) -> etree._Element:
    """
    Create a test paragraph with specified text segments and formatting.
    
    Args:
        segments: List of (text, is_bold, is_italic) tuples
        
    Returns:
        w:p element
    """
    para = etree.Element(f'{W}p')
    
    for text, is_bold, is_italic in segments:
        run = etree.SubElement(para, f'{W}r')
        
        if is_bold or is_italic:
            rPr = etree.SubElement(run, f'{W}rPr')
            if is_bold:
                etree.SubElement(rPr, f'{W}b')
            if is_italic:
                etree.SubElement(rPr, f'{W}i')
        
        t = etree.SubElement(run, f'{W}t')
        t.text = text
    
    return para


def test_word_map_basic():
    """Test basic word map building."""
    # Create: "The SELLER shall"
    para = create_test_paragraph([
        ("The ", False, False),
        ("SELLER", True, False),
        (" shall", False, False),
    ])
    
    word_map = build_word_map_for_paragraph(para)
    
    assert len(word_map) == 3
    assert word_map[0].text == "The"
    assert word_map[1].text == "SELLER"
    assert word_map[2].text == "shall"
    
    # Check positions
    assert word_map[0].char_start == 0
    assert word_map[0].char_end == 3
    assert word_map[1].char_start == 4
    assert word_map[1].char_end == 10
    
    print("✓ test_word_map_basic passed")


def test_bold_word_formatting():
    """Test that bold words are captured correctly."""
    para = create_test_paragraph([
        ("The ", False, False),
        ("SELLER", True, False),  # BOLD
        (" shall pay", False, False),
    ])
    
    word_map = build_word_map_for_paragraph(para)
    
    # Word 0 "The" should NOT be bold
    assert word_map[0].rPr is None or word_map[0].rPr.find(f'{W}b') is None
    
    # Word 1 "SELLER" SHOULD be bold
    assert word_map[1].rPr is not None
    assert word_map[1].rPr.find(f'{W}b') is not None
    
    # Word 2 "shall" should NOT be bold
    assert word_map[2].rPr is None or word_map[2].rPr.find(f'{W}b') is None
    
    print("✓ test_bold_word_formatting passed")


def test_get_formatting_for_char_position():
    """Test formatting lookup by character position."""
    para = create_test_paragraph([
        ("The ", False, False),
        ("SELLER", True, False),  # BOLD
        (" shall", False, False),
    ])
    
    word_map = build_word_map_for_paragraph(para)
    
    # Position 5 is inside "SELLER" (chars 4-10) - should be bold
    rPr = get_formatting_for_char_position(word_map, 5)
    assert rPr is not None
    assert rPr.find(f'{W}b') is not None
    
    # Position 0 is inside "The" - should NOT be bold
    rPr = get_formatting_for_char_position(word_map, 0)
    assert rPr is None or rPr.find(f'{W}b') is None
    
    # Position 12 is inside "shall" - should NOT be bold
    rPr = get_formatting_for_char_position(word_map, 12)
    assert rPr is None or rPr.find(f'{W}b') is None
    
    print("✓ test_get_formatting_for_char_position passed")


def test_italic_word_formatting():
    """Test italic word formatting is captured."""
    para = create_test_paragraph([
        ("Contact the ", False, False),
        ("Buyer", False, True),  # ITALIC
        (" immediately", False, False),
    ])
    
    word_map = build_word_map_for_paragraph(para)
    
    # Find "Buyer"
    buyer_word = next(w for w in word_map if w.text == "Buyer")
    assert buyer_word.rPr is not None
    assert buyer_word.rPr.find(f'{W}i') is not None
    
    print("✓ test_italic_word_formatting passed")


def test_mixed_formatting():
    """Test paragraph with bold AND italic words."""
    para = create_test_paragraph([
        ("The ", False, False),
        ("SELLER", True, False),    # BOLD
        (" shall contact the ", False, False),
        ("Buyer", False, True),     # ITALIC
        (" immediately.", False, False),
    ])
    
    word_map = build_word_map_for_paragraph(para)
    
    seller = next(w for w in word_map if w.text == "SELLER")
    buyer = next(w for w in word_map if w.text == "Buyer")
    
    # SELLER is bold
    assert seller.rPr is not None
    assert seller.rPr.find(f'{W}b') is not None
    
    # Buyer is italic
    assert buyer.rPr is not None
    assert buyer.rPr.find(f'{W}i') is not None
    
    print("✓ test_mixed_formatting passed")


def test_punctuation_attached():
    """Test that punctuation stays with word."""
    para = create_test_paragraph([
        ("The claims.", False, False),
    ])
    
    word_map = build_word_map_for_paragraph(para)
    
    # Should have 2 words: "The" and "claims."
    assert len(word_map) == 2
    assert word_map[1].text == "claims."  # Period attached
    
    print("✓ test_punctuation_attached passed")


def test_space_inherits_from_previous():
    """Test that spaces between words inherit from previous word."""
    para = create_test_paragraph([
        ("The ", False, False),
        ("SELLER", True, False),  # BOLD
        (" shall", False, False),
    ])
    
    word_map = build_word_map_for_paragraph(para)
    
    # Position 3 is the space after "The" (before "SELLER")
    # It should inherit from "The" (position before first word of next)
    rPr = get_formatting_for_char_position(word_map, 3)
    # Space at position 3 is between words, should get previous word's rPr
    # "The" has no rPr, so None is expected
    
    # Position 10 is the space after "SELLER"
    rPr = get_formatting_for_char_position(word_map, 10)
    # This should inherit from "SELLER" which is bold
    assert rPr is not None
    assert rPr.find(f'{W}b') is not None
    
    print("✓ test_space_inherits_from_previous passed")


if __name__ == "__main__":
    test_word_map_basic()
    test_bold_word_formatting()
    test_get_formatting_for_char_position()
    test_italic_word_formatting()
    test_mixed_formatting()
    test_punctuation_attached()
    test_space_inherits_from_previous()
    print("\n✅ All tests passed!")
