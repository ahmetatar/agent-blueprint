"""Tests for SandboxRunner — engine resolution and container command construction."""

from pathlib import Path

import pytest

import agent_blueprint.runners.sandbox as sandbox_mod
from agent_blueprint.exceptions import SandboxError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.run import SandboxConfig, SandboxEngine
from agent_blueprint.runners.sandbox import SandboxRunner, resolve_engine
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

FIXTURES = Path(__file__).parent.parent / "fixtures"


def load_spec_and_ir(name: str = "basic_chatbot.yml"):
    raw = load_blueprint_yaml(FIXTURES / name)
    spec = BlueprintSpec.model_validate(raw)
    return spec, compile_blueprint(spec)


def make_runner(
    config: SandboxConfig | None = None,
    *,
    engine: str = "podman",
    thread_id: str = "default",
    tempdir: Path | None = None,
) -> SandboxRunner:
    spec, ir = load_spec_and_ir()
    runner = SandboxRunner(
        ir, spec, config or SandboxConfig(), thread_id=thread_id, engine=engine
    )
    if tempdir is not None:
        runner._tempdir = tempdir
    return runner


class TestResolveEngine:
    def _stub_available(self, monkeypatch, available: set[str]) -> None:
        monkeypatch.setattr(
            sandbox_mod, "engine_available", lambda rt: rt in available
        )

    def test_auto_prefers_podman(self, monkeypatch):
        self._stub_available(monkeypatch, {"podman", "docker"})
        assert resolve_engine(SandboxEngine.auto) == "podman"

    def test_auto_falls_back_to_docker(self, monkeypatch):
        self._stub_available(monkeypatch, {"docker"})
        assert resolve_engine(SandboxEngine.auto) == "docker"

    def test_auto_without_engines_raises(self, monkeypatch):
        self._stub_available(monkeypatch, set())
        with pytest.raises(SandboxError, match="no container engine"):
            resolve_engine(SandboxEngine.auto)

    def test_explicit_engine_returned_when_available(self, monkeypatch):
        self._stub_available(monkeypatch, {"docker"})
        assert resolve_engine(SandboxEngine.docker) == "docker"

    def test_explicit_engine_unavailable_raises(self, monkeypatch):
        self._stub_available(monkeypatch, {"docker"})
        with pytest.raises(SandboxError, match="podman"):
            resolve_engine(SandboxEngine.podman)


class TestImageAndDockerfile:
    def test_image_name_from_blueprint_slug(self):
        runner = make_runner()
        assert runner.image == "abp-run-basic-chatbot:latest"

    def test_install_deps_is_noop(self):
        runner = make_runner()
        assert runner._install_deps() == 0

    def test_dockerfile_rendered(self, tmp_path):
        cfg = SandboxConfig(image="python:3.12-slim")
        runner = make_runner(cfg, tempdir=tmp_path)
        runner._write_dockerfile()
        dockerfile = (tmp_path / "Dockerfile").read_text()
        assert "FROM python:3.12-slim" in dockerfile
        assert 'ENTRYPOINT ["python", "_abp_runner.py"]' in dockerfile


class TestRunCommand:
    def test_one_shot_appends_input(self, tmp_path):
        runner = make_runner(tempdir=tmp_path)
        cmd = runner.run_command(user_input="hello", env_file=None)
        assert cmd[0] == "podman"
        assert cmd[1:3] == ["run", "--rm"]
        assert cmd[-1] == "hello"
        assert "-i" not in cmd

    def test_repl_adds_stdin_flag(self, tmp_path):
        runner = make_runner(tempdir=tmp_path)
        cmd = runner.run_command(user_input=None, env_file=None)
        assert "-i" in cmd
        assert cmd[-1] == runner.image

    def test_network_flag(self, tmp_path):
        cfg = SandboxConfig(network="none")
        runner = make_runner(cfg, tempdir=tmp_path)
        cmd = runner.run_command(user_input="x", env_file=None)
        idx = cmd.index("--network")
        assert cmd[idx + 1] == "none"

    def test_resource_limits(self, tmp_path):
        cfg = SandboxConfig(memory="512m", cpus=2.0)
        runner = make_runner(cfg, tempdir=tmp_path)
        cmd = runner.run_command(user_input="x", env_file=None)
        assert cmd[cmd.index("--memory") + 1] == "512m"
        assert cmd[cmd.index("--cpus") + 1] == "2.0"

    def test_limits_absent_by_default(self, tmp_path):
        runner = make_runner(tempdir=tmp_path)
        cmd = runner.run_command(user_input="x", env_file=None)
        assert "--memory" not in cmd
        assert "--cpus" not in cmd

    def test_mount_selinux_label_for_podman(self, tmp_path):
        runner = make_runner(engine="podman", tempdir=tmp_path)
        cmd = runner.run_command(user_input="x", env_file=None)
        mount = cmd[cmd.index("-v") + 1]
        assert mount == f"{tmp_path}:/abp-out:Z"

    def test_mount_plain_for_docker(self, tmp_path):
        runner = make_runner(engine="docker", tempdir=tmp_path)
        cmd = runner.run_command(user_input="x", env_file=None)
        mount = cmd[cmd.index("-v") + 1]
        assert mount == f"{tmp_path}:/abp-out"


class TestContainerEnv:
    def test_core_abp_vars(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ABP_TOOL_APPROVAL_MODE", raising=False)
        runner = make_runner(thread_id="sess-7", tempdir=tmp_path)
        env = runner.container_env(None)
        assert env["ABP_THREAD_ID"] == "sess-7"
        assert env["ABP_TOOL_APPROVAL_MODE"] == "deny"
        assert env["ABP_TRACE_FILE"] == "/abp-out/abp_trace.json"
        assert env["PYTHONUNBUFFERED"] == "1"

    def test_provider_key_forwarded_when_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        runner = make_runner(tempdir=tmp_path)
        env = runner.container_env(None)
        assert env["OPENAI_API_KEY"] == "sk-test"

    def test_host_env_not_inherited(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SOME_RANDOM_HOST_VAR", "leaky")
        runner = make_runner(tempdir=tmp_path)
        env = runner.container_env(None)
        assert "SOME_RANDOM_HOST_VAR" not in env

    def test_env_passthrough_forwarded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_EXTRA", "value")
        cfg = SandboxConfig(env_passthrough=["MY_EXTRA"])
        runner = make_runner(cfg, tempdir=tmp_path)
        env = runner.container_env(None)
        assert env["MY_EXTRA"] == "value"

    def test_missing_passthrough_warns(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
        cfg = SandboxConfig(env_passthrough=["NOT_SET_ANYWHERE"])
        runner = make_runner(cfg, tempdir=tmp_path)
        env = runner.container_env(None)
        assert "NOT_SET_ANYWHERE" not in env
        assert "NOT_SET_ANYWHERE" in capsys.readouterr().err

    def test_host_env_wins_over_env_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "from-shell")
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=from-dotenv\n")
        runner = make_runner(tempdir=tmp_path)
        env = runner.container_env(env_file)
        assert env["OPENAI_API_KEY"] == "from-shell"

    def test_env_file_used_when_host_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("OPENAI_API_KEY=from-dotenv\n")
        runner = make_runner(tempdir=tmp_path)
        env = runner.container_env(env_file)
        assert env["OPENAI_API_KEY"] == "from-dotenv"

    def test_ollama_rewrite_podman(self, tmp_path):
        runner = make_runner(engine="podman", tempdir=tmp_path)
        env = runner.container_env(None)
        assert env["OLLAMA_BASE_URL"] == "http://host.containers.internal:11434"

    def test_ollama_rewrite_docker(self, tmp_path):
        runner = make_runner(engine="docker", tempdir=tmp_path)
        env = runner.container_env(None)
        assert env["OLLAMA_BASE_URL"] == "http://host.docker.internal:11434"

    def test_ollama_not_rewritten_with_host_network(self, tmp_path):
        cfg = SandboxConfig(network="host")
        runner = make_runner(cfg, tempdir=tmp_path)
        env = runner.container_env(None)
        assert "OLLAMA_BASE_URL" not in env


class TestExecuteBuildFailure:
    def test_build_failure_short_circuits(self, tmp_path, monkeypatch):
        import subprocess

        runner = make_runner(tempdir=tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="boom")

        monkeypatch.setattr(sandbox_mod.subprocess, "run", fake_run)
        proc = runner._execute(user_input="x", env_file=None, capture_output=True)
        assert proc.returncode == 1
        # only the build command ran; no `<engine> run`
        assert len(calls) == 1
        assert calls[0][:2] == ["podman", "build"]
