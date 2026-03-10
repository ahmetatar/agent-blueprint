"""abp deploy — generate and deploy to a cloud platform."""

import shutil
import tempfile
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

from agent_blueprint.exceptions import BlueprintValidationError, GeneratorError
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

console = Console()
err_console = Console(stderr=True)

_PLATFORMS = ("azure", "aws", "gcp", "docker", "podman")


def deploy(
    blueprint: Path = typer.Argument(..., help="Path to the blueprint YAML file"),
    platform: Optional[str] = typer.Option(
        None, "--platform", "-p",
        help="Cloud platform: azure | aws | gcp (overrides deploy.platform in blueprint)",
    ),
    target: str = typer.Option(
        "langgraph", "--target", "-t", help="Generator target (only langgraph supported)"
    ),
    image_tag: str = typer.Option(
        "latest", "--image-tag", help="Docker image tag"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print commands without executing them"
    ),
    env_extra: Optional[list[str]] = typer.Option(
        None, "--env", help="Extra env vars as KEY=VALUE (repeatable)"
    ),
) -> None:
    """Generate a blueprint and deploy it to a cloud platform.

    Requires Docker and the platform CLI (az / aws / gcloud) to be installed
    and authenticated. Secret values are read from environment variables
    declared in the blueprint (model_providers, tools auth, etc.).
    """
    # 1. Load + validate
    try:
        raw = load_blueprint_yaml(blueprint)
        spec = BlueprintSpec.model_validate(raw)
    except BlueprintValidationError as e:
        err_console.print(f"[bold red]Load error:[/] {e}")
        raise typer.Exit(1) from e
    except ValidationError as e:
        err_console.print(f"[bold red]Validation error:[/] {e}")
        raise typer.Exit(1) from e

    # 2. Resolve platform
    resolved_platform = platform
    if not resolved_platform and spec.deploy:
        resolved_platform = spec.deploy.platform
    if not resolved_platform:
        err_console.print(
            "[bold red]No platform specified.[/] "
            "Use [cyan]--platform azure|aws|gcp[/] or set [cyan]deploy.platform[/] in blueprint."
        )
        raise typer.Exit(1)
    if resolved_platform not in _PLATFORMS:
        err_console.print(f"[bold red]Unknown platform:[/] '{resolved_platform}'. Choose from: {', '.join(_PLATFORMS)}")
        raise typer.Exit(1)

    # 3. Get platform-specific deploy config
    deploy_cfg = spec.deploy if spec.deploy else None
    platform_config = None
    if deploy_cfg:
        platform_config = getattr(deploy_cfg, resolved_platform, None)

    # docker/podman: allow missing config — fall back to defaults
    if platform_config is None and resolved_platform in ("docker", "podman"):
        from agent_blueprint.models.deploy import DockerDeployConfig
        platform_config = DockerDeployConfig()
    elif platform_config is None:
        err_console.print(
            f"[bold red]No deploy.{resolved_platform} config found in blueprint.[/] "
            f"Add a [cyan]deploy.{resolved_platform}[/] section."
        )
        raise typer.Exit(1)

    # 4. Compile IR
    from agent_blueprint.ir.compiler import compile_blueprint
    from agent_blueprint.exceptions import BlueprintCompilationError

    try:
        ir = compile_blueprint(spec)
    except BlueprintCompilationError as e:
        err_console.print(f"[bold red]Compilation error:[/] {e}")
        raise typer.Exit(1) from e

    # 5. Select deployer
    bp_name = spec.blueprint.name
    if resolved_platform == "azure":
        from agent_blueprint.deployers.azure import AzureDeployer
        deployer = AzureDeployer(platform_config, bp_name)
    elif resolved_platform == "aws":
        from agent_blueprint.deployers.aws import AWSDeployer
        deployer = AWSDeployer(platform_config, bp_name)
    elif resolved_platform == "docker":
        from agent_blueprint.deployers.docker import DockerDeployer
        deployer = DockerDeployer(platform_config, bp_name)
    elif resolved_platform == "podman":
        from agent_blueprint.deployers.docker import PodmanDeployer
        deployer = PodmanDeployer(platform_config, bp_name)
    else:
        from agent_blueprint.deployers.gcp import GCPDeployer
        deployer = GCPDeployer(platform_config, bp_name)

    # 6. Check prerequisites
    issues = deployer.check_prerequisites()
    if issues:
        err_console.print("[bold red]Prerequisites not met:[/]")
        for issue in issues:
            err_console.print(f"  ✗ {issue}")
        raise typer.Exit(1)

    # 7. Collect secrets
    from agent_blueprint.deployers.secrets import collect_required_secrets, resolve_secrets

    extra_env: dict[str, str] = {}
    for kv in (env_extra or []):
        if "=" in kv:
            k, _, v = kv.partition("=")
            extra_env[k.strip()] = v.strip()

    required = collect_required_secrets(spec)
    secrets, missing = resolve_secrets(required, extra=extra_env)

    if missing:
        console.print(
            f"[yellow]⚠  {len(missing)} secret(s) not found in environment "
            f"and will not be injected:[/] {', '.join(missing)}"
        )

    # 8. Generate code to temp dir
    if target != "langgraph":
        err_console.print("[bold red]abp deploy[/] only supports [cyan]langgraph[/] target for now.")
        raise typer.Exit(1)

    from agent_blueprint.generators.langgraph import LangGraphGenerator
    from agent_blueprint.deployers.packager import DeployPackager

    tmpdir = Path(tempfile.mkdtemp(prefix="abp_deploy_"))
    try:
        gen = LangGraphGenerator()
        try:
            files = gen.generate(ir)
        except GeneratorError as e:
            err_console.print(f"[bold red]Generation error:[/] {e}")
            raise typer.Exit(1) from e

        for filename, content in files.items():
            dest = tmpdir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

        # Add Dockerfile + server.py
        DeployPackager().package(tmpdir, ir)

        if dry_run:
            console.print(Panel(
                "\n".join(f"  [cyan]{f}[/]" for f in sorted(p.name for p in tmpdir.iterdir())),
                title="[bold yellow]Dry run[/] — deploy package contents",
                border_style="yellow",
            ))

        # 9. Deploy
        result = deployer.deploy(
            tmpdir, secrets, image_tag=image_tag, dry_run=dry_run
        )

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # 10. Print result
    if result.success:
        lines = [f"  Platform   [cyan]{resolved_platform}[/]"]
        if result.url:
            lines.append(f"  Endpoint   [link={result.url}]{result.url}[/link]")
            lines.append(f"\n  [dim]POST {result.url}/invoke[/dim]")
            lines.append(f"  [dim]  {{\"input\": \"Hello\", \"thread_id\": \"default\"}}[/dim]")
        console.print(Panel(
            "\n".join(lines),
            title=f"[bold green]Deployed[/] — {bp_name}",
            border_style="green",
        ))
    else:
        err_console.print(f"[bold red]Deploy failed:[/] {result.message}")
        raise typer.Exit(1)
