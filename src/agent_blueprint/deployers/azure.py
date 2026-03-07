"""Azure Container Apps deployer."""

from pathlib import Path

from agent_blueprint.deployers.base import BaseDeployer, DeployResult
from agent_blueprint.models.deploy import AzureDeployConfig


class AzureDeployer(BaseDeployer):
    """Deploy to Azure Container Apps via ACR."""

    def __init__(self, config: AzureDeployConfig, blueprint_name: str) -> None:
        self._cfg = config
        self._app_name = config.app_name or blueprint_name.replace(" ", "-").lower()

    # ------------------------------------------------------------------
    # Prerequisites
    # ------------------------------------------------------------------

    def check_prerequisites(self) -> list[str]:
        errors: list[str] = []

        if not self._probe(["az", "--version"]):
            errors.append(
                "Azure CLI not found. Install: https://aka.ms/installazurecli"
            )
        if not self._probe(["docker", "info"]):
            errors.append("Docker daemon is not running or docker CLI not found.")
        if not self._probe(["az", "account", "show"]):
            errors.append("Not logged in to Azure. Run: az login")

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
        acr_server = f"{cfg.acr_name}.azurecr.io"
        full_image = f"{acr_server}/{self._app_name}:{image_tag}"

        print(f"\n→ [Azure] Deploying '{self._app_name}' to Container Apps")

        # 1. ACR login
        self._cmd(["az", "acr", "login", "--name", cfg.acr_name], dry_run=dry_run)

        # 2. Build image
        self._cmd(
            ["docker", "build", "-t", full_image, str(code_dir)],
            dry_run=dry_run,
        )

        # 3. Push image
        self._cmd(["docker", "push", full_image], dry_run=dry_run)

        # 4. Create or update Container App
        app_exists = self._probe([
            "az", "containerapp", "show",
            "--name", self._app_name,
            "--resource-group", cfg.resource_group,
        ])

        if app_exists:
            print("  (updating existing Container App)")
            self._cmd([
                "az", "containerapp", "update",
                "--name", self._app_name,
                "--resource-group", cfg.resource_group,
                "--image", full_image,
            ], dry_run=dry_run)
        else:
            print("  (creating new Container App)")
            self._cmd([
                "az", "containerapp", "create",
                "--name", self._app_name,
                "--resource-group", cfg.resource_group,
                "--environment", cfg.container_app_env,
                "--image", full_image,
                "--registry-server", acr_server,
                "--ingress", "external",
                "--target-port", "8080",
                "--min-replicas", str(cfg.min_replicas),
                "--max-replicas", str(cfg.max_replicas),
            ], dry_run=dry_run)

        # 5. Set secrets
        if secrets:
            secret_pairs = [f"{k.lower().replace('_', '-')}={v}" for k, v in secrets.items()]
            self._cmd([
                "az", "containerapp", "secret", "set",
                "--name", self._app_name,
                "--resource-group", cfg.resource_group,
                "--secrets", *secret_pairs,
            ], dry_run=dry_run)

            env_pairs = [
                f"{k}=secretref:{k.lower().replace('_', '-')}"
                for k in secrets
            ]
            self._cmd([
                "az", "containerapp", "env", "vars", "set",
                "--name", self._app_name,
                "--resource-group", cfg.resource_group,
                "--container-name", self._app_name,
                "--env-vars", *env_pairs,
            ], dry_run=dry_run)

        # 6. Get URL
        url = ""
        if not dry_run:
            fqdn = self._capture([
                "az", "containerapp", "show",
                "--name", self._app_name,
                "--resource-group", cfg.resource_group,
                "--query", "properties.configuration.ingress.fqdn",
                "-o", "tsv",
            ])
            if fqdn:
                url = f"https://{fqdn}"

        return DeployResult(
            success=True,
            url=url or None,
            message=f"Deployed to Azure Container Apps as '{self._app_name}'",
        )
