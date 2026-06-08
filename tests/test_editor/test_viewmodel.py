"""Tests for the editor graph view-model (phase E1)."""

from pathlib import Path
from typing import Any

import pytest

from agent_blueprint.editor.viewmodel import END_ID, START_ID, build_view_model
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml

_BASIC = """\
blueprint:
  name: "vm-basic"
  version: "1.0"

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append

agents:
  assistant:
    model: "anthropic/claude-sonnet-4-6"
    system_prompt: "Help."
    tools: [lookup]

tools:
  lookup:
    type: function
    description: "Look something up"

graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
"""

_CONDITIONAL = """\
blueprint:
  name: "vm-conditional"
  version: "1.0"

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    verdict:
      type: string
      default: null

agents:
  triage:
    model: "openai/gpt-4o"
    system_prompt: "Triage."
  fixer:
    model: "openai/gpt-4o"
    system_prompt: "Fix."

graph:
  entry_point: triage
  nodes:
    triage:
      agent: triage
    fixer:
      agent: fixer
  edges:
    - from: triage
      to:
        - condition: "state.verdict == 'fix'"
          target: fixer
        - default: END
    - from: fixer
      to: END
"""

_SUPERVISOR = """\
blueprint:
  name: "vm-supervisor"
  version: "1.0"

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append

agents:
  boss:
    model: "openai/gpt-4o"
    system_prompt: "Delegate."
  worker_a:
    model: "openai/gpt-4o"
    system_prompt: "Work."
  worker_b:
    model: "openai/gpt-4o"
    system_prompt: "Work."

graph:
  entry_point: boss
  nodes:
    boss:
      type: supervisor
      agent: boss
      workers: [a, b]
      max_iterations: 4
    a:
      agent: worker_a
    b:
      agent: worker_b
"""

_PARALLEL_SUBGRAPH = """\
blueprint:
  name: "vm-parallel-subgraph"
  version: "1.0"

state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    notes:
      type: string
      default: null

agents:
  splitter:
    model: "openai/gpt-4o"
    system_prompt: "Split."
  left:
    model: "openai/gpt-4o"
    system_prompt: "Left."
  right:
    model: "openai/gpt-4o"
    system_prompt: "Right."
  joiner:
    model: "openai/gpt-4o"
    system_prompt: "Join."
  inner:
    model: "openai/gpt-4o"
    system_prompt: "Inner."

subgraphs:
  wrap_up:
    entry_point: polish
    nodes:
      polish:
        agent: inner
    edges:
      - from: polish
        to: END

graph:
  entry_point: fan
  nodes:
    fan:
      type: parallel
      branches: [left, right]
      join: merge
    left:
      agent: left
    right:
      agent: right
    merge:
      agent: joiner
    finish:
      type: subgraph
      ref: wrap_up
      input_map: {notes: notes}
      output_map: {notes: notes}
  edges:
    - from: merge
      to: finish
    - from: finish
      to: END
"""


@pytest.fixture
def vm_builder(tmp_path: Path) -> Any:
    def build(yaml_text: str) -> dict[str, Any]:
        path = tmp_path / "bp.yml"
        path.write_text(yaml_text, encoding="utf-8")
        spec = BlueprintSpec.model_validate(load_blueprint_yaml(path))
        return build_view_model(spec)

    return build


def _node(vm: dict[str, Any], node_id: str) -> dict[str, Any]:
    matches = [n for n in vm["nodes"] if n["id"] == node_id]
    assert matches, f"node {node_id!r} not in {[n['id'] for n in vm['nodes']]}"
    return matches[0]


def _edges(vm: dict[str, Any], source: str, target: str) -> list[dict[str, Any]]:
    return [e for e in vm["edges"] if e["source"] == source and e["target"] == target]


def test_basic_nodes_resolved_llm_and_terminals(vm_builder) -> None:
    vm = vm_builder(_BASIC)
    assistant = _node(vm, "assistant")
    assert assistant["type"] == "agent"
    assert assistant["provider"] == "anthropic"
    assert assistant["model"] == "claude-sonnet-4-6"
    assert assistant["tools"] == ["lookup"]
    assert _node(vm, START_ID)["type"] == "start"
    assert _node(vm, END_ID)["type"] == "end"
    assert _edges(vm, START_ID, "assistant")[0]["kind"] == "entry"
    assert _edges(vm, "assistant", END_ID)[0]["kind"] == "normal"


def test_conditional_edges_carry_condition_and_default_labels(vm_builder) -> None:
    vm = vm_builder(_CONDITIONAL)
    cond = _edges(vm, "triage", "fixer")[0]
    assert cond["kind"] == "conditional"
    assert cond["label"] == "state.verdict == 'fix'"
    default = _edges(vm, "triage", END_ID)[0]
    assert default["kind"] == "default"
    assert default["label"] == "default"
    # Single unconditional target gets no "default" label noise.
    assert _edges(vm, "fixer", END_ID)[0]["label"] is None


def test_supervisor_delegation_return_and_on_finish(vm_builder) -> None:
    vm = vm_builder(_SUPERVISOR)
    boss = _node(vm, "boss")
    assert boss["type"] == "supervisor"
    assert boss["workers"] == ["a", "b"]
    assert boss["max_iterations"] == 4
    for worker in ("a", "b"):
        assert _edges(vm, "boss", worker)[0]["kind"] == "delegation"
        assert _edges(vm, worker, "boss")[0]["kind"] == "return"
    finish = _edges(vm, "boss", END_ID)[0]
    assert finish["label"] == "on finish"


def test_parallel_fan_out_and_join_edges(vm_builder) -> None:
    vm = vm_builder(_PARALLEL_SUBGRAPH)
    assert _node(vm, "fan")["type"] == "parallel"
    for branch in ("left", "right"):
        assert _edges(vm, "fan", branch)[0]["kind"] == "parallel"
        assert _edges(vm, branch, "merge")[0]["kind"] == "parallel"


def test_subgraph_renders_as_group_with_namespaced_children(vm_builder) -> None:
    vm = vm_builder(_PARALLEL_SUBGRAPH)
    group = _node(vm, "finish")
    assert group["type"] == "subgraph"
    assert group["ref"] == "wrap_up"
    assert group["expanded"] is True
    polish = _node(vm, "finish:polish")
    assert polish["parent"] == "finish"
    assert polish["entry"] is True  # subgraph entry point is flagged
    # Internal END routes to a terminal inside the group, not the global END.
    inner_end = _node(vm, "finish:__end__")
    assert inner_end["parent"] == "finish"
    assert _edges(vm, "finish:polish", "finish:__end__")
    # Parent-graph edges connect to the group node itself.
    assert _edges(vm, "merge", "finish")
    assert _edges(vm, "finish", END_ID)


def test_real_edges_carry_ops_ref_synthetic_do_not(vm_builder) -> None:
    vm = vm_builder(_CONDITIONAL)
    cond = _edges(vm, "triage", "fixer")[0]
    assert cond["ref"] == {
        "graph": "graph",
        "from": "triage",
        "target": "fixer",
        "condition": "state.verdict == 'fix'",
    }
    default = _edges(vm, "triage", END_ID)[0]
    assert default["ref"] == {
        "graph": "graph",
        "from": "triage",
        "target": "END",
        "condition": None,
    }
    # The synthetic START -> entry edge does not exist in graph.edges.
    assert _edges(vm, START_ID, "triage")[0]["ref"] is None


def test_nodes_carry_graph_ref_and_vm_lists_agents(vm_builder) -> None:
    vm = vm_builder(_CONDITIONAL)
    assert _node(vm, "triage")["graph_ref"] == "graph"
    assert vm["agents"] == ["triage", "fixer"]


def test_vm_lists_subgraph_names_for_add_node_dialog(vm_builder) -> None:
    # The add-node dialog picks a subgraph node's `ref` from this list.
    assert vm_builder(_PARALLEL_SUBGRAPH)["subgraphs"] == ["wrap_up"]
    assert vm_builder(_CONDITIONAL)["subgraphs"] == []


def test_subgraph_scope_in_refs(vm_builder) -> None:
    vm = vm_builder(_PARALLEL_SUBGRAPH)
    assert _node(vm, "finish:polish")["graph_ref"] == "subgraphs.wrap_up"
    assert _node(vm, "finish")["graph_ref"] == "graph"  # the subgraph *node* lives in the parent
    internal = _edges(vm, "finish:polish", "finish:__end__")[0]
    assert internal["ref"] == {
        "graph": "subgraphs.wrap_up",
        "from": "polish",
        "target": "END",
        "condition": None,
    }
    # Supervisor/parallel synthetic edges never carry a ref.
    assert _edges(vm, "fan", "left")[0]["ref"] is None


def test_nodes_carry_config_payloads_for_forms(vm_builder) -> None:
    vm = vm_builder(_BASIC)
    assistant = _node(vm, "assistant")
    assert assistant["config"]["type"] == "agent"
    assert assistant["config"]["retry"]["max_attempts"] == 1  # defaults filled in
    assert assistant["agent_config"]["model"] == "anthropic/claude-sonnet-4-6"
    assert assistant["agent_config"]["tools"] == ["lookup"]
    assert vm["tools"] == ["lookup"]
    # Terminals are synthetic and carry no config.
    assert "config" not in _node(vm, START_ID)


def test_nodes_carry_runtime_ids_for_trace_mapping(vm_builder) -> None:
    # Trace events carry flattened-graph ids; the canvas maps them back via
    # runtime_id, which mirrors the compiler's "__" namespacing chain.
    vm = vm_builder(_PARALLEL_SUBGRAPH)
    assert _node(vm, "fan")["runtime_id"] == "fan"
    assert _node(vm, "finish:polish")["runtime_id"] == "finish__polish"
