"""GCP Cloud Run deployer."""

from pathlib import Path

from agent_blueprint.deployers.base import BaseDeployer, DeployResult
from agent_blueprint.models.deploy import GCPDeployConfig


class GCPDeployer(BaseDeployer):
    """Deploy to GCP Cloud Run via Artifact Registry."""

    def __init__(self, config: GCPDeployConfig, blueprint_name: str) -> None:
        self._cfg = config
        self._service_name = config.service_name or blueprint_name.replace(" ", "-").lower()

    # ------------------------------------------------------------------
    # Prerequisites
    # ------------------------------------------------------------------

    def check_prerequisites(self) -> list[str]:
        errors: list[str] = []

        if not self._probe(["gcloud", "--version"]):
            errors.append(
                "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
            )
        if not self._probe(["docker", "info"]):
            errors.append("Docker daemon is not running or docker CLI not found.")
        if not self._probe(["gcloud", "auth", "print-identity-token"]):
            errors.append(
                "Not logged in to GCP. Run: gcloud auth login"
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
        import os

        project = os.environ.get(cfg.project_env, f"${{{cfg.project_env}}}")
        registry_host = f"{cfg.region}-docker.pkg.dev"
        full_image = (
            f"{registry_host}/{project}/{cfg.artifact_repo}"
            f"/{self._service_name}:{image_tag}"
        )

        print(f"\n→ [GCP] Deploying '{self._service_name}' to Cloud Run")

        # 1. Configure docker auth for Artifact Registry
        self._cmd(
            ["gcloud", "auth", "configure-docker", registry_host, "--quiet"],
            dry_run=dry_run,
        )

        # 2. Ensure Artifact Registry repo exists
        repo_exists = self._probe([
            "gcloud", "artifacts", "repositories", "describe",
            cfg.artifact_repo,
            "--location", cfg.region,
            "--project", project,
        ])
        if not repo_exists:
            print("  (creating Artifact Registry repository)")
            self._cmd([
                "gcloud", "artifacts", "repositories", "create",
                cfg.artifact_repo,
                "--repository-format", "docker",
                "--location", cfg.region,
                "--project", project,
                "--quiet",
            ], dry_run=dry_run)

        # 3. Build + push
        self._cmd(["docker", "build", "-t", full_image, str(code_dir)], dry_run=dry_run)
        self._cmd(["docker", "push", full_image], dry_run=dry_run)

        # 4. Deploy to Cloud Run
        deploy_cmd = [
            "gcloud", "run", "deploy", self._service_name,
            "--image", full_image,
            "--region", cfg.region,
            "--platform", "managed",
            "--port", "8080",
            "--project", project,
            "--quiet",
        ]

        if secrets:
            env_str = ",".join(f"{k}={v}" for k, v in secrets.items())
            deploy_cmd += ["--set-env-vars", env_str]

        if cfg.allow_unauthenticated:
            deploy_cmd.append("--allow-unauthenticated")
        else:
            deploy_cmd.append("--no-allow-unauthenticated")

        self._cmd(deploy_cmd, dry_run=dry_run)

        # 5. Get URL
        url = ""
        if not dry_run:
            url = self._capture([
                "gcloud", "run", "services", "describe", self._service_name,
                "--region", cfg.region,
                "--project", project,
                "--format", "value(status.url)",
            ])

        return DeployResult(
            success=True,
            url=url or None,
            message=f"Deployed to GCP Cloud Run as '{self._service_name}'",
        )
