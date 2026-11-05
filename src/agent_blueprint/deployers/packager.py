"""DeployPackager — adds Dockerfile and HTTP server to a generated code directory."""

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.ir.compiler import AgentGraph
from agent_blueprint.models.deploy import DeployConfig

_DEPLOY_TEMPLATES = [
    ("Dockerfile.j2", "Dockerfile"),
    (".dockerignore.j2", ".dockerignore"),
    ("server.py.j2", "server.py"),
    ("_abp_auth.py.j2", "_abp_auth.py"),
    ("requirements_deploy.txt.j2", "requirements_deploy.txt"),
]


class DeployPackager:
    """Renders deploy-specific templates into an existing code directory."""

    def __init__(self) -> None:
        self._env = Environment(
            loader=PackageLoader("agent_blueprint", "templates/deploy"),
            autoescape=select_autoescape([]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        # Python literals (None/True), not JSON (null/true), in generated code.
        self._env.filters["py"] = repr

    def package(self, code_dir: Path, ir: AgentGraph, deploy: DeployConfig | None = None) -> None:
        """Write Dockerfile, server.py, etc. into code_dir in-place."""
        config = deploy or DeployConfig()
        check_auth_configured(config)
        for template_name, output_name in _DEPLOY_TEMPLATES:
            try:
                template = self._env.get_template(template_name)
                content = template.render(ir=ir, auth=config.auth, rate_limit=config.rate_limit)
                (code_dir / output_name).write_text(content, encoding="utf-8")
            except Exception as e:
                raise GeneratorError(
                    f"Failed to render deploy template '{template_name}': {e}"
                ) from e


def check_auth_configured(config: DeployConfig) -> None:
    """Refuse to package a server that could not verify who is calling it."""
    if not config.auth.is_configured:
        raise GeneratorError(
            "deploy.auth is not configured. The deployed /invoke endpoint authenticates "
            "callers with a JWT by default: set deploy.auth.jwks_url (identity provider) "
            "or deploy.auth.secret_env (shared HMAC secret). To expose it without "
            "authentication, for example behind a gateway that already authenticates, "
            "set deploy.auth.mode: none."
        )
