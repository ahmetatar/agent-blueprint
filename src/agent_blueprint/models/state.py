"""State schema models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ReducerType(str, Enum):
    APPEND = "append"
    REPLACE = "replace"
    MERGE = "merge"


class FieldDef(BaseModel):
    type: str
    default: Any = None
    reducer: ReducerType = ReducerType.REPLACE
    enum: list[str] | None = None
    nullable: bool = False
    description: str | None = None


class StateDef(BaseModel):
    fields: dict[str, FieldDef] = Field(default_factory=dict)
