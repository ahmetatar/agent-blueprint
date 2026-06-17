"""Utilities for collecting and resolving required secrets from a blueprint."""

import os
from typing import TYPE_CHECKING

from agent_blueprint.models.blueprint import BlueprintSpec

if TYPE_CHECKING:
    from agent_blueprint.ir.compiler import AgentGraph

#: Runtime env keys for each provider, used when an agent resolves to a provider
#: via a bare model (no `model_providers` entry whose api_key_env would cover it).
_PROVIDER_ENV_KEYS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "google": ["GOOGLE_API_KEY"],
    "azure_openai": ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"],
}


def collect_required_secrets(
    spec: BlueprintSpec, ir: "AgentGraph | None" = None
) -> set[str]:
    """Return the set of environment variable names required at runtime.

    Scans model_providers and tool auth. When the compiled `ir` is supplied,
    also adds the implicit provider key for each node's resolved provider — e.g.
    an agent on a bare `gpt-4o` (no model_providers entry) still needs
    OPENAI_API_KEY, which the model_providers scan alone would miss.
    """
    secrets: set[str] = set()

    for provider in spec.model_providers.values():
        if provider.api_key_env:
            secrets.add(provider.api_key_env)
        if provider.aws_profile_env:
            secrets.add(provider.aws_profile_env)

    for tool in spec.tools.values():
        if tool.auth:
            for env_var in [
                tool.auth.token_env,
                tool.auth.key_env,
                tool.auth.username_env,
                tool.auth.password_env,
            ]:
                if env_var:
                    secrets.add(env_var)

    if ir is not None:
        for node in ir.nodes:
            # Only agent nodes call an LLM; non-agent nodes (tool/join/subgraph
            # adapters) keep the default `resolved_provider` and would otherwise
            # pull in a spurious key (matches the compiler's `if node.agent` use).
            if not node.agent:
                continue
            resolved = getattr(node, "resolved_provider", None)
            if resolved:
                secrets.update(_PROVIDER_ENV_KEYS.get(resolved, []))

    return secrets


def resolve_secrets(
    names: set[str],
    *,
    extra: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Look up secret values from the environment and extra overrides.

    Returns:
        resolved  — dict of name → value for secrets that were found
        missing   — list of names that could not be resolved
    """
    extra = extra or {}
    resolved: dict[str, str] = {}
    missing: list[str] = []

    for name in sorted(names):
        value = extra.get(name) or os.environ.get(name)
        if value:
            resolved[name] = value
        else:
            missing.append(name)

    return resolved, missing
