"""Tests for CliPackager — installable CLI distribution from generator output."""

import ast

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.packagers.cli import (
    CliPackager,
    dist_name_for,
    package_name_for,
)


def _spec_data(name: str = "My Cool Agent") -> dict:
    return {
        "blueprint": {"name": name, "description": "Does cool things"},
        "state": {
            "fields": {"messages": {"type": "list[message]", "reducer": "append"}}
        },
        "agents": {"assistant": {"model": "gpt-4o", "tools": ["lookup"]}},
        "tools": {
            "lookup": {"type": "function", "impl": "mypkg.tools.lookup"},
        },
        "graph": {
            "entry_point": "assistant",
            "nodes": {"assistant": {"agent": "assistant"}},
            "edges": [{"from": "assistant", "to": "END"}],
        },
    }


def _packaged(name: str = "My Cool Agent") -> dict[str, str]:
    spec = BlueprintSpec.model_validate(_spec_data(name))
    ir = compile_blueprint(spec)
    files = LangGraphGenerator().generate(ir)
    return CliPackager().package(files, ir)


class TestNaming:
    def test_dist_name_slugs_spaces_and_case(self):
        assert dist_name_for("My Cool Agent") == "my-cool-agent"

    def test_dist_name_falls_back_when_empty(self):
        assert dist_name_for("!!!") == "agent"

    def test_package_name_is_importable_identifier(self):
        assert package_name_for("My Cool Agent") == "my_cool_agent"
        assert package_name_for("3d-printer").startswith("agent_")
        assert package_name_for("3d-printer").isidentifier()


class TestPackageLayout:
    def test_src_layout_with_entry_module(self):
        packaged = _packaged()
        assert "pyproject.toml" in packaged
        assert "src/my_cool_agent/cli.py" in packaged
        assert "src/my_cool_agent/main.py" in packaged
        assert "src/my_cool_agent/graph.py" in packaged
        assert ".env.example" in packaged  # non-Python files stay at the root

    def test_requirements_txt_folded_into_pyproject(self):
        packaged = _packaged()
        assert "requirements.txt" not in packaged
        pyproject = packaged["pyproject.toml"]
        assert '"langgraph>=0.3",' in pyproject
        assert '"langchain-core>=0.3",' in pyproject

    def test_pyproject_metadata_and_entry_point(self):
        pyproject = _packaged()["pyproject.toml"]
        assert 'name = "my-cool-agent"' in pyproject
        assert 'description = "Does cool things"' in pyproject
        assert 'my-cool-agent = "my_cool_agent.cli:main"' in pyproject
        assert 'packages = ["src/my_cool_agent"]' in pyproject

    def test_all_python_files_parse(self):
        packaged = _packaged()
        for path, content in packaged.items():
            if path.endswith(".py"):
                ast.parse(content, filename=path)


class TestImportRewrite:
    def test_local_imports_become_relative(self):
        packaged = _packaged()
        main_py = packaged["src/my_cool_agent/main.py"]
        assert "from .graph import graph" in main_py
        assert "from ._abp_trace import" in main_py
        nodes_py = packaged["src/my_cool_agent/nodes.py"]
        assert "from .state import AgentState" in nodes_py
        assert "from .tools import" in nodes_py

    def test_no_flat_local_imports_remain(self):
        packaged = _packaged()
        for path, content in packaged.items():
            if not path.endswith(".py"):
                continue
            for line in content.splitlines():
                assert not line.startswith("from state import"), path
                assert not line.startswith("from graph import"), path
                assert not line.startswith("from _abp_trace import"), path

    def test_user_impl_imports_untouched(self):
        tools_py = _packaged()["src/my_cool_agent/tools.py"]
        assert "mypkg" in tools_py
        assert "from .mypkg" not in tools_py

    def test_cli_module_imports_run(self):
        cli_py = _packaged()["src/my_cool_agent/cli.py"]
        assert "from .main import run" in cli_py
        assert "def main() -> int:" in cli_py


class TestObservabilityVariant:
    def test_otel_module_packaged_and_rewritten(self):
        data = _spec_data()
        data["observability"] = {"tracing": {"enabled": True, "exporter": "console"}}
        spec = BlueprintSpec.model_validate(data)
        ir = compile_blueprint(spec)
        packaged = CliPackager().package(LangGraphGenerator().generate(ir), ir)

        assert "src/my_cool_agent/_abp_otel.py" in packaged
        assert "from ._abp_trace import" in packaged["src/my_cool_agent/_abp_otel.py"]
        assert "from ._abp_otel import" in packaged["src/my_cool_agent/main.py"]
        # otel deps folded into pyproject as well
        assert "opentelemetry-sdk" in packaged["pyproject.toml"]


class TestUserImplModules:
    """User `impl` modules next to the blueprint are copied into the package and
    their absolute imports rewritten to package-relative, so the installed CLI
    resolves `impl: "<module>.func"` references."""

    def _spec_with_impl(self) -> dict:
        return {
            "blueprint": {"name": "growops"},
            "state": {"fields": {"messages": {"type": "list[message]", "reducer": "append"}}},
            "agents": {"a": {"model": "gpt-4o", "tools": ["pt"]}},
            "tools": {"pt": {"type": "function", "impl": "farm_impl.parse_telemetry"}},
            "graph": {
                "entry_point": "a",
                "nodes": {"a": {"agent": "a"}},
                "edges": [{"from": "a", "to": "END"}],
            },
        }

    def test_user_module_included_and_imports_relativized(self):
        spec = BlueprintSpec.model_validate(self._spec_with_impl())
        ir = compile_blueprint(spec)
        files = LangGraphGenerator().generate(ir)
        packaged = CliPackager().package(
            files, ir, user_modules={"farm_impl": "def parse_telemetry(payload):\n    return {}\n"}
        )
        pkg = package_name_for("growops")
        assert f"src/{pkg}/farm_impl.py" in packaged
        tools = packaged[f"src/{pkg}/tools.py"]
        assert "from .farm_impl import" in tools
        assert "from farm_impl import" not in tools

    def test_without_user_modules_impl_import_stays_absolute(self):
        spec = BlueprintSpec.model_validate(self._spec_with_impl())
        ir = compile_blueprint(spec)
        files = LangGraphGenerator().generate(ir)
        packaged = CliPackager().package(files, ir)  # no user_modules
        pkg = package_name_for("growops")
        tools = packaged[f"src/{pkg}/tools.py"]
        assert "from farm_impl import" in tools
        assert f"src/{pkg}/farm_impl.py" not in packaged
