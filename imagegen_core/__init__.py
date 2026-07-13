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
from .service import GenerationService
from .intent import IntentPlanner
from .jobs import JobManager
from .references import ReferenceResolver

__all__ = [
    "CallerContext",
    "Capability",
    "ErrorKind",
    "GenerationHandle",
    "GenerationRequest",
    "GenerationResult",
    "GenerationService",
    "IntentPlanner",
    "JobManager",
    "MediaArtifact",
    "ProviderFailure",
    "ReferenceAsset",
    "ReferenceResolver",
]
