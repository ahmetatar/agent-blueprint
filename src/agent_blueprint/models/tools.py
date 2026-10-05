"""Tool definition models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from agent_blueprint.models.graph import RetryPolicyDef


class ToolType(str, Enum):
    function = "function"
    api = "api"
    retrieval = "retrieval"
    mcp = "mcp"


class SideEffect(str, Enum):
    none = "none"
    read = "read"
    write = "write"
    irreversible = "irreversible"


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class AuthType(str, Enum):
    bearer = "bearer"
    basic = "basic"
    api_key = "api_key"


class AuthDef(BaseModel):
    type: AuthType
    token_env: str | None = None
    username_env: str | None = None
    password_env: str | None = None
    header: str = "Authorization"
    key_env: str | None = None


class ParameterDef(BaseModel):
    type: str
    required: bool = False
    default: Any = None
    description: str | None = None
    enum: list[str] | None = None


class ToolDef(BaseModel):
    type: ToolType
    description: str | None = None
    parameters: dict[str, ParameterDef] = Field(default_factory=dict)
    returns: ParameterDef | None = None
    requires_approval: bool = False

    # side-effect metadata (opt-in; undeclared tools keep today's behaviour)
    side_effect: SideEffect | None = None
    idempotent: bool | None = None
    # Explicit waiver of the approval that `irreversible` tools otherwise imply.
    approval_waived: bool = False
    # Retries the tool execution itself (after approval), not the LLM call.
    retry: RetryPolicyDef | None = None

    # api tool fields
    method: HttpMethod | None = None
    url: str | None = None
    auth: AuthDef | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    # retrieval tool fields
    retriever: str | None = None
    source: str | None = None
    embedding_model: str | None = None
    top_k: int = 5

    # mcp tool fields
    server: str | None = None
    tool: str | None = None

    # function tool fields
    impl: str | None = None  # dotted import path, e.g. "mypackage.tools.my_func"

    @model_validator(mode="after")
    def validate_type_fields(self) -> "ToolDef":
        if self.type == ToolType.api and not self.url:
            raise ValueError("api tools require a 'url' field")
        if self.type == ToolType.retrieval and not self.retriever:
            raise ValueError("retrieval tools require a 'retriever' field")
        if self.type == ToolType.mcp and not (self.server and self.tool):
            raise ValueError("mcp tools require 'server' and 'tool' fields")
        if self.impl and self.type != ToolType.function:
            raise ValueError("'impl' is only valid for function tools")
        if self.type == ToolType.retrieval and self.side_effect in (
            SideEffect.write,
            SideEffect.irreversible,
        ):
            raise ValueError("retrieval tools are read-only; side_effect cannot be write or irreversible")
        if self.approval_waived and self.side_effect != SideEffect.irreversible:
            raise ValueError("'approval_waived' is only valid for side_effect: irreversible")
        if self.approval_waived and self.requires_approval:
            raise ValueError("'approval_waived' conflicts with 'requires_approval: true'")
        if (
            self.retry is not None
            and self.retry.max_attempts > 1
            and self.side_effect in (SideEffect.write, SideEffect.irreversible)
            and self.idempotent is not True
        ):
            raise ValueError(
                f"unsafe-retry: tool retries (max_attempts={self.retry.max_attempts}) on a "
                f"side_effect: {self.side_effect.value} tool require 'idempotent: true'; "
                "a retried call may repeat an effect that already happened"
            )
        return self

    @property
    def effective_requires_approval(self) -> bool:
        """Approval gate actually enforced: explicit flag, or implied by `irreversible`."""
        if self.requires_approval:
            return True
        return self.side_effect == SideEffect.irreversible and not self.approval_waived
