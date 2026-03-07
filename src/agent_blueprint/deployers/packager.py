"""DeployPackager — adds Dockerfile and HTTP server to a generated code directory."""

from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.ir.compiler import AgentGraph

_DEPLOY_TEMPLATES = [
    ("Dockerfile.j2", "Dockerfile"),
    (".dockerignore.j2", ".dockerignore"),
    ("server.py.j2", "server.py"),
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

    def package(self, code_dir: Path, ir: AgentGraph) -> None:
        """Write Dockerfile, server.py, etc. into code_dir in-place."""
        for template_name, output_name in _DEPLOY_TEMPLATES:
            try:
                template = self._env.get_template(template_name)
                content = template.render(ir=ir)
                (code_dir / output_name).write_text(content, encoding="utf-8")
            except Exception as e:
                raise GeneratorError(
                    f"Failed to render deploy template '{template_name}': {e}"
                ) from e
