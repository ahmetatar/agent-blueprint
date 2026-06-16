"""SandboxRunner — generate to a temp dir and execute inside a container."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from agent_blueprint.deployers.secrets import _PROVIDER_ENV_KEYS, collect_required_secrets
from agent_blueprint.exceptions import SandboxError
from agent_blueprint.ir.compiler import AgentGraph
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.run import SandboxConfig, SandboxEngine, SandboxNetwork
from agent_blueprint.runners.local import LocalRunner

#: Host alias that resolves to the host machine from inside a container
_OLLAMA_HOSTS = {
    "docker": "host.docker.internal",
    "podman": "host.containers.internal",
}

#: Trace output path inside the container (the temp dir is mounted there)
_CONTAINER_OUT = "/abp-out"


def engine_available(runtime: str) -> bool:
    """Return True if the container runtime exists and its daemon/machine responds."""
    if shutil.which(runtime) is None:
        return False
    try:
        subprocess.run([runtime, "info"], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def resolve_engine(requested: SandboxEngine) -> str:
    """Resolve the effective container engine.

    `auto` probes podman first (rootless, daemonless), then docker.
    """
    if requested != SandboxEngine.auto:
        if not engine_available(requested.value):
            raise SandboxError(
                f"sandbox engine '{requested.value}' is not available "
                f"or its daemon/machine is not running"
            )
        return str(requested.value)
    for runtime in ("podman", "docker"):
        if engine_available(runtime):
            return runtime
    raise SandboxError(
        "no container engine found: install podman or docker, "
        "or run without --sandbox"
    )


class SandboxRunner(LocalRunner):
    """Generates a blueprint, builds a container image, and runs it isolated.

    Inherits the generate → execute flow from LocalRunner; only the execute
    step is replaced by an image build + `<engine> run --rm`. Dependencies
    are baked into the image, so the host pip install step is a no-op.
    """

    def __init__(
        self,
        ir: AgentGraph,
        spec: BlueprintSpec,
        config: SandboxConfig,
        thread_id: str = "default",
        engine: str | None = None,
    ) -> None:
        super().__init__(ir, thread_id=thread_id)
        self._spec = spec
        self._cfg = config
        self._engine = engine or resolve_engine(config.engine)

    @property
    def engine(self) -> str:
        return self._engine

    @property
    def image(self) -> str:
        slug = self._ir.name.replace(" ", "-").lower()
        return f"abp-run-{slug}:latest"

    # ------------------------------------------------------------------
    # LocalRunner overrides
    # ------------------------------------------------------------------

    def _install_deps(self) -> int:
        return 0  # dependencies are installed at image build time

    def _execute(
        self,
        *,
        user_input: str | None,
        env_file: Path | None,
        extra_env: dict[str, str] | None = None,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert self._tempdir is not None

        self._write_dockerfile()
        build = self._build_image(capture_output=capture_output)
        if build.returncode != 0:
            return build

        cmd = self.run_command(
            user_input=user_input, env_file=env_file, extra_env=extra_env
        )
        return subprocess.run(
            cmd,
            check=False,
            capture_output=capture_output,
            text=True,
        )

    # ------------------------------------------------------------------
    # Container helpers
    # ------------------------------------------------------------------

    def _write_dockerfile(self) -> None:
        env = Environment(
            loader=PackageLoader("agent_blueprint", "templates/sandbox"),
            autoescape=select_autoescape([]),
            keep_trailing_newline=True,
        )
        content = env.get_template("Dockerfile.j2").render(image=self._cfg.image)
        assert self._tempdir is not None
        (self._tempdir / "Dockerfile").write_text(content, encoding="utf-8")

    def _build_image(self, *, capture_output: bool) -> subprocess.CompletedProcess[str]:
        assert self._tempdir is not None
        if not capture_output:
            print(
                f"→ Building sandbox image {self.image} ({self._engine})…", flush=True
            )
        return subprocess.run(
            [self._engine, "build", "-t", self.image, str(self._tempdir)],
            check=False,
            capture_output=capture_output,
            text=True,
        )

    def run_command(
        self,
        *,
        user_input: str | None,
        env_file: Path | None,
        extra_env: dict[str, str] | None = None,
    ) -> list[str]:
        """Build the full `<engine> run` command line."""
        assert self._tempdir is not None
        cmd = [self._engine, "run", "--rm"]

        if user_input is None:
            cmd.append("-i")  # interactive REPL needs stdin
            if sys.stdin.isatty():
                cmd.append("-t")

        cmd += ["--network", self._cfg.network.value]
        if self._cfg.memory:
            cmd += ["--memory", self._cfg.memory]
        if self._cfg.cpus is not None:
            cmd += ["--cpus", str(self._cfg.cpus)]

        # Mount the temp dir so the trace file lands back on the host
        mount = f"{self._tempdir}:{_CONTAINER_OUT}"
        if self._engine == "podman":
            mount += ":Z"  # SELinux relabel; harmless elsewhere
        cmd += ["-v", mount]

        for key, value in self.container_env(env_file, extra_env=extra_env).items():
            cmd += ["-e", f"{key}={value}"]

        cmd.append(self.image)
        if user_input is not None:
            cmd.append(user_input)
        return cmd

    def container_env(
        self,
        env_file: Path | None,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Allowlist-based env for the container.

        Forwards only the blueprint's required secrets plus the declarative
        `env_passthrough` list — the host environment is not inherited.
        Host env wins over the .env file, matching LocalRunner.
        """
        file_vals = _parse_env_file(env_file) if env_file else {}
        names = (
            collect_required_secrets(self._spec)
            | self._provider_env_keys()
            | set(self._cfg.env_passthrough)
        )

        env: dict[str, str] = {}
        missing: list[str] = []
        for name in sorted(names):
            value = os.environ.get(name) or file_vals.get(name)
            if value:
                env[name] = value
            else:
                missing.append(name)
        if missing:
            print(
                f"⚠  Warning: not forwarding unset env var(s): {', '.join(missing)}",
                file=sys.stderr,
            )

        env["ABP_THREAD_ID"] = self._thread_id
        env["ABP_TOOL_APPROVAL_MODE"] = os.environ.get("ABP_TOOL_APPROVAL_MODE", "deny")
        env["ABP_TRACE_FILE"] = f"{_CONTAINER_OUT}/abp_trace.json"
        env["PYTHONUNBUFFERED"] = "1"

        # Fix Ollama URL: localhost → host alias (matches deployers/docker.py).
        # Skip when already forwarded or when --network=host is used.
        ollama_url = self._ir.settings.ollama_base_url
        if (
            "OLLAMA_BASE_URL" not in env
            and self._cfg.network != SandboxNetwork.host
            and ("localhost" in ollama_url or "127.0.0.1" in ollama_url)
        ):
            alias = _OLLAMA_HOSTS[self._engine]
            env["OLLAMA_BASE_URL"] = ollama_url.replace("localhost", alias).replace(
                "127.0.0.1", alias
            )

        if extra_env:
            env.update(extra_env)
        return env

    def _provider_env_keys(self) -> set[str]:
        keys: set[str] = set()
        for node in self._ir.nodes:
            keys.update(_PROVIDER_ENV_KEYS.get(node.resolved_provider, []))
        return keys


def _parse_env_file(env_file: Path) -> dict[str, str]:
    """Parse a .env file (same format LocalRunner accepts)."""
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values.setdefault(key.strip(), value.strip())
    return values
