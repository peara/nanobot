from __future__ import annotations


class VectorStoreError(Exception):
    """Base exception for vector store errors."""

    pass


class ConfigNotFoundError(VectorStoreError):
    """Raised when mem0 config file is not found."""

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"mem0 config file not found: {path}")


class ConfigLoadError(VectorStoreError):
    """Raised when mem0 config cannot be loaded."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to load mem0 config from {path}: {reason}")
