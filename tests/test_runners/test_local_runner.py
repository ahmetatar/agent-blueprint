"""Tests for LocalRunner — generation and env setup (no real subprocess)."""

from pathlib import Path


from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.runners.local import LocalRunner
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_ir(name: str):
    raw = load_blueprint_yaml(FIXTURES / name)
    spec = BlueprintSpec.model_validate(raw)
    return compile_blueprint(spec)


class TestRunnerGeneration:
    def test_runner_template_included_when_thread_id_given(self):
        ir = load_ir("basic_chatbot.yml")
        gen = LangGraphGenerator()
        files = gen.generate(ir, runner_thread_id="test-thread")
        assert "_abp_runner.py" in files

    def test_runner_template_excluded_by_default(self):
        ir = load_ir("basic_chatbot.yml")
        gen = LangGraphGenerator()
        files = gen.generate(ir)
        assert "_abp_runner.py" not in files

    def test_runner_contains_thread_id(self):
        ir = load_ir("basic_chatbot.yml")
        gen = LangGraphGenerator()
        files = gen.generate(ir, runner_thread_id="my-session")
        assert "my-session" in files["_abp_runner.py"]

    def test_runner_contains_single_shot_and_repl(self):
        ir = load_ir("basic_chatbot.yml")
        gen = LangGraphGenerator()
        files = gen.generate(ir, runner_thread_id="default")
        runner = files["_abp_runner.py"]
        assert "_single_shot" in runner
        assert "_interactive" in runner

    def test_runner_is_valid_python(self):
        import ast
        ir = load_ir("basic_chatbot.yml")
        gen = LangGraphGenerator()
        files = gen.generate(ir, runner_thread_id="default")
        ast.parse(files["_abp_runner.py"])


class TestLocalRunnerEnv:
    def _make_runner(self, name="basic_chatbot.yml", thread_id="default"):
        ir = load_ir(name)
        return LocalRunner(ir, thread_id=thread_id)

    def test_pythonpath_includes_cwd(self, tmp_path):
        runner = self._make_runner()
        runner._tempdir = tmp_path
        env = runner._build_env(None)
        assert str(Path.cwd()) in env["PYTHONPATH"]

    def test_pythonpath_includes_tempdir(self, tmp_path):
        runner = self._make_runner()
        runner._tempdir = tmp_path
        env = runner._build_env(None)
        assert str(tmp_path) in env["PYTHONPATH"]

    def test_thread_id_in_env(self, tmp_path):
        runner = self._make_runner(thread_id="sess-42")
        runner._tempdir = tmp_path
        env = runner._build_env(None)
        assert env["ABP_THREAD_ID"] == "sess-42"

    def test_env_file_loaded(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MY_SECRET=hello\nOTHER=world\n")
        runner = self._make_runner()
        runner._tempdir = tmp_path / "run"
        runner._tempdir.mkdir()
        env = runner._build_env(env_file)
        assert env.get("MY_SECRET") == "hello"
        assert env.get("OTHER") == "world"

    def test_env_file_does_not_override_existing_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EXISTING_VAR", "original")
        env_file = tmp_path / ".env"
        env_file.write_text("EXISTING_VAR=overridden\n")
        runner = self._make_runner()
        runner._tempdir = tmp_path / "run"
        runner._tempdir.mkdir()
        env = runner._build_env(env_file)
        # setdefault means existing env wins
        assert env["EXISTING_VAR"] == "original"


class TestStubWarning:
    def test_warns_for_stub_tools(self, capsys):
        ir = load_ir("impl_tools.yml")  # has send_email without impl
        runner = LocalRunner(ir)
        runner._warn_stubs()
        captured = capsys.readouterr()
        assert "send_email" in captured.err
        assert "Warning" in captured.err

    def test_no_warning_when_all_impl(self, capsys):
        ir = load_ir("basic_chatbot.yml")  # no function tools at all
        runner = LocalRunner(ir)
        runner._warn_stubs()
        captured = capsys.readouterr()
        assert captured.err == ""
