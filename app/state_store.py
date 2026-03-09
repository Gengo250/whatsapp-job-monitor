from __future__ import annotations

import json
from pathlib import Path


class SeenMessageStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._values: set[str] = set()
        self._load()

    def _load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._flush()
            return

        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._values = {str(item) for item in raw}
        except Exception:
            self._values = set()
            self._flush()

    def has(self, signature: str) -> bool:
        return signature in self._values

    def add(self, signature: str) -> None:
        self._values.add(signature)

    def _flush(self) -> None:
        self.path.write_text(
            json.dumps(sorted(self._values), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def persist(self) -> None:
        self._flush()
