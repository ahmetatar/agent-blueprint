"""Contract schema models."""

from typing import Any

from pydantic import BaseModel, Field


class OutputContractDef(BaseModel):
    type: str
    required: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    items: dict[str, Any] | None = None
    description: str | None = None
    additional_properties: bool | dict[str, Any] | None = Field(
        default=None,
        alias="additionalProperties",
    )

    model_config = {"populate_by_name": True, "extra": "allow"}


class StateContractDef(BaseModel):
    required_fields: list[str] = Field(default_factory=list)
    immutable_fields: list[str] = Field(default_factory=list)
    invariants: list[str] = Field(default_factory=list)


class NodeContractDef(BaseModel):
    requires: list[str] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    forbids_mutation: list[str] = Field(default_factory=list)
    output_contract: str | None = None


class ContractsDef(BaseModel):
    state: StateContractDef = Field(default_factory=StateContractDef)
    nodes: dict[str, NodeContractDef] = Field(default_factory=dict)
    outputs: dict[str, OutputContractDef] = Field(default_factory=dict)
