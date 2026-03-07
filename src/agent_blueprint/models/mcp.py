"""MCP (Model Context Protocol) server configuration models."""

from enum import Enum

from pydantic import BaseModel, model_validator


class McpTransport(str, Enum):
    stdio = "stdio"
    sse = "sse"
    http = "http"


class McpServerDef(BaseModel):
    transport: McpTransport
    # sse / http
    url: str | None = None
    headers: dict[str, str] = {}
    # stdio
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}

    @model_validator(mode="after")
    def validate_transport_fields(self) -> "McpServerDef":
        if self.transport in (McpTransport.sse, McpTransport.http) and not self.url:
            raise ValueError(f"MCP server with transport '{self.transport}' requires a 'url' field")
        if self.transport == McpTransport.stdio and not self.command:
            raise ValueError("MCP server with transport 'stdio' requires a 'command' field")
        return self
