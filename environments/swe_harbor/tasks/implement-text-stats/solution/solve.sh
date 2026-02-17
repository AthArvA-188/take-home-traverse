#!/bin/bash
cat > /app/text_stats.py << 'EOF'
class TextStats:
    def __init__(self, text: str):
        self._text = text

    def line_count(self) -> int:
        if not self._text:
            return 0
        return self._text.count('\n') + 1

    def word_count(self) -> int:
        return len(self._text.split())

    def char_count(self) -> int:
        return len(self._text)

    def most_frequent_word(self) -> str | None:
        words = self._text.split()
        if not words:
            return None

        counts: dict[str, int] = {}
        for raw in words:
            stripped = raw.strip()
            # Strip leading and trailing non-alphanumeric characters
            while stripped and not stripped[0].isalnum():
                stripped = stripped[1:]
            while stripped and not stripped[-1].isalnum():
                stripped = stripped[:-1]
            if not stripped:
                continue
            key = stripped.lower()
            counts[key] = counts.get(key, 0) + 1

        if not counts:
            return None

        max_count = max(counts.values())
        candidates = [w for w, c in counts.items() if c == max_count]
        candidates.sort()
        return candidates[0]
EOF
