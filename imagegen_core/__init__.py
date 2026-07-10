"""Core contracts shared by image and video generation paths."""

from .models import (
    CallerContext,
    Capability,
    ErrorKind,
    GenerationHandle,
    GenerationRequest,
    GenerationResult,
    MediaArtifact,
    ProviderFailure,
    ReferenceAsset,
)

__all__ = [
    "CallerContext",
    "Capability",
    "ErrorKind",
    "GenerationHandle",
    "GenerationRequest",
    "GenerationResult",
    "MediaArtifact",
    "ProviderFailure",
    "ReferenceAsset",
]
