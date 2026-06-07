"""Tests for editor diagnostics: lint position mapping + compile-error findings."""

from pathlib import Path

from ruamel.yaml import YAML

from agent_blueprint.editor.diagnostics import lint_with_positions, locate_in_yaml
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

_DOC = """\
graph:
  entry_point: a
  nodes:
    a:
      agent: a
  edges:
    - from: a
      to:
        - condition: "state.x == 1"
          target: a
        - default: END
state:
  fields:
    x:
      type: integer
"""

# Valid spec that fails compilation: subgraph reference cycle s1 -> s2 -> s1.
_REF_CYCLE = """\
blueprint:
  name: "ref-cycle"
  version: "1.0"

state:
  fields:
    notes:
      type: string
      default: null

subgraphs:
  s1:
    entry_point: hop
    nodes:
      hop:
        type: subgraph
        ref: s2
        input_map: {notes: notes}
        output_map: {notes: notes}
  s2:
    entry_point: hop
    nodes:
      hop:
        type: subgraph
        ref: s1
        input_map: {notes: notes}
        output_map: {notes: notes}

graph:
  entry_point: entry
  nodes:
    entry:
      type: subgraph
      ref: s1
      input_map: {notes: notes}
      output_map: {notes: notes}
  edges:
    - from: entry
      to: END
"""


def _load_doc() -> object:
    return YAML().load(_DOC)


def test_locate_nested_mapping_key() -> None:
    line, col = locate_in_yaml(_load_doc(), "graph.nodes.a")
    assert (line, col) == (4, 5)


def test_locate_indexed_path_segments() -> None:
    # graph.edges[0] → the first list item; .to[1] → the default target.
    line, _col = locate_in_yaml(_load_doc(), "graph.edges[0]")
    assert line == 7
    line, _col = locate_in_yaml(_load_doc(), "graph.edges[0].to[1]")
    assert line == 11


def test_locate_unknown_path_degrades_to_none() -> None:
    doc = _load_doc()
    assert locate_in_yaml(doc, "graph.nodes.missing") == (None, None)
    assert locate_in_yaml(doc, "graph.edges[9]") == (None, None)
    assert locate_in_yaml(doc, "graph.entry_point.too_deep") == (None, None)
    assert locate_in_yaml(doc, "") == (None, None)
    assert locate_in_yaml(None, "graph") == (None, None)


def test_compile_error_surfaces_as_synthetic_finding(tmp_path: Path) -> None:
    path = tmp_path / "cycle.yml"
    path.write_text(_REF_CYCLE, encoding="utf-8")
    spec = BlueprintSpec.model_validate(load_blueprint_yaml(path))
    findings = lint_with_positions(spec, _REF_CYCLE)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["code"] == "compile-error"
    assert finding["severity"] == "error"
    assert "cycle" in finding["message"]
