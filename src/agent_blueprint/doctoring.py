"""Pre-generation doctor checks for agent blueprints."""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from enum import Enum

from agent_blueprint.cli.generate import TargetFramework
from agent_blueprint.deployers.secrets import collect_required_secrets
from agent_blueprint.ir.compiler import AgentGraph
from agent_blueprint.models.blueprint import BlueprintSpec


class DoctorSeverity(str, Enum):
    error = "error"
    warning = "warning"


@dataclass(frozen=True)
class DoctorFinding:
    severity: DoctorSeverity
    code: str
    location: str
    message: str


def doctor_blueprint(spec: BlueprintSpec, ir: AgentGraph, *, target: TargetFramework) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    findings.extend(_check_env_vars(spec, ir))
    findings.extend(_check_impl_imports(spec))
    findings.extend(_check_provider_configuration(spec, ir))
    findings.extend(_check_target_compatibility(spec, ir, target=target))
    return findings


def _check_env_vars(spec: BlueprintSpec, ir: AgentGraph) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    required = collect_required_secrets(spec)
    if ir.memory.connection_string_env:
        required.add(ir.memory.connection_string_env)

    for env_var in sorted(required):
        if not os.environ.get(env_var):
            findings.append(DoctorFinding(
                severity=DoctorSeverity.warning,
                code="missing-env-var",
                location=f"env.{env_var}",
                message=f"Environment variable '{env_var}' is declared by the blueprint but is not set",
            ))
    return findings


def _check_impl_imports(spec: BlueprintSpec) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []

    for tool_name, tool in spec.tools.items():
        if tool.impl and _resolve_impl_error(tool.impl):
            findings.append(DoctorFinding(
                severity=DoctorSeverity.error,
                code="unresolved-impl-import",
                location=f"tools.{tool_name}.impl",
                message=f"Could not import '{tool.impl}'",
            ))

    for retriever_name, retriever in spec.retrievers.items():
        if _resolve_impl_error(retriever.impl):
            findings.append(DoctorFinding(
                severity=DoctorSeverity.error,
                code="unresolved-impl-import",
                location=f"retrievers.{retriever_name}.impl",
                message=f"Could not import '{retriever.impl}'",
            ))

    return findings


def _resolve_impl_error(dotted_path: str) -> str | None:
    module_path, sep, attr_name = dotted_path.rpartition(".")
    if not sep:
        return "missing attribute path"
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        return str(exc)
    if not hasattr(module, attr_name):
        return f"attribute '{attr_name}' not found"
    return None


def _check_provider_configuration(spec: BlueprintSpec, ir: AgentGraph) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []
    for node in ir.nodes:
        if not node.agent:
            continue
        provider = node.resolved_provider
        provider_def = node.resolved_provider_def

        if provider in {"openai", "anthropic", "google", "azure_openai", "openai_compatible"}:
            if provider_def is None:
                findings.append(DoctorFinding(
                    severity=DoctorSeverity.warning,
                    code="implicit-provider-config",
                    location=f"agents.{node.node_def.agent or node.id}",
                    message=(
                        f"Node '{node.id}' uses provider '{provider}' without an explicit model_provider; "
                        "runtime credentials will rely on ambient SDK defaults"
                    ),
                ))
            elif not provider_def.api_key_env:
                findings.append(DoctorFinding(
                    severity=DoctorSeverity.warning,
                    code="provider-api-key-env-missing",
                    location=f"model_providers.{node.agent.model_provider or spec.settings.default_model_provider}",
                    message=(
                        f"Provider '{node.agent.model_provider or spec.settings.default_model_provider}' is used by node "
                        f"'{node.id}' but has no api_key_env configured"
                    ),
                ))

        if provider == "bedrock" and provider_def and not provider_def.aws_profile_env:
            findings.append(DoctorFinding(
                severity=DoctorSeverity.warning,
                code="provider-aws-profile-env-missing",
                location=f"model_providers.{node.agent.model_provider or spec.settings.default_model_provider}",
                message=(
                    f"Bedrock provider '{node.agent.model_provider or spec.settings.default_model_provider}' is used by "
                    f"node '{node.id}' but has no aws_profile_env configured"
                ),
            ))
    return findings


def _check_target_compatibility(
    spec: BlueprintSpec,
    ir: AgentGraph,
    *,
    target: TargetFramework,
) -> list[DoctorFinding]:
    findings: list[DoctorFinding] = []

    if target == TargetFramework.crewai:
        findings.append(DoctorFinding(
            severity=DoctorSeverity.error,
            code="target-not-implemented",
            location="target.crewai",
            message="CrewAI target is not implemented yet; use --target langgraph or --target plain",
        ))
        return findings

    if target == TargetFramework.plain:
        if len(ir.nodes) > 1:
            findings.append(DoctorFinding(
                severity=DoctorSeverity.error,
                code="target-incompatible-feature",
                location="graph.nodes",
                message="Plain target does not support multi-node workflows",
            ))
        if any(edge.from_node != ir.entry_point or len(edge.targets) != 1 or edge.targets[0].target != "END" for edge in ir.edges):
            findings.append(DoctorFinding(
                severity=DoctorSeverity.error,
                code="target-incompatible-feature",
                location="graph.edges",
                message="Plain target only supports a single entry node that returns directly without graph routing",
            ))
        if spec.tools:
            findings.append(DoctorFinding(
                severity=DoctorSeverity.error,
                code="target-incompatible-feature",
                location="tools",
                message="Plain target does not support ABP tool wiring",
            ))
        if spec.contracts:
            findings.append(DoctorFinding(
                severity=DoctorSeverity.warning,
                code="target-partial-feature",
                location="contracts",
                message="Plain target does not enforce node/state contracts at runtime",
            ))
        if spec.harness:
            findings.append(DoctorFinding(
                severity=DoctorSeverity.warning,
                code="target-partial-feature",
                location="harness",
                message="Plain target does not integrate with ABP harness or replay helpers",
            ))

    return findings
