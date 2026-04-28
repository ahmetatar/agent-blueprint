"""LangGraph code generator."""

import re
import keyword

from jinja2 import Environment, PackageLoader, select_autoescape

from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.generators.base import BaseGenerator
from agent_blueprint.ir.compiler import AgentGraph, IRNode

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


def _to_python(value: object) -> str:
    """Convert a value to a valid Python literal (uses repr)."""
    return repr(value)


def _llm_class(node: IRNode) -> str:
    mapping = {
        "anthropic": "ChatAnthropic",
        "google": "ChatGoogleGenerativeAI",
        "ollama": "ChatOllama",
        "azure_openai": "AzureChatOpenAI",
        "bedrock": "ChatBedrock",
        "openai_compatible": "ChatOpenAI",
    }
    return mapping.get(node.resolved_provider, "ChatOpenAI")


def _llm_constructor_kwargs(node: IRNode, temperature: float | None) -> dict[str, object]:
    """Build constructor kwargs without interpreting provider-specific extras."""
    p = node.resolved_provider
    m = node.resolved_model
    pd = node.resolved_provider_def
    kwargs: dict[str, object] = {}

    if p == "azure_openai":
        kwargs["azure_deployment"] = pd.deployment if pd else m
        kwargs["azure_endpoint"] = pd.base_url if pd else ""
        kwargs["api_version"] = (pd.api_version if pd else None) or "2024-02-01"
    elif p == "bedrock":
        kwargs["model_id"] = m
        kwargs["region_name"] = (pd.region if pd else None) or "us-east-1"
    else:
        kwargs["model"] = m
        if p == "ollama":
            kwargs["base_url"] = (pd.base_url if pd else None) or "http://localhost:11434"
        elif p == "openai_compatible" and pd:
            kwargs["base_url"] = pd.base_url

    if temperature is not None:
        kwargs["temperature"] = temperature
    if node.agent and node.agent.max_tokens is not None:
        kwargs["max_tokens"] = node.agent.max_tokens
    if pd:
        kwargs.update(pd.extra)
    if node.agent:
        kwargs.update(node.agent.llm_params)
        if node.agent.reasoning and node.agent.reasoning.enabled:
            kwargs.update(node.agent.reasoning.params)
    return kwargs


def _render_kwargs(kwargs: dict[str, object]) -> str:
    """Render kwargs while allowing arbitrary user-supplied key names via **dict."""
    parts: list[str] = []
    unpacked: dict[str, object] = {}
    for key, value in kwargs.items():
        if key.isidentifier() and not keyword.iskeyword(key):
            parts.append(f"{key}={value!r}")
        else:
            unpacked[key] = value
    if unpacked:
        parts.append(f"**{unpacked!r}")
    return ", ".join(parts)


def _llm_call_args(node: IRNode, temperature: float | None) -> str:
    return _render_kwargs(_llm_constructor_kwargs(node, temperature))


def _impl_parts(tool_name: str, impl_path: str) -> dict[str, str]:
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
        self._env.filters["to_python"] = _to_python
        self._env.globals["impl_parts"] = _impl_parts
        self._env.globals["llm_class"] = _llm_class
        self._env.globals["llm_call_args"] = _llm_call_args

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
