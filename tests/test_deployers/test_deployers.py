"""Tests for cloud deployers — prerequisite checking with mocked subprocess."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

import pytest

from agent_blueprint.deployers.azure import AzureDeployer
from agent_blueprint.deployers.aws import AWSDeployer
from agent_blueprint.deployers.gcp import GCPDeployer
from agent_blueprint.models.deploy import AzureDeployConfig, AWSDeployConfig, GCPDeployConfig


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

    def test_missing_docker(self):
        deployer = AzureDeployer(_az_config(), "test-agent")
        def probe(cmd):
            return "docker" not in cmd[0]
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert any("Docker" in e for e in errors)

    def test_not_logged_in(self):
        deployer = AzureDeployer(_az_config(), "test-agent")
        def probe(cmd):
            return "account" not in " ".join(cmd)
        with patch.object(deployer, "_probe", side_effect=probe):
            errors = deployer.check_prerequisites()
        assert any("az login" in e for e in errors)


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
