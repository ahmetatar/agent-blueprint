"""Byte-level round-trip tests for the editor's targeted ruamel ops (E2b).

Every test asserts the *full* dumped text, so comment, key-order, quoting,
and indentation preservation on untouched regions is checked byte-for-byte —
the core guarantee of the targeted-mutation write path.
"""

import glob
import io
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from agent_blueprint.editor.ops import EditOp, OpError, apply_ops
from agent_blueprint.utils.yaml_loader import load_blueprint_document, yaml

_OPS = TypeAdapter(list[EditOp])

# A compact blueprint exercising the edge sugar the ops must navigate:
# a conditional to-list with a `- default:` shorthand, and a scalar `to:`.
_FIXTURE = """\
# routing demo
blueprint:
  name: "ops-test"  # keep this comment
  version: "1.0"

graph:
  entry_point: router
  nodes:
    router:
      agent: router
    worker:  # the workhorse
      agent: worker
  edges:
    - from: router
      to:
        - condition: "state.route == 'worker'"
          target: worker
        - default: END
    # plain edge below
    - from: worker
      to: END
"""


def _apply(source: str, ops: list[dict[str, Any]], tmp_path: Path) -> str:
    path = tmp_path / "bp.yml"
    path.write_text(source, encoding="utf-8")
    document = load_blueprint_document(path)
    apply_ops(document, _OPS.validate_python(ops))
    buffer = io.StringIO()
    yaml.dump(document, buffer)
    return buffer.getvalue()


@pytest.mark.parametrize("example", sorted(glob.glob("examples/*.yml")))
def test_noop_roundtrip_is_byte_identical(example: str, tmp_path: Path) -> None:
    source = Path(example).read_text(encoding="utf-8")
    assert _apply(source, [], tmp_path) == source


def test_add_node_appends_to_nodes_mapping(tmp_path: Path) -> None:
    result = _apply(
        _FIXTURE,
        [{"op": "add_node", "node_id": "extra", "node": {"agent": "worker"}}],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        """\
    worker:  # the workhorse
      agent: worker
""",
        """\
    worker:  # the workhorse
      agent: worker
    extra:
      agent: worker
""",
    )


def test_add_node_duplicate_rejected(tmp_path: Path) -> None:
    with pytest.raises(OpError, match="already exists"):
        _apply(_FIXTURE, [{"op": "add_node", "node_id": "router", "node": {}}], tmp_path)


def test_add_edge_normalizes_scalar_to(tmp_path: Path) -> None:
    # worker's `to: END` is sugar for a single default target; extending it
    # must keep END as the default.
    result = _apply(
        _FIXTURE,
        [
            {
                "op": "add_edge",
                "from_node": "worker",
                "target": "router",
                "condition": "state.retry",
            }
        ],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        """\
    - from: worker
      to: END
""",
        """\
    - from: worker
      to:
        - default: END
        - condition: state.retry
          target: router
""",
    )


def test_add_edge_new_from_node_plain(tmp_path: Path) -> None:
    # No existing edge entry for the source → a fresh scalar-`to` entry.
    without_worker_edge = _FIXTURE.replace(
        """\
    # plain edge below
    - from: worker
      to: END
""",
        "",
    )
    result = _apply(
        without_worker_edge,
        [{"op": "add_edge", "from_node": "worker", "target": "END"}],
        tmp_path,
    )
    assert result == without_worker_edge.replace(
        """\
        - default: END
""",
        """\
        - default: END
    - from: worker
      to: END
""",
    )


def test_add_edge_duplicate_rejected(tmp_path: Path) -> None:
    with pytest.raises(OpError, match="already exists"):
        _apply(
            _FIXTURE,
            [
                {
                    "op": "add_edge",
                    "from_node": "router",
                    "target": "worker",
                    "condition": "state.route == 'worker'",
                }
            ],
            tmp_path,
        )


def test_remove_edge_conditional_target(tmp_path: Path) -> None:
    result = _apply(
        _FIXTURE,
        [
            {
                "op": "remove_edge",
                "from_node": "router",
                "target": "worker",
                "condition": "state.route == 'worker'",
            }
        ],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        """\
      to:
        - condition: "state.route == 'worker'"
          target: worker
        - default: END
""",
        """\
      to:
        - default: END
""",
    )


def test_remove_edge_default_shorthand_target(tmp_path: Path) -> None:
    # The `- default: END` shorthand matches target=END with no condition.
    # ruamel attaches a comment line to the *preceding* item, so the comment
    # that followed the deleted target goes with it — a known v1 cost.
    result = _apply(
        _FIXTURE,
        [{"op": "remove_edge", "from_node": "router", "target": "END"}],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        """\
        - default: END
    # plain edge below
""",
        "",
    )


def test_remove_edge_scalar_removes_whole_entry(tmp_path: Path) -> None:
    # The comment precedes the deleted entry, so it is attached to the entry
    # *before* it and survives the deletion.
    result = _apply(
        _FIXTURE,
        [{"op": "remove_edge", "from_node": "worker", "target": "END"}],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        """\
    - from: worker
      to: END
""",
        "",
    )


def test_remove_edge_missing_rejected(tmp_path: Path) -> None:
    with pytest.raises(OpError, match="not found"):
        _apply(
            _FIXTURE,
            [{"op": "remove_edge", "from_node": "router", "target": "nowhere"}],
            tmp_path,
        )


def test_remove_node_cascades_edges(tmp_path: Path) -> None:
    # Removing worker drops its node entry, its own edge entry, and the
    # conditional target pointing at it — leaving router's default route.
    result = _apply(
        _FIXTURE,
        [{"op": "remove_node", "node_id": "worker"}],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        """\
    worker:  # the workhorse
      agent: worker
""",
        "",
    ).replace(
        """\
        - condition: "state.route == 'worker'"
          target: worker
""",
        "",
    ).replace(
        """\
    - from: worker
      to: END
""",
        "",
    )


def test_remove_node_missing_rejected(tmp_path: Path) -> None:
    with pytest.raises(OpError, match="does not exist"):
        _apply(_FIXTURE, [{"op": "remove_node", "node_id": "ghost"}], tmp_path)


def test_set_field_creates_missing_mapping_levels(tmp_path: Path) -> None:
    result = _apply(
        _FIXTURE,
        [{"op": "set_field", "path": "graph.nodes.router.description", "value": "Routes"}],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        """\
    router:
      agent: router
""",
        """\
    router:
      agent: router
      description: Routes
""",
    )


def test_set_field_indexed_path(tmp_path: Path) -> None:
    result = _apply(
        _FIXTURE,
        [
            {
                "op": "set_field",
                "path": "graph.edges[0].to[0].condition",
                "value": "state.route == 'x'",
            }
        ],
        tmp_path,
    )
    # The embedded single quotes force the emitter to double-quote the new
    # scalar — matching the authored style of the line it replaces.
    assert result == _FIXTURE.replace(
        """\
        - condition: "state.route == 'worker'"
""",
        """\
        - condition: "state.route == 'x'"
""",
    )


def test_set_field_bad_index_rejected(tmp_path: Path) -> None:
    with pytest.raises(OpError, match="out of range"):
        _apply(
            _FIXTURE,
            [{"op": "set_field", "path": "graph.edges[9].to", "value": "END"}],
            tmp_path,
        )


def test_unknown_graph_scope_rejected(tmp_path: Path) -> None:
    with pytest.raises(OpError, match="unknown subgraph"):
        _apply(
            _FIXTURE,
            [{"op": "remove_node", "graph": "subgraphs.ghost", "node_id": "router"}],
            tmp_path,
        )
    with pytest.raises(OpError, match="invalid graph scope"):
        _apply(
            _FIXTURE,
            [{"op": "remove_node", "graph": "agents", "node_id": "router"}],
            tmp_path,
        )


_SUBGRAPH_FIXTURE = """\
blueprint:
  name: "ops-subgraph"

graph:
  entry_point: main
  nodes:
    main:
      agent: main
  edges:
    - from: main
      to: END

subgraphs:
  inner:
    entry_point: a
    nodes:
      a:
        agent: main
      b:
        agent: main
    edges:
      - from: a
        to: END
"""


def test_ops_scope_into_subgraph(tmp_path: Path) -> None:
    result = _apply(
        _SUBGRAPH_FIXTURE,
        [{"op": "add_edge", "graph": "subgraphs.inner", "from_node": "b", "target": "a"}],
        tmp_path,
    )
    assert result == _SUBGRAPH_FIXTURE.replace(
        """\
      - from: a
        to: END
""",
        """\
      - from: a
        to: END
      - from: b
        to: a
""",
    )


def test_unset_field_removes_key(tmp_path: Path) -> None:
    # The trailing blank line is comment data attached to the deleted key in
    # ruamel, so it goes with it (same semantics as deleted seq items).
    result = _apply(
        _FIXTURE,
        [{"op": "unset_field", "path": "blueprint.version"}],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        """\
  version: "1.0"

""",
        "",
    )


def test_unset_field_missing_key_is_noop(tmp_path: Path) -> None:
    for path in (
        "blueprint.description",  # parent exists, key doesn't
        "graph.nodes.ghost.description",  # parent doesn't exist
        "graph.edges[9].to",  # index out of range mid-path
    ):
        assert _apply(_FIXTURE, [{"op": "unset_field", "path": path}], tmp_path) == _FIXTURE


def test_unset_field_list_element_rejected(tmp_path: Path) -> None:
    with pytest.raises(OpError, match="list element"):
        _apply(_FIXTURE, [{"op": "unset_field", "path": "graph.edges[0]"}], tmp_path)


# -- retarget_edge (E4a) -------------------------------------------------------


def test_retarget_scalar_to(tmp_path: Path) -> None:
    result = _apply(
        _FIXTURE,
        [{"op": "retarget_edge", "from_node": "worker", "target": "END", "new_target": "router"}],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        "    - from: worker\n      to: END\n",
        "    - from: worker\n      to: router\n",
    )


def test_retarget_conditional_item_in_place(tmp_path: Path) -> None:
    # The conditional entry keeps its list position, its condition, and the
    # comments around it — only the target value changes.
    result = _apply(
        _FIXTURE,
        [
            {
                "op": "retarget_edge",
                "from_node": "router",
                "target": "worker",
                "condition": "state.route == 'worker'",
                "new_target": "END",
            }
        ],
        tmp_path,
    )
    assert result == _FIXTURE.replace(
        "          target: worker\n",
        "          target: END\n",
    )


def test_retarget_default_shorthand(tmp_path: Path) -> None:
    result = _apply(
        _FIXTURE,
        [{"op": "retarget_edge", "from_node": "router", "target": "END", "new_target": "worker"}],
        tmp_path,
    )
    assert result == _FIXTURE.replace("        - default: END\n", "        - default: worker\n")


def test_retarget_duplicate_is_rejected(tmp_path: Path) -> None:
    # Retargeting the default entry onto an unconditional duplicate of an
    # existing (target, condition) pair must be refused.
    source = _FIXTURE.replace(
        "        - default: END\n",
        "        - target: archive\n        - default: END\n",
    ).replace(
        "    worker:  # the workhorse\n",
        "    worker:  # the workhorse\n      agent: worker\n    archive:\n",
    )
    with pytest.raises(OpError, match="already exists"):
        _apply(
            source,
            [
                {
                    "op": "retarget_edge",
                    "from_node": "router",
                    "target": "END",
                    "new_target": "archive",
                }
            ],
            tmp_path,
        )


def test_retarget_missing_edge_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(OpError, match="not found"):
        _apply(
            _FIXTURE,
            [
                {
                    "op": "retarget_edge",
                    "from_node": "router",
                    "target": "nope",
                    "new_target": "worker",
                }
            ],
            tmp_path,
        )
