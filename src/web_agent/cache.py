from __future__ import annotations

from collections import OrderedDict
from typing import Any


class Cache:
    """Generic in-memory key-value cache with LRU eviction.

    Provides a simple ``get`` / ``set`` / ``delete`` interface so higher-level
    consumers (e.g. ``DomainChromeCache``) don't depend on a specific storage
    backend.  Swap this out for Redis or similar by implementing the same three
    methods.
    """

    def __init__(self, max_entries: int = 50) -> None:
        self._max_entries = max_entries
        self._store: OrderedDict[str, Any] = OrderedDict()

    # -- public interface --------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Return value for *key*, or ``None`` if not present."""
        if key not in self._store:
            return None
        self._store.move_to_end(key)
        return self._store[key]

    def set(self, key: str, value: Any) -> None:
        """Store *value* under *key*, evicting the eldest entry if at capacity."""
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = value
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)

    def delete(self, key: str) -> bool:
        """Remove *key*. Returns ``True`` if the key existed."""
        if key in self._store:
            del self._store[key]
            return True
        return False

    def keys(self) -> list[str]:
        return list(self._store.keys())

    def clear(self) -> None:
        self._store.clear()
