from __future__ import annotations

import re
import unicodedata


class KeywordMatcher:
    URL_RE = re.compile(
        r"(?:https?://\S+|www\.\S+|mailto:\S+|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})",
        re.IGNORECASE,
    )

    def __init__(self, keywords: list[str]) -> None:
        self.normalized_keywords = [self._normalize(item) for item in keywords if item.strip()]

    def matches(self, text: str, require_link: bool = False) -> bool:
        normalized_text = self._normalize(text)
        has_keyword = any(keyword in normalized_text for keyword in self.normalized_keywords)
        if not has_keyword:
            return False
        if require_link and not self.contains_link(text):
            return False
        return True

    @classmethod
    def contains_link(cls, text: str) -> bool:
        return bool(cls.URL_RE.search(text or ""))

    @staticmethod
    def _normalize(value: str) -> str:
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return value.casefold().strip()
