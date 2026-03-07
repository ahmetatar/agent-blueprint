"""Tests for DeployPackager — Dockerfile and server.py generation."""

import ast
from pathlib import Path

from agent_blueprint.deployers.packager import DeployPackager
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_ir(name: str):
    raw = load_blueprint_yaml(FIXTURES / name)
    spec = BlueprintSpec.model_validate(raw)
    return compile_blueprint(spec)


class TestDeployPackager:
    def test_creates_dockerfile(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        assert (tmp_path / "Dockerfile").exists()

    def test_creates_dockerignore(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        assert (tmp_path / ".dockerignore").exists()

    def test_creates_server_py(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        assert (tmp_path / "server.py").exists()

    def test_creates_requirements_deploy(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        assert (tmp_path / "requirements_deploy.txt").exists()

    def test_server_py_is_valid_python(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        ast.parse((tmp_path / "server.py").read_text())

    def test_server_py_contains_blueprint_name(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        content = (tmp_path / "server.py").read_text()
        assert "basic-chatbot" in content

    def test_server_py_has_invoke_and_health_endpoints(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        content = (tmp_path / "server.py").read_text()
        assert "/invoke" in content
        assert "/health" in content

    def test_dockerfile_exposes_8080(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        content = (tmp_path / "Dockerfile").read_text()
        assert "8080" in content

    def test_requirements_deploy_includes_base(self, tmp_path):
        ir = load_ir("basic_chatbot.yml")
        DeployPackager().package(tmp_path, ir)
        content = (tmp_path / "requirements_deploy.txt").read_text()
        assert "-r requirements.txt" in content
        assert "fastapi" in content
        assert "uvicorn" in content


class TestDeployBlueprintModel:
    def test_deploy_section_loads(self):
        raw = load_blueprint_yaml(FIXTURES / "deploy_agent.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.deploy is not None
        assert spec.deploy.platform == "azure"
        assert spec.deploy.azure is not None
        assert spec.deploy.aws is not None
        assert spec.deploy.gcp is not None

    def test_azure_config_fields(self):
        raw = load_blueprint_yaml(FIXTURES / "deploy_agent.yml")
        spec = BlueprintSpec.model_validate(raw)
        az = spec.deploy.azure
        assert az.resource_group == "my-rg"
        assert az.acr_name == "myregistry"
        assert az.location == "westeurope"

    def test_gcp_config_fields(self):
        raw = load_blueprint_yaml(FIXTURES / "deploy_agent.yml")
        spec = BlueprintSpec.model_validate(raw)
        gcp = spec.deploy.gcp
        assert gcp.allow_unauthenticated is True
        assert gcp.region == "europe-west1"

    def test_deploy_section_optional(self):
        raw = load_blueprint_yaml(FIXTURES / "basic_chatbot.yml")
        spec = BlueprintSpec.model_validate(raw)
        assert spec.deploy is None
