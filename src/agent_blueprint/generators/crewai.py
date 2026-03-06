"""CrewAI code generator (stub - Phase 3)."""

from agent_blueprint.exceptions import GeneratorError
from agent_blueprint.generators.base import BaseGenerator
from agent_blueprint.ir.compiler import AgentGraph


class CrewAIGenerator(BaseGenerator):
    """Generates a CrewAI project from an AgentGraph IR."""

    def generate(self, ir: AgentGraph) -> dict[str, str]:
        raise GeneratorError(
            "CrewAI generator is not yet implemented. Coming in Phase 3.\n"
            "Use --target langgraph or --target plain for now."
        )
