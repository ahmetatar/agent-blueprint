"""LocalRunner — generate to a temp dir and execute in a subprocess."""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.ir.compiler import AgentGraph
from agent_blueprint.models.tools import ToolType


@dataclass
class LocalRunResult:
    returncode: int
    stdout: str
    stderr: str
    trace_file: Path | None
    trace_manifest: dict | None


class LocalRunner:
    """Generates a blueprint into a temp dir and runs it in the current Python env."""

    def __init__(self, ir: AgentGraph, thread_id: str = "default") -> None:
        self._ir = ir
        self._thread_id = thread_id
        self._tempdir: Path | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        user_input: str | None = None,
        *,
        install: bool = False,
        env_file: Path | None = None,
        keep_temp: bool = False,
    ) -> int:
        """Generate, optionally install deps, then execute.

        Returns the subprocess exit code.
        """
        result = self.run_capture(
            user_input=user_input,
            install=install,
            env_file=env_file,
            keep_temp=keep_temp,
        )
        return result.returncode

    def run_capture(
        self,
        user_input: str | None = None,
        *,
        install: bool = False,
        env_file: Path | None = None,
        keep_temp: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> LocalRunResult:
        """Generate, optionally install deps, then execute and capture outputs."""
        self._tempdir = Path(tempfile.mkdtemp(prefix="abp_run_"))
        if not keep_temp:
            atexit.register(self._cleanup)

        self._generate()
        self._warn_stubs()

        if install:
            rc = self._install_deps()
            if rc != 0:
                return LocalRunResult(
                    returncode=rc,
                    stdout="",
                    stderr="",
                    trace_file=None,
                    trace_manifest=None,
                )

        proc = self._execute(
            user_input=user_input,
            env_file=env_file,
            extra_env=extra_env,
            capture_output=True,
        )
        trace_file = None
        trace_manifest = None
        if self._tempdir is not None:
            candidate = self._tempdir / "abp_trace.json"
            if candidate.exists():
                trace_file = candidate
                try:
                    trace_manifest = json.loads(candidate.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    trace_manifest = None
        return LocalRunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            trace_file=trace_file,
            trace_manifest=trace_manifest,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate(self) -> None:
        from agent_blueprint.generators.langgraph import LangGraphGenerator

        gen = LangGraphGenerator()
        try:
            files = gen.generate(self._ir, runner_thread_id=self._thread_id)
        except GeneratorError as e:
            raise GeneratorError(f"abp run: generation failed: {e}") from e

        assert self._tempdir is not None
        for filename, content in files.items():
            dest = self._tempdir / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")

    def _warn_stubs(self) -> None:
        stubs = [
            name
            for name, tool in self._ir.all_tools.items()
            if tool.type == ToolType.function and not tool.impl
        ]
        if stubs:
            names = ", ".join(stubs)
            print(
                f"⚠  Warning: {len(stubs)} tool(s) have no implementation "
                f"and will raise NotImplementedError if called: {names}",
                file=sys.stderr,
            )

    def _install_deps(self) -> int:
        assert self._tempdir is not None
        req = self._tempdir / "requirements.txt"
        if not req.exists():
            return 0
        print("→ Installing dependencies…", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
            check=False,
        )
        return result.returncode

    def _execute(
        self,
        *,
        user_input: str | None,
        env_file: Path | None,
        extra_env: dict[str, str] | None = None,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        assert self._tempdir is not None

        env = self._build_env(env_file, extra_env=extra_env)
        cmd = [sys.executable, "_abp_runner.py"]
        if user_input is not None:
            cmd.append(user_input)

        return subprocess.run(
            cmd,
            cwd=str(self._tempdir),
            env=env,
            check=False,
            capture_output=capture_output,
            text=True,
        )

    def _build_env(
        self,
        env_file: Path | None,
        *,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()

        # Load .env file if provided and exists
        if env_file and env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip())

        # PYTHONPATH: CWD first (so impl: "myapp.x" resolves), then tempdir
        cwd = str(Path.cwd())
        tempdir = str(self._tempdir)
        existing = env.get("PYTHONPATH", "")
        parts = [p for p in [cwd, tempdir, existing] if p]
        env["PYTHONPATH"] = os.pathsep.join(parts)

        env["ABP_THREAD_ID"] = self._thread_id
        env.setdefault("ABP_TOOL_APPROVAL_MODE", "deny")
        env.setdefault("ABP_TRACE_FILE", str(self._tempdir / "abp_trace.json"))
        if extra_env:
            env.update(extra_env)
        return env

    def _cleanup(self) -> None:
        if self._tempdir and self._tempdir.exists():
            shutil.rmtree(self._tempdir, ignore_errors=True)
