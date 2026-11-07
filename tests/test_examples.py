"""Keep the blueprints under examples/ valid and lint-error free.

The examples are what new users copy first, so a schema change that breaks
them must fail CI. Running their harness scenarios needs the generated
project's dependencies (LangGraph), so here we stop at validate + compile +
lint; `abp test --install` covers the runtime path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
EXAMPLES = sorted(EXAMPLES_DIR.glob("*/*.agents.yaml"))


def test_examples_exist() -> None:
    assert EXAMPLES, f"no *.agents.yaml blueprints found under {EXAMPLES_DIR}"


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.parent.name)
def test_example_compiles_without_lint_errors(path: Path) -> None:
    spec = BlueprintSpec.model_validate(load_blueprint_yaml(path))
    ir = compile_blueprint(spec)
    errors = [f for f in lint_blueprint(spec, ir) if f.severity.value == "error"]
    assert not errors, errors
    assert ir.harness is not None and ir.harness.scenarios, "examples must ship scenarios"
