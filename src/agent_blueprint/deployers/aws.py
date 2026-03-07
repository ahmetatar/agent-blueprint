"""AWS App Runner deployer (via ECR)."""

import json
from pathlib import Path

from agent_blueprint.deployers.base import BaseDeployer, DeployResult
from agent_blueprint.models.deploy import AWSDeployConfig


class AWSDeployer(BaseDeployer):
    """Deploy to AWS App Runner with an ECR container image."""

    def __init__(self, config: AWSDeployConfig, blueprint_name: str) -> None:
        self._cfg = config
        self._service_name = config.service_name or blueprint_name.replace(" ", "-").lower()

    # ------------------------------------------------------------------
    # Prerequisites
    # ------------------------------------------------------------------

    def check_prerequisites(self) -> list[str]:
        errors: list[str] = []

        if not self._probe(["aws", "--version"]):
            errors.append(
                "AWS CLI not found. Install: https://aws.amazon.com/cli/"
            )
        if not self._probe(["docker", "info"]):
            errors.append("Docker daemon is not running or docker CLI not found.")
        if not self._probe(["aws", "sts", "get-caller-identity"]):
            errors.append(
                "AWS credentials not configured. Run: aws configure"
            )

        return errors

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy(
        self,
        code_dir: Path,
        secrets: dict[str, str],
        *,
        image_tag: str,
        dry_run: bool = False,
    ) -> DeployResult:
        cfg = self._cfg

        # Resolve AWS account ID
        account_id = self._capture(
            ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"]
        ) or "<account-id>"

        ecr_uri = f"{account_id}.dkr.ecr.{cfg.region}.amazonaws.com"
        full_image = f"{ecr_uri}/{cfg.ecr_repo}:{image_tag}"

        print(f"\n→ [AWS] Deploying '{self._service_name}' to App Runner")

        # 1. Ensure ECR repo exists
        repo_exists = self._probe([
            "aws", "ecr", "describe-repositories",
            "--repository-names", cfg.ecr_repo,
            "--region", cfg.region,
        ])
        if not repo_exists:
            print("  (creating ECR repository)")
            self._cmd([
                "aws", "ecr", "create-repository",
                "--repository-name", cfg.ecr_repo,
                "--region", cfg.region,
            ], dry_run=dry_run)

        # 2. ECR login
        if not dry_run:
            password = self._capture([
                "aws", "ecr", "get-login-password", "--region", cfg.region
            ])
            self._cmd(
                ["docker", "login", "--username", "AWS", "--password-stdin", ecr_uri],
                dry_run=False,
                input=password,
            )
        else:
            print(f"  $ aws ecr get-login-password | docker login --username AWS {ecr_uri}")

        # 3. Build + tag + push
        self._cmd(["docker", "build", "-t", full_image, str(code_dir)], dry_run=dry_run)
        self._cmd(["docker", "push", full_image], dry_run=dry_run)

        # 4. Create or update App Runner service
        env_vars = {k: v for k, v in secrets.items()}
        source_config = {
            "ImageRepository": {
                "ImageIdentifier": full_image,
                "ImageRepositoryType": "ECR",
                "ImageConfiguration": {
                    "Port": "8080",
                    "RuntimeEnvironmentVariables": env_vars,
                },
            },
            "AutoDeploymentsEnabled": False,
        }

        service_exists = self._probe([
            "aws", "apprunner", "list-services",
            "--region", cfg.region,
        ])

        # Check if service with our name exists
        existing_arn = ""
        if service_exists and not dry_run:
            raw = self._capture([
                "aws", "apprunner", "list-services",
                "--region", cfg.region,
                "--query",
                f"ServiceSummaryList[?ServiceName=='{self._service_name}'].ServiceArn",
                "--output", "text",
            ])
            existing_arn = raw.strip()

        if existing_arn:
            print("  (updating existing App Runner service)")
            self._cmd([
                "aws", "apprunner", "update-service",
                "--service-arn", existing_arn,
                "--source-configuration", json.dumps(source_config),
                "--region", cfg.region,
            ], dry_run=dry_run)
        else:
            print("  (creating new App Runner service)")
            instance_config = {"Cpu": "1024", "Memory": "2048"}
            self._cmd([
                "aws", "apprunner", "create-service",
                "--service-name", self._service_name,
                "--source-configuration", json.dumps(source_config),
                "--instance-configuration", json.dumps(instance_config),
                "--region", cfg.region,
            ], dry_run=dry_run)

        # 5. Get service URL
        url = ""
        if not dry_run:
            url_raw = self._capture([
                "aws", "apprunner", "list-services",
                "--region", cfg.region,
                "--query",
                f"ServiceSummaryList[?ServiceName=='{self._service_name}'].ServiceUrl",
                "--output", "text",
            ])
            if url_raw:
                url = f"https://{url_raw.strip()}"

        return DeployResult(
            success=True,
            url=url or None,
            message=f"Deployed to AWS App Runner as '{self._service_name}'",
        )
