"""
Persistent source→destination ID mapping.

Tracks what has been migrated so the script is resumable and idempotent.
"""

import json
import os

MAPPER_FILE = "id_map.json"


class IDMapper:
    """Maps source entity IDs to destination entity IDs, persisted to disk."""

    def __init__(self, path: str = MAPPER_FILE):
        self.path = path
        self._data: dict[str, dict[str, int]] = {}
        if os.path.exists(path):
            with open(path) as f:
                self._data = json.load(f)

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def set(self, entity_type: str, source_id: int, dest_id: int,
            fallback: bool = False):
        """Record a mapping. entity_type e.g. 'project', 'todolist', 'todo', 'message'."""
        self._data.setdefault(entity_type, {})[str(source_id)] = dest_id
        if fallback:
            self._data.setdefault("_fallback", {})[f"{entity_type}:{source_id}"] = dest_id
        self._save()

    def get(self, entity_type: str, source_id: int) -> int | None:
        """Look up a destination ID. Returns None if not yet migrated."""
        return self._data.get(entity_type, {}).get(str(source_id))

    def has(self, entity_type: str, source_id: int) -> bool:
        return self.get(entity_type, source_id) is not None

    def get_all(self, entity_type: str) -> dict[str, int]:
        return self._data.get(entity_type, {})

    def is_fallback(self, entity_type: str, source_id: int) -> bool:
        return f"{entity_type}:{source_id}" in self._data.get("_fallback", {})

    def get_fallbacks(self) -> dict[str, int]:
        return self._data.get("_fallback", {})

    def clear_fallback(self, entity_type: str, source_id: int):
        fb = self._data.get("_fallback", {})
        fb.pop(f"{entity_type}:{source_id}", None)
        self._save()

    def summary(self):
        for etype, mappings in self._data.items():
            if etype == "_fallback":
                print(f"  (fallback items needing authorship fix: {len(mappings)})")
            else:
                print(f"  {etype}: {len(mappings)} items mapped")
