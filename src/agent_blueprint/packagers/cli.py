"""CliPackager — turns a generated project into an installable CLI package.

Takes the flat module layout produced by the LangGraph generator
(state.py, nodes.py, graph.py, main.py, _abp_*.py) and restructures it
into a src-layout Python package with a console-script entry point, so
the agent can be installed with pip/pipx and invoked as a command:

    abp package my-agent.yml
    pipx install ./my-agent-cli
    my-agent "Hello"
"""

import re

from jinja2 import Environment, PackageLoader, select_autoescape

from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.generators.langgraph import _to_python
from agent_blueprint.ir.compiler import AgentGraph

#: Modules the generator emits — the only import targets rewritten to
#: package-relative form. User `impl` imports are left untouched.
_LOCAL_MODULES = (
    "state",
    "tools",
    "nodes",
    "graph",
    "main",
    "_abp_trace",
    "_abp_harness",
    "_abp_otel",
    "_abp_runner",
)

_FROM_IMPORT_RE = re.compile(
    rf"^from ({'|'.join(_LOCAL_MODULES)}) import ", flags=re.MULTILINE
)


def dist_name_for(blueprint_name: str) -> str:
    """PEP 503-style distribution / command name: 'My Agent' → 'my-agent'."""
    slug = re.sub(r"[^a-z0-9]+", "-", blueprint_name.lower()).strip("-")
    return slug or "agent"


def package_name_for(blueprint_name: str) -> str:
    """Importable package name: 'My Agent' → 'my_agent'."""
    pkg = dist_name_for(blueprint_name).replace("-", "_")
    if pkg[0].isdigit():
        pkg = f"agent_{pkg}"
    return pkg


def _rewrite_local_imports(source: str, extra_modules: tuple[str, ...] = ()) -> str:
    """Rewrite generator-emitted cross-imports to package-relative form.

    `extra_modules` are user `impl` modules copied into the package (e.g. a
    function-tool impl living next to the blueprint), so their absolute imports
    (`from farm_impl import ...`) become package-relative too.
    """
    modules = "|".join(re.escape(m) for m in (*_LOCAL_MODULES, *extra_modules))
    pattern = re.compile(rf"^from ({modules}) import ", flags=re.MULTILINE)
    return pattern.sub(lambda m: f"from .{m.group(1)} import ", source)


def _parse_requirements(requirements: str) -> list[str]:
    return [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


class CliPackager:
    """Restructures generator output into an installable CLI distribution."""

    def __init__(self) -> None:
        self._env = Environment(
            loader=PackageLoader("agent_blueprint", "templates/package"),
            autoescape=select_autoescape([]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._env.filters["to_python"] = _to_python

    def package(
        self,
        files: dict[str, str],
        ir: AgentGraph,
        user_modules: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Map generator output (flat layout) to a src-layout CLI package.

        Returns a new {relative_path: content} mapping; the input is not
        mutated. requirements.txt is folded into pyproject dependencies.

        `user_modules` maps a top-level module name to its source — function-tool
        / retriever `impl` modules that live next to the blueprint. They are
        copied into the package and their absolute imports rewritten to relative,
        so the installed CLI can resolve `impl: "<module>.func"` references.
        """
        user_modules = user_modules or {}
        extra = tuple(user_modules)
        dist_name = dist_name_for(ir.name)
        pkg_name = package_name_for(ir.name)
        pkg_dir = f"src/{pkg_name}"

        packaged: dict[str, str] = {}
        dependencies: list[str] = []

        for filename, content in files.items():
            if filename == "requirements.txt":
                dependencies = _parse_requirements(content)
            elif filename.endswith(".py"):
                packaged[f"{pkg_dir}/{filename}"] = _rewrite_local_imports(content, extra)
            else:
                # Non-Python project files (.env.example) stay at the root.
                packaged[filename] = content

        for mod_name, mod_source in user_modules.items():
            packaged[f"{pkg_dir}/{mod_name}.py"] = _rewrite_local_imports(mod_source, extra)

        for template_name, output_name in (
            ("cli.py.j2", f"{pkg_dir}/cli.py"),
            ("pyproject.toml.j2", "pyproject.toml"),
        ):
            try:
                template = self._env.get_template(template_name)
                packaged[output_name] = template.render(
                    ir=ir,
                    dist_name=dist_name,
                    pkg_name=pkg_name,
                    dependencies=dependencies,
                )
            except Exception as e:
                raise GeneratorError(
                    f"Failed to render package template '{template_name}': {e}"
                ) from e

        return packaged
