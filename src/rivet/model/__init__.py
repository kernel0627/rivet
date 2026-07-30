"""Provider-neutral model contracts and adapters."""

from rivet.model.errors import ModelErrorKind, ModelGatewayError
from rivet.model.fake import ConditionalResponse, FakeModel, RequestCondition
from rivet.model.gateway import ModelGateway
from rivet.model.providers import (
    ProviderProfile,
    normalize_provider_name,
    resolve_provider,
)
from rivet.model.types import (
    CancellationToken,
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelRequest,
    ModelResult,
    ToolProposal,
    ToolSchema,
    Usage,
)

__all__ = [
    "CancellationToken",
    "ConditionalResponse",
    "FakeModel",
    "Message",
    "MessageRole",
    "ModelErrorKind",
    "ModelEvent",
    "ModelEventType",
    "ModelGateway",
    "ModelGatewayError",
    "ModelRequest",
    "ModelResult",
    "ProviderProfile",
    "RequestCondition",
    "ToolProposal",
    "ToolSchema",
    "Usage",
    "normalize_provider_name",
    "resolve_provider",
]
