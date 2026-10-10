"""VSSAG exception types."""

from __future__ import annotations


class VSSAGError(Exception):
    """Base exception for all VSSAG errors."""


class ConfigurationError(VSSAGError):
    """Invalid or incomplete SDK configuration."""


class ProviderError(VSSAGError):
    """External model provider request or response failure."""


class ParseError(VSSAGError):
    """Document parsing failure."""


class RetrievalError(VSSAGError):
    """Retrieval failure."""


class VFSPathError(VSSAGError):
    """Invalid or unsafe virtual file path."""


class UnsupportedOperation(VSSAGError):
    """The requested operation is not supported."""
