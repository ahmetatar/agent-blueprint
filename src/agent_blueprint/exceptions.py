"""Custom exception hierarchy for agent-blueprint."""


class BlueprintError(Exception):
    """Base exception for all blueprint errors."""


class BlueprintValidationError(BlueprintError):
    """Raised when a blueprint YAML fails validation."""


class BlueprintCompilationError(BlueprintError):
    """Raised when the IR compiler encounters an unresolvable error."""


class ExpressionError(BlueprintError):
    """Raised when a condition expression cannot be parsed or compiled."""


class GeneratorError(BlueprintError):
    """Raised during code generation."""


class DeployerError(BlueprintError):
    """Raised during deployment."""
