"""Tests for checked-in example blueprints."""

from pathlib import Path

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

EXAMPLES = Path(__file__).parent.parent / "examples"


def test_prd_factory_example_loads_and_compiles():
    raw = load_blueprint_yaml(EXAMPLES / "prd-factory.yml")
    spec = BlueprintSpec.model_validate(raw)
    ir = compile_blueprint(spec)

    assert spec.blueprint.name == "prd-factory"
    assert ir.artifacts["prd_doc"].path == "artifacts/prd.md"
    assert ir.artifacts["prd_doc"].metadata == {"kind": "prd", "audience": "product"}
    assert ir.artifact_owners == {"writer": ["prd_doc"]}
    assert ir.harness is not None
    assert ir.harness.scenarios[0].expected.artifacts == ["prd_doc"]


def test_prd_factory_example_generates_langgraph_artifact_runtime():
    raw = load_blueprint_yaml(EXAMPLES / "prd-factory.yml")
    spec = BlueprintSpec.model_validate(raw)
    files = LangGraphGenerator().generate(compile_blueprint(spec))

    assert '"prd_doc"' in files["nodes.py"]
    assert '"contract": \'prd_contract\'' in files["nodes.py"]
    assert '"metadata": {\'kind\': \'prd\', \'audience\': \'product\'}' in files["nodes.py"]
    assert '"contract_schema":' in files["nodes.py"]
    assert "artifact_written" in files["nodes.py"]


def test_incident_response_example_compiles_with_workflow_nodes():
    raw = load_blueprint_yaml(EXAMPLES / "incident-response.yml")
    spec = BlueprintSpec.model_validate(raw)
    ir = compile_blueprint(spec)

    ids = {n.id for n in ir.nodes}
    # parallel + handoff survive, subgraph flattens to namespaced nodes
    assert {"analyze", "logs", "metrics", "page_oncall"} <= ids
    assert {"triage__entry", "triage__write", "triage__exit"} <= ids
    assert "triage__raw_findings" in ir.state.fields


def test_incident_response_example_generates_runnable_modules():
    import ast

    raw = load_blueprint_yaml(EXAMPLES / "incident-response.yml")
    spec = BlueprintSpec.model_validate(raw)
    files = LangGraphGenerator().generate(compile_blueprint(spec))

    for name in ("nodes.py", "graph.py", "main.py"):
        ast.parse(files[name])
    assert "_deliver_handoff_console" in files["nodes.py"]
    assert '"parallel_started"' in files["nodes.py"]
