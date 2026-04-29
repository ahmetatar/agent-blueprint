"""Harness models for deterministic scenario-based workflow testing."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class HarnessLlmMode(str, Enum):
    mock = "mock"
    replay = "replay"
    live = "live"


class HarnessToolMode(str, Enum):
    stub = "stub"
    live = "live"
    replay = "replay"


class HarnessNormalizeConfig(BaseModel):
    whitespace: bool = True
    timestamps: bool = True
    ids: bool = True


class HarnessFixtures(BaseModel):
    llm_outputs: dict[str, list[Any]] = Field(default_factory=dict)
    tool_outputs: dict[str, Any] = Field(default_factory=dict)


class HarnessDefaults(BaseModel):
    llm_mode: HarnessLlmMode = HarnessLlmMode.mock
    tool_mode: HarnessToolMode = HarnessToolMode.stub
    seed: int | None = None
    replay_trace: str | None = None
    freeze_env: list[str] = Field(default_factory=list)
    normalize: HarnessNormalizeConfig = Field(default_factory=HarnessNormalizeConfig)
    fixtures: HarnessFixtures = Field(default_factory=HarnessFixtures)


class HarnessExpected(BaseModel):
    route: str | None = None
    tools_called: list[str] = Field(default_factory=list)
    output_contract: str | None = None
    state_assertions: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    approvals_triggered: bool | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)


class HarnessScenario(BaseModel):
    id: str
    input: dict[str, Any] = Field(default_factory=dict)
    expected: HarnessExpected = Field(default_factory=HarnessExpected)
    llm_mode: HarnessLlmMode | None = None
    tool_mode: HarnessToolMode | None = None
    seed: int | None = None
    replay_trace: str | None = None
    fixtures: HarnessFixtures = Field(default_factory=HarnessFixtures)


class HarnessDef(BaseModel):
    file: str | None = None
    files: list[str] = Field(default_factory=list)
    defaults: HarnessDefaults = Field(default_factory=HarnessDefaults)
    scenarios: list[HarnessScenario] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_scenario_ids(self) -> "HarnessDef":
        seen: set[str] = set()
        duplicates: set[str] = set()
        for scenario in self.scenarios:
            if scenario.id in seen:
                duplicates.add(scenario.id)
            seen.add(scenario.id)
        if duplicates:
            ids = ", ".join(sorted(duplicates))
            raise ValueError(f"harness.scenarios contains duplicate id(s): {ids}")
        return self
