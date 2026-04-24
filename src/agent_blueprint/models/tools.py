"""Tool definition models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ToolType(str, Enum):
    function = "function"
    api = "api"
    retrieval = "retrieval"
    mcp = "mcp"


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
        return self
