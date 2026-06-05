"""Tests for the `run:` blueprint section (sandbox config)."""

import pytest
from pydantic import ValidationError

from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.run import RunConfig, SandboxConfig, SandboxEngine, SandboxNetwork

_MINIMAL = {
    "blueprint": {"name": "demo"},
    "graph": {
        "entry_point": "n",
        "nodes": {"n": {"type": "function"}},
        "edges": [],
    },
}


class TestSandboxConfigDefaults:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.enabled is False
        assert cfg.engine == SandboxEngine.auto
        assert cfg.image == "python:3.11-slim"
        assert cfg.network == SandboxNetwork.bridge
        assert cfg.memory is None
        assert cfg.cpus is None
        assert cfg.env_passthrough == []

    def test_run_config_sandbox_optional(self):
        assert RunConfig().sandbox is None


class TestSandboxConfigValidation:
    def test_full_config(self):
        cfg = SandboxConfig(
            enabled=True,
            engine="podman",
            image="python:3.12-slim",
            network="none",
            memory="512m",
            cpus=1.5,
            env_passthrough=["FOO", "BAR"],
        )
        assert cfg.engine == SandboxEngine.podman
        assert cfg.network == SandboxNetwork.none
        assert cfg.cpus == 1.5

    def test_invalid_engine_rejected(self):
        with pytest.raises(ValidationError):
            SandboxConfig(engine="containerd")

    def test_invalid_network_rejected(self):
        with pytest.raises(ValidationError):
            SandboxConfig(network="macvlan")

    def test_nonpositive_cpus_rejected(self):
        with pytest.raises(ValidationError):
            SandboxConfig(cpus=0)

    def test_empty_memory_rejected(self):
        with pytest.raises(ValidationError):
            SandboxConfig(memory="  ")


class TestBlueprintRunSection:
    def test_spec_without_run_section(self):
        spec = BlueprintSpec.model_validate(_MINIMAL)
        assert spec.run is None

    def test_spec_with_run_sandbox(self):
        raw = dict(_MINIMAL)
        raw["run"] = {
            "sandbox": {
                "enabled": True,
                "engine": "podman",
                "network": "none",
                "memory": "256m",
                "env_passthrough": ["MY_KEY"],
            }
        }
        spec = BlueprintSpec.model_validate(raw)
        assert spec.run is not None
        assert spec.run.sandbox is not None
        assert spec.run.sandbox.enabled is True
        assert spec.run.sandbox.engine == SandboxEngine.podman
        assert spec.run.sandbox.env_passthrough == ["MY_KEY"]
