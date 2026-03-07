"""Tests for model provider configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load(name: str) -> dict:
    return load_blueprint_yaml(FIXTURES / name)


class TestModelProviders:
    def test_multi_provider_blueprint_loads(self):
        raw = load("model_providers.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert "openai_default" in spec.model_providers
        assert "gemini" in spec.model_providers
        assert "local_ollama" in spec.model_providers
        assert "azure_gpt4" in spec.model_providers
        assert "anthropic_claude" in spec.model_providers

    def test_provider_types(self):
        raw = load("model_providers.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.model_providers["openai_default"].provider == "openai"
        assert spec.model_providers["gemini"].provider == "google"
        assert spec.model_providers["local_ollama"].provider == "ollama"
        assert spec.model_providers["azure_gpt4"].provider == "azure_openai"
        assert spec.model_providers["anthropic_claude"].provider == "anthropic"

    def test_ollama_base_url(self):
        raw = load("model_providers.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.model_providers["local_ollama"].base_url == "http://localhost:11434"

    def test_azure_fields(self):
        raw = load("model_providers.yml")
        spec = BlueprintSpec.model_validate(raw)
        azure = spec.model_providers["azure_gpt4"]
        assert azure.deployment == "gpt-4o-prod"
        assert azure.api_version == "2024-02-01"
        assert azure.api_key_env == "AZURE_OPENAI_KEY"

    def test_default_model_provider_in_settings(self):
        raw = load("model_providers.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.settings.default_model_provider == "openai_default"

    def test_agent_model_provider_reference(self):
        raw = load("model_providers.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.agents["researcher"].model_provider == "gemini"
        assert spec.agents["researcher"].model == "gemini-2.0-flash"
        assert spec.agents["local_agent"].model_provider == "local_ollama"

    def test_undefined_provider_in_agent_raises(self):
        raw = load("model_providers.yml")
        raw["agents"]["router"]["model_provider"] = "nonexistent"
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate(raw)
        assert "nonexistent" in str(exc_info.value)

    def test_undefined_default_provider_in_settings_raises(self):
        raw = load("model_providers.yml")
        raw["settings"]["default_model_provider"] = "nonexistent"
        with pytest.raises(ValidationError) as exc_info:
            BlueprintSpec.model_validate(raw)
        assert "nonexistent" in str(exc_info.value)

    def test_ollama_missing_base_url_raises(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "model_providers": {"local": {"provider": "ollama"}},  # base_url eksik
                "graph": {"entry_point": "n", "nodes": {"n": {}}, "edges": []},
            })

    def test_azure_missing_deployment_raises(self):
        with pytest.raises(ValidationError):
            BlueprintSpec.model_validate({
                "blueprint": {"name": "test"},
                "model_providers": {
                    "az": {
                        "provider": "azure_openai",
                        "base_url": "https://example.openai.azure.com",
                        # deployment eksik
                    }
                },
                "graph": {"entry_point": "n", "nodes": {"n": {}}, "edges": []},
            })

    def test_blueprint_without_model_providers_still_valid(self):
        """Existing blueprints without model_providers must remain valid."""
        raw = load("basic_chatbot.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.model_providers == {}
        assert spec.settings.default_model_provider is None
