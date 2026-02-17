# Implement Text Stats

Create a file `/app/text_stats.py` containing a `TextStats` class that analyzes a string and reports basic statistics.

## Requirements

The `TextStats` class must have the following interface:

### `__init__(self, text: str)`
Store the input text for analysis.

### `line_count() -> int`
Return the number of lines in the text. An empty string has **0** lines. Any non-empty string has at least 1 line, with additional lines separated by `\n`.

### `word_count() -> int`
Return the number of whitespace-separated words. An empty or whitespace-only string has **0** words.

### `char_count() -> int`
Return the total number of characters, **including** whitespace and newlines.

### `most_frequent_word() -> str | None`
Return the most frequently occurring word (case-insensitive). Before counting, strip any leading and trailing punctuation characters (characters where `str.isalnum()` is `False`) from each word. Ignore any token that becomes empty after stripping.

- Return `None` if there are no words.
- If there is a tie, return the word that comes first **alphabetically** (lowercase comparison).
- The returned word must be **lowercase**.

## Example

```python
ts = TextStats("Hello world\nhello World")
ts.line_count()          # 2
ts.word_count()          # 4
ts.char_count()          # 22
ts.most_frequent_word()  # "hello"
```
