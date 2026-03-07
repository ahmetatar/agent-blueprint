"""Utilities for collecting and resolving required secrets from a blueprint."""

import os

from agent_blueprint.models.blueprint import BlueprintSpec


def collect_required_secrets(spec: BlueprintSpec) -> set[str]:
    """Return the set of environment variable names required at runtime.

    Scans model_providers, tools (auth), and mcp_servers.
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
