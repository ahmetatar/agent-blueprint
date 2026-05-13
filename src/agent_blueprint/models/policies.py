"""Policy schema models."""

from enum import Enum

from pydantic import BaseModel, Field


class ApprovalMode(str, Enum):
    selective = "selective"
    all = "all"


class ApprovalViolationMode(str, Enum):
    block = "block"
    warn = "warn"


class UnknownToolMode(str, Enum):
    fail = "fail"
    ignore = "ignore"


class ApprovalPolicyDef(BaseModel):
    mode: ApprovalMode = ApprovalMode.selective
    tools: list[str] = Field(default_factory=list)
    on_violation: ApprovalViolationMode = ApprovalViolationMode.block


class ToolUsagePolicyDef(BaseModel):
    max_calls_per_node: int | None = Field(default=None, ge=1)
    max_calls_per_run: int | None = Field(default=None, ge=1)
    require_explicit_arguments: bool = False
    on_unknown_tool: UnknownToolMode = UnknownToolMode.ignore


class EscalationPolicyDef(BaseModel):
    on_low_confidence: str | None = None
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class BudgetPolicyDef(BaseModel):
    max_tokens_per_run: int | None = Field(default=None, ge=1)
    max_latency_seconds: float | None = Field(default=None, gt=0)
    max_cost_usd: float | None = Field(default=None, ge=0)


class PoliciesDef(BaseModel):
    approvals: ApprovalPolicyDef = Field(default_factory=ApprovalPolicyDef)
    tool_usage: ToolUsagePolicyDef = Field(default_factory=ToolUsagePolicyDef)
    escalation: EscalationPolicyDef = Field(default_factory=EscalationPolicyDef)
    budgets: BudgetPolicyDef = Field(default_factory=BudgetPolicyDef)
