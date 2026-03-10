"""Local container deployer — shared base for Docker and Podman."""

from pathlib import Path

from agent_blueprint.deployers.base import BaseDeployer, DeployResult
from agent_blueprint.models.deploy import DockerDeployConfig


class _ContainerDeployer(BaseDeployer):
    """Shared implementation for Docker-compatible runtimes (docker, podman)."""

    #: CLI executable name — overridden by subclasses
    _runtime: str = "docker"
    #: Host alias that resolves to the host machine from inside a container
    _ollama_host: str = "host.docker.internal"

    def __init__(self, config: DockerDeployConfig, blueprint_name: str) -> None:
        self._cfg = config
        self._name = config.container_name or blueprint_name.replace(" ", "-").lower()

    def check_prerequisites(self) -> list[str]:
        issues: list[str] = []
        if not self._probe([self._runtime, "info"]):
            issues.append(
                f"'{self._runtime}' is not available or its daemon/machine is not running"
            )
        return issues

    def deploy(
        self,
        code_dir: Path,
        secrets: dict[str, str],
        *,
        image_tag: str = "latest",
        dry_run: bool = False,
    ) -> DeployResult:
        rt = self._runtime
        image = f"{self._name}:{image_tag}"
        port = self._cfg.host_port

        # 1. Build image
        build_cmd = [rt, "build", "-t", image]
        if self._cfg.platform:
            build_cmd += ["--platform", self._cfg.platform]
        build_cmd.append(str(code_dir))
        self._cmd(build_cmd, dry_run=dry_run)

        # 2. Remove existing container (ignore errors)
        self._cmd([rt, "rm", "-f", self._name], dry_run=dry_run)

        # 3. Build env flags from secrets
        env_flags: list[str] = []
        for k, v in secrets.items():
            env_flags += ["-e", f"{k}={v}"]

        # 4. Fix Ollama URL: localhost → <runtime host alias>.
        #    Skip when the user already provided OLLAMA_BASE_URL or --network=host is set.
        if not secrets.get("OLLAMA_BASE_URL") and (self._cfg.network or "") != "host":
            env_flags += ["-e", f"OLLAMA_BASE_URL=http://{self._ollama_host}:11434"]

        # 5. Run container
        run_cmd = [rt, "run", "-d", "--name", self._name, "-p", f"{port}:8080"]
        if self._cfg.network:
            run_cmd += ["--network", self._cfg.network]
        run_cmd += env_flags
        run_cmd.append(image)
        self._cmd(run_cmd, dry_run=dry_run)

        return DeployResult(
            success=True,
            url=f"http://localhost:{port}",
            message="Container started",
        )


class DockerDeployer(_ContainerDeployer):
    """Runs the agent as a local Docker container."""

    _runtime = "docker"
    _ollama_host = "host.docker.internal"


class PodmanDeployer(_ContainerDeployer):
    """Runs the agent as a local Podman container."""

    _runtime = "podman"
    _ollama_host = "host.containers.internal"
