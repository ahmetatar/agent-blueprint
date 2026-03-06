"""Base deployer protocol (Phase 4)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DeployResult:
    success: bool
    url: str | None = None
    message: str = ""


class BaseDeployer(ABC):
    """Abstract base class for all deployers."""

    @abstractmethod
    def check_prerequisites(self) -> bool:
        """Return True if required CLI tools and credentials are available."""
        ...

    @abstractmethod
    def deploy(self, code_dir: Path) -> DeployResult:
        """Package and deploy the generated code."""
        ...
