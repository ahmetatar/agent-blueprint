"""Base deployer interface."""

import shlex
import subprocess
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DeployResult:
    success: bool
    url: str | None = None
    message: str = ""
    outputs: dict[str, str] = field(default_factory=dict)


class BaseDeployer(ABC):
    """Abstract base class for all cloud deployers."""

    #: Called with each child process right after spawn — lets a caller
    #: (e.g. the editor's task runner) terminate a long build on cancel.
    process_hook: Callable[[subprocess.Popen[str]], None] | None = None

    @abstractmethod
    def check_prerequisites(self) -> list[str]:
        """Return a list of error messages for unmet prerequisites.

        An empty list means all prerequisites are satisfied.
        """
        ...

    @abstractmethod
    def deploy(
        self,
        code_dir: Path,
        secrets: dict[str, str],
        *,
        image_tag: str,
        dry_run: bool = False,
    ) -> DeployResult:
        """Package and deploy the generated code directory."""
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _cmd(
        self,
        cmd: list[str],
        *,
        dry_run: bool = False,
        capture: bool = False,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str] | None:
        """Print and optionally run a shell command."""
        print(f"  $ {shlex.join(cmd)}")
        if dry_run:
            return None
        if self.process_hook is None:
            return subprocess.run(
                cmd,
                check=True,
                capture_output=capture,
                text=True,
                input=input,
            )
        # Popen (rather than subprocess.run) so the process handle can be
        # exposed via process_hook before we block — same check=True
        # semantics as above.
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if input is not None else None,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            text=True,
        )
        self.process_hook(proc)
        stdout, stderr = proc.communicate(input=input)
        if proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd, stdout, stderr)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)

    def _probe(self, cmd: list[str]) -> bool:
        """Return True if command exits with code 0."""
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    def _capture(self, cmd: list[str]) -> str:
        """Run command and return stripped stdout, or empty string on error."""
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return ""
