"""Tests for cloud deployers — prerequisite checking with mocked subprocess."""

from unittest.mock import patch


from agent_blueprint.deployers.azure import AzureDeployer
from agent_blueprint.deployers.aws import AWSDeployer
from agent_blueprint.deployers.docker import DockerDeployer, PodmanDeployer
from agent_blueprint.deployers.gcp import GCPDeployer
from agent_blueprint.models.deploy import (
    AWSDeployConfig,
    AzureDeployConfig,
    DockerDeployConfig,
    GCPDeployConfig,
)


def _az_config():
    return AzureDeployConfig(
        resource_group="my-rg",
        acr_name="myregistry",
        container_app_env="my-env",
    )


def _aws_config():
    return AWSDeployConfig(ecr_repo="my-agent")


def _gcp_config():
    return GCPDeployConfig()


def _record_cmds(deployer):
    """Patch helper: collect every _cmd invocation's argv list."""
    calls: list[list[str]] = []

    def fake_cmd(cmd, *, dry_run=False, capture=False, input=None):
        calls.append(cmd)
        return None

    return calls, fake_cmd


class TestAzurePrerequisites:
    def test_all_prerequisites_met(self):
        deployer = AzureDeployer(_az_config(), "test-agent")
        with patch.object(deployer, "_probe", return_value=True):
            errors = deployer.check_prerequisites()
        assert errors == []

    def test_missing_az_cli(self):
        deployer = AzureDeployer(_az_config(), "test-agent")
        def probe(cmd):
            return "az" not in cmd[0]
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert any("Azure CLI" in e for e in errors)

    def test_docker_not_required(self):
        # The image is built remotely via `az acr build`, so a missing local
        # Docker/Podman is not a prerequisite error.
        deployer = AzureDeployer(_az_config(), "test-agent")
        def probe(cmd):
            return "docker" not in cmd[0]   # az present, docker absent
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert errors == []

    def test_deploy_builds_in_acr_not_locally(self, tmp_path):
        # No local `docker build`/`docker push`; image is built via `az acr build`.
        deployer = AzureDeployer(_az_config(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with (
            patch.object(deployer, "_cmd", side_effect=fake_cmd),
            patch.object(deployer, "_probe", return_value=False),
            patch.object(deployer, "_capture", return_value=""),
        ):
            deployer.deploy(tmp_path, {}, image_tag="v1")
        flat = [" ".join(c) for c in calls]
        assert any(c.startswith("az acr build") for c in flat)
        assert not any("docker" in c for c in flat)

    def test_not_logged_in(self):
        deployer = AzureDeployer(_az_config(), "test-agent")
        def probe(cmd):
            return "account" not in " ".join(cmd)
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert any("az login" in e for e in errors)

    def test_secrets_bound_via_containerapp_update(self, tmp_path):
        # Env vars are bound to secrets via `az containerapp update
        # --set-env-vars`. The `az containerapp env vars` group does not exist
        # (`containerapp env` is the managed environment), so it must not appear.
        deployer = AzureDeployer(_az_config(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with (
            patch.object(deployer, "_cmd", side_effect=fake_cmd),
            patch.object(deployer, "_probe", return_value=False),
            patch.object(deployer, "_capture", return_value=""),
        ):
            deployer.deploy(tmp_path, {"AZURE_OPENAI_API_KEY": "k"}, image_tag="v1")
        flat = [" ".join(c) for c in calls]
        assert any("containerapp secret set" in c for c in flat)
        assert any(
            "containerapp update" in c
            and "--set-env-vars" in c
            and "AZURE_OPENAI_API_KEY=secretref:azure-openai-api-key" in c
            for c in flat
        )
        assert not any("containerapp env vars" in c for c in flat)


class TestAWSPrerequisites:
    def test_all_prerequisites_met(self):
        deployer = AWSDeployer(_aws_config(), "test-agent")
        with patch.object(deployer, "_probe", return_value=True):
            errors = deployer.check_prerequisites()
        assert errors == []

    def test_missing_aws_cli(self):
        deployer = AWSDeployer(_aws_config(), "test-agent")
        def probe(cmd):
            return "aws" not in cmd[0]
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert any("AWS CLI" in e for e in errors)

    def test_no_credentials(self):
        deployer = AWSDeployer(_aws_config(), "test-agent")
        def probe(cmd):
            return "get-caller-identity" not in " ".join(cmd)
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert any("credentials" in e for e in errors)


class TestGCPPrerequisites:
    def test_all_prerequisites_met(self):
        deployer = GCPDeployer(_gcp_config(), "test-agent")
        with patch.object(deployer, "_probe", return_value=True):
            errors = deployer.check_prerequisites()
        assert errors == []

    def test_missing_gcloud(self):
        deployer = GCPDeployer(_gcp_config(), "test-agent")
        def probe(cmd):
            return "gcloud" not in cmd[0]
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert any("gcloud" in e for e in errors)

    def test_not_logged_in(self):
        deployer = GCPDeployer(_gcp_config(), "test-agent")
        def probe(cmd):
            return "print-identity-token" not in " ".join(cmd)
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert any("gcloud auth login" in e for e in errors)


class TestDeployResultShape:
    def test_deploy_result_dry_run_azure(self, tmp_path):
        deployer = AzureDeployer(_az_config(), "test-agent")
        with (
            patch.object(deployer, "_cmd", return_value=None),
            patch.object(deployer, "_probe", return_value=False),
            patch.object(deployer, "_capture", return_value=""),
        ):
            result = deployer.deploy(tmp_path, {}, image_tag="latest", dry_run=True)
        assert result.success is True

    def test_deploy_result_dry_run_gcp(self, tmp_path):
        deployer = GCPDeployer(_gcp_config(), "test-agent")
        with (
            patch.object(deployer, "_cmd", return_value=None),
            patch.object(deployer, "_probe", return_value=False),
            patch.object(deployer, "_capture", return_value=""),
        ):
            result = deployer.deploy(tmp_path, {}, image_tag="latest", dry_run=True)
        assert result.success is True


class TestBaseDeployerHelpers:
    """The subprocess helpers themselves, exercised with harmless real commands."""

    def _deployer(self):
        return DockerDeployer(DockerDeployConfig(), "test-agent")

    def test_cmd_dry_run_prints_but_does_not_execute(self, capsys):
        result = self._deployer()._cmd(["definitely-not-a-binary"], dry_run=True)
        assert result is None
        assert "$ definitely-not-a-binary" in capsys.readouterr().out

    def test_cmd_executes_and_returns_completed_process(self):
        import sys
        result = self._deployer()._cmd(
            [sys.executable, "-c", "print('ok')"], capture=True
        )
        assert result is not None
        assert result.stdout.strip() == "ok"

    def test_probe_true_on_success(self):
        import sys
        assert self._deployer()._probe([sys.executable, "-c", "pass"]) is True

    def test_probe_false_on_nonzero_exit(self):
        import sys
        assert self._deployer()._probe(
            [sys.executable, "-c", "raise SystemExit(1)"]
        ) is False

    def test_probe_false_on_missing_binary(self):
        assert self._deployer()._probe(["definitely-not-a-binary-xyz"]) is False

    def test_capture_returns_stripped_stdout(self):
        import sys
        out = self._deployer()._capture([sys.executable, "-c", "print('  value  ')"])
        assert out == "value"

    def test_capture_empty_on_failure(self):
        import sys
        assert self._deployer()._capture(
            [sys.executable, "-c", "raise SystemExit(1)"]
        ) == ""
        assert self._deployer()._capture(["definitely-not-a-binary-xyz"]) == ""


class TestContainerPrerequisites:
    def test_docker_available(self):
        deployer = DockerDeployer(DockerDeployConfig(), "test-agent")
        with patch.object(deployer, "_probe", return_value=True):
            assert deployer.check_prerequisites() == []

    def test_docker_unavailable(self):
        deployer = DockerDeployer(DockerDeployConfig(), "test-agent")
        with patch.object(deployer, "_probe", return_value=False):
            errors = deployer.check_prerequisites()
        assert any("'docker'" in e for e in errors)

    def test_podman_unavailable(self):
        deployer = PodmanDeployer(DockerDeployConfig(), "test-agent")
        with patch.object(deployer, "_probe", return_value=False):
            errors = deployer.check_prerequisites()
        assert any("'podman'" in e for e in errors)


class TestContainerDeploy:
    def test_build_rm_run_sequence(self, tmp_path):
        deployer = DockerDeployer(DockerDeployConfig(), "My Agent")
        calls, fake_cmd = _record_cmds(deployer)
        with patch.object(deployer, "_cmd", side_effect=fake_cmd):
            result = deployer.deploy(tmp_path, {}, image_tag="v1")

        assert [c[:2] for c in calls] == [
            ["docker", "build"],
            ["docker", "rm"],
            ["docker", "run"],
        ]
        # Blueprint name is slugified for both image and container name
        assert calls[0][2:4] == ["-t", "my-agent:v1"]
        assert calls[1] == ["docker", "rm", "-f", "my-agent"]
        assert result.success is True
        assert result.url == "http://localhost:8080"

    def test_container_name_and_port_overrides(self, tmp_path):
        cfg = DockerDeployConfig(container_name="custom", host_port=9000)
        deployer = DockerDeployer(cfg, "ignored-name")
        calls, fake_cmd = _record_cmds(deployer)
        with patch.object(deployer, "_cmd", side_effect=fake_cmd):
            result = deployer.deploy(tmp_path, {}, image_tag="latest")

        run_cmd = calls[2]
        assert "custom" in run_cmd
        assert "9000:8080" in run_cmd
        assert result.url == "http://localhost:9000"

    def test_platform_flag_passed_to_build(self, tmp_path):
        cfg = DockerDeployConfig(platform="linux/amd64")
        deployer = DockerDeployer(cfg, "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with patch.object(deployer, "_cmd", side_effect=fake_cmd):
            deployer.deploy(tmp_path, {}, image_tag="latest")

        build_cmd = calls[0]
        idx = build_cmd.index("--platform")
        assert build_cmd[idx + 1] == "linux/amd64"

    def test_secrets_become_env_flags(self, tmp_path):
        deployer = DockerDeployer(DockerDeployConfig(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with patch.object(deployer, "_cmd", side_effect=fake_cmd):
            deployer.deploy(tmp_path, {"OPENAI_API_KEY": "sk-test"}, image_tag="latest")

        run_cmd = calls[2]
        assert "OPENAI_API_KEY=sk-test" in run_cmd

    def test_ollama_url_rewritten_for_docker(self, tmp_path):
        deployer = DockerDeployer(DockerDeployConfig(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with patch.object(deployer, "_cmd", side_effect=fake_cmd):
            deployer.deploy(tmp_path, {}, image_tag="latest")

        run_cmd = calls[2]
        assert "OLLAMA_BASE_URL=http://host.docker.internal:11434" in run_cmd

    def test_ollama_url_rewritten_for_podman(self, tmp_path):
        deployer = PodmanDeployer(DockerDeployConfig(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with patch.object(deployer, "_cmd", side_effect=fake_cmd):
            deployer.deploy(tmp_path, {}, image_tag="latest")

        run_cmd = calls[2]
        assert run_cmd[0] == "podman"
        assert "OLLAMA_BASE_URL=http://host.containers.internal:11434" in run_cmd

    def test_ollama_rewrite_skipped_when_user_provided(self, tmp_path):
        deployer = DockerDeployer(DockerDeployConfig(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with patch.object(deployer, "_cmd", side_effect=fake_cmd):
            deployer.deploy(
                tmp_path, {"OLLAMA_BASE_URL": "http://my-host:11434"}, image_tag="latest"
            )

        run_cmd = calls[2]
        assert "OLLAMA_BASE_URL=http://my-host:11434" in run_cmd
        assert "OLLAMA_BASE_URL=http://host.docker.internal:11434" not in run_cmd

    def test_ollama_rewrite_skipped_on_host_network(self, tmp_path):
        cfg = DockerDeployConfig(network="host")
        deployer = DockerDeployer(cfg, "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with patch.object(deployer, "_cmd", side_effect=fake_cmd):
            deployer.deploy(tmp_path, {}, image_tag="latest")

        run_cmd = calls[2]
        assert not any(str(part).startswith("OLLAMA_BASE_URL=") for part in run_cmd)
        idx = run_cmd.index("--network")
        assert run_cmd[idx + 1] == "host"


class TestAWSDeploy:
    def test_dry_run_creates_repo_and_service(self, tmp_path):
        deployer = AWSDeployer(_aws_config(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)
        with (
            patch.object(deployer, "_cmd", side_effect=fake_cmd),
            patch.object(deployer, "_probe", return_value=False),
            patch.object(deployer, "_capture", return_value="123456789012"),
        ):
            result = deployer.deploy(tmp_path, {}, image_tag="latest", dry_run=True)

        joined = [" ".join(c) for c in calls]
        assert any("ecr create-repository" in c for c in joined)
        assert any("apprunner create-service" in c for c in joined)
        # Account id resolved via _capture lands in the image URI
        assert any("123456789012.dkr.ecr.us-east-1.amazonaws.com/my-agent:latest" in c
                   for c in joined)
        assert result.success is True
        assert result.url is None

    def test_existing_service_gets_updated(self, tmp_path):
        deployer = AWSDeployer(_aws_config(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)

        def fake_capture(cmd):
            text = " ".join(cmd)
            if "get-caller-identity" in text:
                return "123456789012"
            if "get-login-password" in text:
                return "ecr-password"
            if "ServiceArn" in text:
                return "arn:aws:apprunner:us-east-1:123456789012:service/test-agent/abc"
            if "ServiceUrl" in text:
                return "abc.us-east-1.awsapprunner.com"
            return ""

        with (
            patch.object(deployer, "_cmd", side_effect=fake_cmd),
            patch.object(deployer, "_probe", return_value=True),
            patch.object(deployer, "_capture", side_effect=fake_capture),
        ):
            result = deployer.deploy(tmp_path, {"K": "v"}, image_tag="v2")

        joined = [" ".join(c) for c in calls]
        assert any("apprunner update-service" in c for c in joined)
        assert not any("apprunner create-service" in c for c in joined)
        assert not any("ecr create-repository" in c for c in joined)  # repo probe was True
        assert any("docker push" in c for c in joined)
        assert result.url == "https://abc.us-east-1.awsapprunner.com"

    def test_new_service_gets_created(self, tmp_path):
        deployer = AWSDeployer(_aws_config(), "test-agent")
        calls, fake_cmd = _record_cmds(deployer)

        def fake_capture(cmd):
            text = " ".join(cmd)
            if "get-caller-identity" in text:
                return "123456789012"
            if "get-login-password" in text:
                return "ecr-password"
            return ""  # no existing ARN, no URL yet

        with (
            patch.object(deployer, "_cmd", side_effect=fake_cmd),
            patch.object(deployer, "_probe", return_value=True),
            patch.object(deployer, "_capture", side_effect=fake_capture),
        ):
            result = deployer.deploy(tmp_path, {}, image_tag="latest")

        joined = [" ".join(c) for c in calls]
        assert any("apprunner create-service" in c for c in joined)
        assert not any("apprunner update-service" in c for c in joined)
        assert result.success is True
        assert result.url is None

    def test_service_name_defaults_to_slugified_blueprint_name(self, tmp_path):
        deployer = AWSDeployer(_aws_config(), "My Cool Agent")
        calls, fake_cmd = _record_cmds(deployer)
        with (
            patch.object(deployer, "_cmd", side_effect=fake_cmd),
            patch.object(deployer, "_probe", return_value=False),
            patch.object(deployer, "_capture", return_value=""),
        ):
            deployer.deploy(tmp_path, {}, image_tag="latest", dry_run=True)

        joined = [" ".join(c) for c in calls]
        assert any("--service-name my-cool-agent" in c for c in joined)
