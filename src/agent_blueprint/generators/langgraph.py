"""LangGraph code generator."""

import re
from importlib.resources import files
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.generators.base import BaseGenerator
from agent_blueprint.ir.compiler import AgentGraph

_TEMPLATES = [
    ("__init__.py.j2", "__init__.py"),
    ("state.py.j2", "state.py"),
    ("tools.py.j2", "tools.py"),
    ("nodes.py.j2", "nodes.py"),
    ("graph.py.j2", "graph.py"),
    ("main.py.j2", "main.py"),
    ("requirements.txt.j2", "requirements.txt"),
]

_RUNNER_TEMPLATE = ("_abp_runner.py.j2", "_abp_runner.py")


def _safe_id(value: str) -> str:
    """Convert a node ID to a safe Python identifier."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", value)


def _python_type(type_str: str) -> str:
    mapping = {
        "string": "str",
        "str": "str",
        "integer": "int",
        "int": "int",
        "number": "float",
        "float": "float",
        "boolean": "bool",
        "bool": "bool",
        "list": "list",
        "dict": "dict",
    }
    return mapping.get(type_str.lower(), "str")


def _escape_string(value: str) -> str:
    """Escape a string for embedding in Python triple-quoted strings."""
    return value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')


def _impl_parts(tool_name: str, impl_path: str) -> dict:
    """Parse an impl dotted path into an import statement and a local alias.

    Example:
        "myapp.tools.classify" → {
            "import_stmt": "from myapp.tools import classify as _classify_intent_impl",
            "alias": "_classify_intent_impl"
        }
    """
    alias = f"_{tool_name}_impl"
    parts = impl_path.rsplit(".", 1)
    if len(parts) == 1:
        import_stmt = f"import {parts[0]} as {alias}"
    else:
        module, func = parts
        import_stmt = f"from {module} import {func} as {alias}"
    return {"import_stmt": import_stmt, "alias": alias}


class LangGraphGenerator(BaseGenerator):
    """Generates a LangGraph Python project from an AgentGraph IR."""

    def __init__(self) -> None:
        self._env = Environment(
            loader=PackageLoader("agent_blueprint", "templates/langgraph"),
            autoescape=select_autoescape([]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._env.filters["safe_id"] = _safe_id
        self._env.filters["python_type"] = _python_type
        self._env.filters["escape_string"] = _escape_string
        self._env.globals["impl_parts"] = _impl_parts

    def generate(
        self,
        ir: AgentGraph,
        *,
        runner_thread_id: str | None = None,
    ) -> dict[str, str]:
        """Generate LangGraph project files from AgentGraph IR.

        If runner_thread_id is provided, also generates _abp_runner.py.
        """
        files: dict[str, str] = {}

        for template_name, output_name in _TEMPLATES:
            try:
                template = self._env.get_template(template_name)
                content = template.render(ir=ir)
                files[output_name] = content
            except Exception as e:
                raise GeneratorError(
                    f"Failed to render template '{template_name}': {e}"
                ) from e

        if runner_thread_id is not None:
            tmpl_name, out_name = _RUNNER_TEMPLATE
            try:
                template = self._env.get_template(tmpl_name)
                content = template.render(ir=ir, thread_id=runner_thread_id)
                files[out_name] = content
            except Exception as e:
                raise GeneratorError(
                    f"Failed to render template '{tmpl_name}': {e}"
                ) from e

        # Generate a .env.example with required env vars
        files[".env.example"] = self._generate_env_example(ir)

        return files

    def _generate_env_example(self, ir: AgentGraph) -> str:
        providers = ir.used_providers
        lines = ["# Environment variables required by this agent"]
        if "openai" in providers or "custom" in providers or not providers:
            lines.append("OPENAI_API_KEY=")
        if "anthropic" in providers:
            lines.append("ANTHROPIC_API_KEY=")
        if "google" in providers:
            lines.append("GOOGLE_API_KEY=")
        if "custom" in providers:
            lines.append("CUSTOM_BASE_URL=http://localhost:8000")
        seen: set[str] = set()

        for tool in ir.all_tools.values():
            if tool.auth and tool.auth.token_env and tool.auth.token_env not in seen:
                lines.append(f"{tool.auth.token_env}=")
                seen.add(tool.auth.token_env)

        if ir.memory.connection_string_env and ir.memory.connection_string_env not in seen:
            lines.append(f"{ir.memory.connection_string_env}=")

        return "\n".join(lines) + "\n"
