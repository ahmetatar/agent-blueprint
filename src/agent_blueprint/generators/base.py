"""Base generator protocol."""

from abc import ABC, abstractmethod
from pathlib import Path

from agent_blueprint.ir.compiler import AgentGraph


class BaseGenerator(ABC):
    """Abstract base class for all code generators."""

    @abstractmethod
    def generate(self, ir: AgentGraph) -> dict[str, str]:
        """Generate code files from an AgentGraph IR.

        Returns a dict mapping relative file paths to file contents.
        """
        ...
