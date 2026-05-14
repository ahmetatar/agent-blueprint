"""Eval suite schema models."""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class EvalMetric(str, Enum):
    exact_match = "exact_match"
    policy_violations = "policy_violations"
    rubric = "rubric"


class EvalSuiteDef(BaseModel):
    id: str = Field(min_length=1)
    metric: EvalMetric
    dataset: str = Field(min_length=1)
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvalsDef(BaseModel):
    suites: list[EvalSuiteDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_suite_ids(self) -> "EvalsDef":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for suite in self.suites:
            if suite.id in seen:
                duplicates.add(suite.id)
            seen.add(suite.id)
        if duplicates:
            ids = ", ".join(sorted(duplicates))
            raise ValueError(f"evals.suites contains duplicate id(s): {ids}")
        return self
