import sys, os
sys.path.insert(0, "/app")

from text_stats import TextStats


# ── line_count ───────────────────────────────────────────────

def test_line_count_empty():
    assert TextStats("").line_count() == 0


def test_line_count_single_line():
    assert TextStats("hello").line_count() == 1


def test_line_count_multiple_lines():
    assert TextStats("a\nb\nc").line_count() == 3


def test_line_count_trailing_newline():
    assert TextStats("a\n").line_count() == 2


# ── word_count ───────────────────────────────────────────────

def test_word_count_empty():
    assert TextStats("").word_count() == 0


def test_word_count_whitespace_only():
    assert TextStats("   \n\t  ").word_count() == 0


def test_word_count_single_word():
    assert TextStats("hello").word_count() == 1


def test_word_count_multiple_words():
    assert TextStats("one two three four").word_count() == 4


def test_word_count_multiline():
    assert TextStats("one two\nthree four").word_count() == 4


# ── char_count ───────────────────────────────────────────────

def test_char_count_empty():
    assert TextStats("").char_count() == 0


def test_char_count_includes_whitespace():
    assert TextStats("a b").char_count() == 3


def test_char_count_includes_newlines():
    assert TextStats("a\nb").char_count() == 3


# ── most_frequent_word ───────────────────────────────────────

def test_most_frequent_word_empty():
    assert TextStats("").most_frequent_word() is None


def test_most_frequent_word_single():
    assert TextStats("hello").most_frequent_word() == "hello"


def test_most_frequent_word_case_insensitive():
    ts = TextStats("Hello hello HELLO world")
    assert ts.most_frequent_word() == "hello"


def test_most_frequent_word_punctuation_stripped():
    ts = TextStats("hello, hello! world")
    assert ts.most_frequent_word() == "hello"


def test_most_frequent_word_tie_alphabetical():
    ts = TextStats("banana apple banana apple")
    assert ts.most_frequent_word() == "apple"


def test_most_frequent_word_whitespace_only():
    assert TextStats("   ").most_frequent_word() is None


def test_most_frequent_word_only_punctuation():
    assert TextStats("... !!! ---").most_frequent_word() is None


def test_most_frequent_word_returned_lowercase():
    ts = TextStats("WORLD world World")
    assert ts.most_frequent_word() == "world"
