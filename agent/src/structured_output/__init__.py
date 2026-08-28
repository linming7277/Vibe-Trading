"""Provider-agnostic structured-output primitives."""

from .runtime import (
    StructuredOutputCapabilities,
    StructuredOutputMode,
    StructuredOutputResult,
    StructuredOutputRuntime,
    resolve_structured_output_capabilities,
)

__all__ = [
    "StructuredOutputCapabilities", "StructuredOutputMode", "StructuredOutputResult",
    "StructuredOutputRuntime", "resolve_structured_output_capabilities",
]
