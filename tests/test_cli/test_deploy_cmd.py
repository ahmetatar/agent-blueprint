"""Tests for abp deploy command — platform resolution, prereqs, and deploy flow."""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from agent_blueprint.cli.app import app
from agent_blueprint.deployers.base import DeployResult


runner = CliRunner()

_BASE_BLUEPRINT = """\
blueprint:
  name: "deploy-test"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
model_providers:
  openai_main:
    provider: openai
    api_key_env: OPENAI_API_KEY
agents:
  assistant:
    model: "gpt-4o"
    model_provider: openai_main
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
"""


def _write_blueprint(tmp_path: Path, content: str = _BASE_BLUEPRINT) -> Path:
    path = tmp_path / "agent.yml"
    path.write_text(content, encoding="utf-8")
    return path


def _flat(result) -> str:
    """Unwrap Rich line-wrapping for stable substring assertions."""
    return " ".join(result.output.split())


class TestDeployValidation:
    def test_invalid_blueprint_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path, "blueprint:\n  name: 1\nagents: []\n")
        result = runner.invoke(app, ["deploy", str(path), "--platform", "docker"])
        assert result.exit_code == 1
        assert "Validation error" in _flat(result)

    def test_no_platform_anywhere_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path)
        result = runner.invoke(app, ["deploy", str(path)])
        assert result.exit_code == 1
        assert "No platform specified" in _flat(result)

    def test_unknown_platform_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path)
        result = runner.invoke(app, ["deploy", str(path), "--platform", "heroku"])
        assert result.exit_code == 1
        assert "Unknown platform" in _flat(result)

    def test_cloud_platform_without_config_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path)
        result = runner.invoke(app, ["deploy", str(path), "--platform", "azure"])
        assert result.exit_code == 1
        assert "No deploy.azure config" in _flat(result)

    def test_non_langgraph_target_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path)
        with patch(
            "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
            return_value=[],
        ):
            result = runner.invoke(
                app, ["deploy", str(path), "--platform", "docker", "--target", "crewai"]
            )
        assert result.exit_code == 1
        assert "only supports" in _flat(result)


class TestDeployPlatformResolution:
    def test_platform_read_from_blueprint_deploy_section(self, tmp_path):
        content = _BASE_BLUEPRINT + (
            "deploy:\n"
            "  platform: docker\n"
            "  docker:\n"
            "    host_port: 9999\n"
        )
        path = _write_blueprint(tmp_path, content)
        with (
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
                return_value=[],
            ),
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.deploy",
                return_value=DeployResult(success=True, url="http://localhost:9999"),
            ) as mock_deploy,
        ):
            result = runner.invoke(app, ["deploy", str(path)])
        assert result.exit_code == 0
        assert mock_deploy.called
        assert "http://localhost:9999" in _flat(result)

    def test_cli_platform_overrides_blueprint(self, tmp_path):
        content = _BASE_BLUEPRINT + "deploy:\n  platform: azure\n"
        path = _write_blueprint(tmp_path, content)
        # CLI says podman even though blueprint says azure (and azure has no config)
        with (
            patch(
                "agent_blueprint.deployers.docker.PodmanDeployer.check_prerequisites",
                return_value=[],
            ),
            patch(
                "agent_blueprint.deployers.docker.PodmanDeployer.deploy",
                return_value=DeployResult(success=True),
            ) as mock_deploy,
        ):
            result = runner.invoke(app, ["deploy", str(path), "--platform", "podman"])
        assert result.exit_code == 0
        assert mock_deploy.called

    def test_docker_works_without_deploy_config(self, tmp_path):
        path = _write_blueprint(tmp_path)
        with (
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
                return_value=[],
            ),
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.deploy",
                return_value=DeployResult(success=True, url="http://localhost:8080"),
            ),
        ):
            result = runner.invoke(app, ["deploy", str(path), "--platform", "docker"])
        assert result.exit_code == 0
        assert "Deployed" in _flat(result)


class TestDeployExecution:
    def test_prerequisite_failure_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path)
        with patch(
            "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
            return_value=["'docker' is not available"],
        ):
            result = runner.invoke(app, ["deploy", str(path), "--platform", "docker"])
        assert result.exit_code == 1
        assert "Prerequisites not met" in _flat(result)
        assert "'docker' is not available" in _flat(result)

    def test_failed_deploy_exits_1(self, tmp_path):
        path = _write_blueprint(tmp_path)
        with (
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
                return_value=[],
            ),
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.deploy",
                return_value=DeployResult(success=False, message="image build failed"),
            ),
        ):
            result = runner.invoke(app, ["deploy", str(path), "--platform", "docker"])
        assert result.exit_code == 1
        assert "Deploy failed" in _flat(result)
        assert "image build failed" in _flat(result)

    def test_deployer_receives_generated_package_and_secrets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        path = _write_blueprint(tmp_path)

        captured: dict = {}

        def fake_deploy(self, code_dir, secrets, *, image_tag, dry_run=False):
            captured["files"] = sorted(p.name for p in Path(code_dir).iterdir())
            captured["secrets"] = secrets
            captured["image_tag"] = image_tag
            return DeployResult(success=True)

        with (
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
                return_value=[],
            ),
            patch("agent_blueprint.deployers.docker.DockerDeployer.deploy", fake_deploy),
        ):
            result = runner.invoke(
                app, ["deploy", str(path), "--platform", "docker", "--image-tag", "v3"]
            )

        assert result.exit_code == 0
        # Generated code + deploy packaging are both present
        assert "graph.py" in captured["files"]
        assert "Dockerfile" in captured["files"]
        assert "server.py" in captured["files"]
        assert captured["secrets"] == {"OPENAI_API_KEY": "sk-from-env"}
        assert captured["image_tag"] == "v3"

    def test_env_option_overrides_environment_secret(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        path = _write_blueprint(tmp_path)

        captured: dict = {}

        def fake_deploy(self, code_dir, secrets, *, image_tag, dry_run=False):
            captured["secrets"] = secrets
            return DeployResult(success=True)

        with (
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
                return_value=[],
            ),
            patch("agent_blueprint.deployers.docker.DockerDeployer.deploy", fake_deploy),
        ):
            result = runner.invoke(
                app,
                [
                    "deploy", str(path), "--platform", "docker",
                    "--env", "OPENAI_API_KEY=sk-from-flag",
                ],
            )

        assert result.exit_code == 0
        assert captured["secrets"] == {"OPENAI_API_KEY": "sk-from-flag"}

    def test_missing_secret_warns_but_continues(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        path = _write_blueprint(tmp_path)
        with (
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
                return_value=[],
            ),
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.deploy",
                return_value=DeployResult(success=True),
            ),
        ):
            result = runner.invoke(app, ["deploy", str(path), "--platform", "docker"])
        assert result.exit_code == 0
        assert "OPENAI_API_KEY" in _flat(result)
        assert "not found in environment" in _flat(result)

    def test_dry_run_prints_package_contents(self, tmp_path):
        path = _write_blueprint(tmp_path)
        with (
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.check_prerequisites",
                return_value=[],
            ),
            patch(
                "agent_blueprint.deployers.docker.DockerDeployer.deploy",
                return_value=DeployResult(success=True),
            ),
        ):
            result = runner.invoke(
                app, ["deploy", str(path), "--platform", "docker", "--dry-run"]
            )
        assert result.exit_code == 0
        flat = _flat(result)
        assert "Dry run" in flat
        assert "Dockerfile" in flat
