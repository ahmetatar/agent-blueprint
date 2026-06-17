"""Tests for secret collection and resolution."""

from pathlib import Path

from agent_blueprint.deployers.secrets import collect_required_secrets, resolve_secrets
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name: str) -> BlueprintSpec:
    return BlueprintSpec.model_validate(load_blueprint_yaml(FIXTURES / name))


class TestCollectRequiredSecrets:
    def test_collects_model_provider_api_keys(self):
        spec = load("deploy_agent.yml")
        secrets = collect_required_secrets(spec)
        assert "OPENAI_API_KEY" in secrets
        assert "GOOGLE_API_KEY" in secrets

    def test_collects_tool_auth_env(self):
        spec = load("deploy_agent.yml")
        secrets = collect_required_secrets(spec)
        assert "BILLING_API_KEY" in secrets

    def test_no_secrets_for_plain_blueprint(self):
        spec = load("basic_chatbot.yml")
        secrets = collect_required_secrets(spec)
        # basic chatbot has no model_providers or tool auth
        assert secrets == set()

    def test_does_not_include_non_env_fields(self):
        spec = load("deploy_agent.yml")
        secrets = collect_required_secrets(spec)
        # GCP_PROJECT_ID is a project_env field on GCPDeployConfig, not a secret
        # (it's on the deploy config, not the runtime blueprint)
        assert "GCP_PROJECT_ID" not in secrets

    def test_ir_adds_implicit_provider_key_for_bare_model(self):
        # basic_chatbot has no model_providers entry, only a bare `gpt-4o` agent.
        # The model_providers scan alone misses its OPENAI_API_KEY; the compiled
        # IR resolves the provider so the runtime key is added.
        spec = load("basic_chatbot.yml")
        assert collect_required_secrets(spec) == set()
        ir = compile_blueprint(spec)
        assert "OPENAI_API_KEY" in collect_required_secrets(spec, ir)

    def test_ir_ignores_non_agent_nodes(self, tmp_path):
        # Non-agent nodes (here a function-tool node) keep the default
        # `resolved_provider` and must NOT pull in a spurious provider key —
        # only the azure agent's key should be collected.
        content = """\
blueprint:
  name: "azure-only"
settings:
  default_model: "gpt-4o"
  default_model_provider: azure
state:
  fields:
    messages: { type: "list[message]", reducer: append }
model_providers:
  azure:
    provider: azure_openai
    base_url: "https://example.openai.azure.com"
    deployment: "gpt-4o"
    api_key_env: AZURE_OPENAI_API_KEY
agents:
  assistant:
    model: "${settings.default_model}"
graph:
  entry_point: assistant
  nodes:
    assistant: { agent: assistant }
    notify:
      type: handoff
      channel: slack
      action: notify
      message_template: "done"
  edges:
    - from: assistant
      to: notify
    - from: notify
      to: END
"""
        path = tmp_path / "azure_only.yml"
        path.write_text(content, encoding="utf-8")
        spec = BlueprintSpec.model_validate(load_blueprint_yaml(path))
        ir = compile_blueprint(spec)
        secrets = collect_required_secrets(spec, ir)
        assert "AZURE_OPENAI_API_KEY" in secrets
        assert "OPENAI_API_KEY" not in secrets


class TestResolveSecrets:
    def test_resolves_from_environment(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        resolved, missing = resolve_secrets({"OPENAI_API_KEY"})
        assert resolved["OPENAI_API_KEY"] == "sk-test"
        assert missing == []

    def test_resolves_from_extra(self):
        resolved, missing = resolve_secrets(
            {"MY_KEY"}, extra={"MY_KEY": "my-value"}
        )
        assert resolved["MY_KEY"] == "my-value"
        assert missing == []

    def test_extra_overrides_env(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "from-env")
        resolved, missing = resolve_secrets(
            {"MY_KEY"}, extra={"MY_KEY": "from-extra"}
        )
        assert resolved["MY_KEY"] == "from-extra"

    def test_missing_reported(self):
        resolved, missing = resolve_secrets({"NONEXISTENT_KEY_XYZ"})
        assert "NONEXISTENT_KEY_XYZ" in missing
        assert "NONEXISTENT_KEY_XYZ" not in resolved

    def test_partial_resolution(self, monkeypatch):
        monkeypatch.setenv("PRESENT_KEY", "value")
        resolved, missing = resolve_secrets({"PRESENT_KEY", "ABSENT_KEY"})
        assert "PRESENT_KEY" in resolved
        assert "ABSENT_KEY" in missing
