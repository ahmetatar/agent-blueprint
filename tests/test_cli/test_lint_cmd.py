"""Tests for abp lint command."""

from pathlib import Path

from typer.testing import CliRunner

from agent_blueprint.cli.app import app


runner = CliRunner()


def _write_blueprint(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "agent.yml"
    path.write_text(content, encoding="utf-8")
    return path


class TestLintCli:
    def test_reports_no_findings_for_clean_blueprint(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-clean"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    route:
      type: string
      default: null
agents:
  router:
    model: "gpt-4o"
graph:
  entry_point: router
  nodes:
    router:
      agent: router
  edges:
    - from: router
      to: END
contracts:
  nodes:
    router:
      produces: [route]
      output_contract: route_payload
  outputs:
    route_payload:
      type: object
      required: [route]
      properties:
        route:
          type: string
output:
  schema:
    route:
      type: string
      required: true
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 0
        assert "No lint findings" in result.output
        assert "0 error(s), 0 warning(s)" in result.output

    def test_exits_non_zero_for_unreachable_node_and_missing_default(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-errors"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
agents:
  router:
    model: "gpt-4o"
graph:
  entry_point: router
  nodes:
    router:
      agent: router
    worker:
      agent: router
    orphan:
      agent: router
  edges:
    - from: router
      to:
        - condition: "state.route == 'worker'"
          target: worker
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 1
        assert "unreachable-node" in result.output
        assert "missing-default-route" in result.output
        assert "(auto-fixable)" in result.output
        assert "graph.nodes.orphan" in result.output

    def test_reports_warning_for_dead_state_field(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-warning"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    unused_flag:
      type: boolean
      default: false
agents:
  assistant:
    model: "gpt-4o"
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 0
        assert "dead-state-field" in result.output
        assert "unused_flag" in result.output
        assert "0 error(s), 1 warning(s)" in result.output

    def test_reports_condition_overlap_ambiguity_warning(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-condition-overlap"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    route:
      type: string
      default: null
agents:
  router:
    model: "gpt-4o"
graph:
  entry_point: router
  nodes:
    router:
      agent: router
    billing:
      agent: router
    general:
      agent: router
  edges:
    - from: router
      to:
        - condition: "state.route == 'billing'"
          target: billing
        - condition: "state.route in ['billing', 'general']"
          target: general
        - default: END
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 0
        assert "condition-overlap-ambiguity" in result.output
        assert "route order will decide" in result.output

    def test_reports_compound_condition_overlap_warning(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-compound-condition-overlap"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    route:
      type: string
      default: null
    priority:
      type: string
      default: normal
agents:
  router:
    model: "gpt-4o"
graph:
  entry_point: router
  nodes:
    router:
      agent: router
    billing:
      agent: router
    urgent:
      agent: router
  edges:
    - from: router
      to:
        - condition: "state.route == 'billing' and state.priority != 'low'"
          target: billing
        - condition: "state.route in ['billing', 'sales']"
          target: urgent
        - default: END
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 0
        assert "condition-overlap-ambiguity" in result.output
        assert "billing" in result.output
        assert "urgent" in result.output

    def test_reports_partially_analyzable_condition_warning(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-partial-condition-analysis"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    score:
      type: number
      default: 0
agents:
  router:
    model: "gpt-4o"
graph:
  entry_point: router
  nodes:
    router:
      agent: router
    review:
      agent: router
  edges:
    - from: router
      to:
        - condition: "state.score >= 0.8"
          target: review
        - default: END
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 0
        assert "condition-partially-analyzable" in result.output
        assert "valid and portable" in result.output

    def test_reports_conflicting_contracts_as_errors(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-contract-errors"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    request_id:
      type: string
      default: null
agents:
  assistant:
    model: "gpt-4o"
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
contracts:
  state:
    immutable_fields: [request_id]
  nodes:
    assistant:
      produces: [request_id]
      forbids_mutation: [request_id]
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 1
        assert "conflicting-node-contract" in result.output
        assert "immutable-produced-field" in result.output

    def test_reports_unused_output_contract_as_warning(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-unused-contract"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
agents:
  assistant:
    model: "gpt-4o"
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
contracts:
  outputs:
    never_used:
      type: object
      properties:
        answer:
          type: string
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 0
        assert "unused-output-contract" in result.output
        assert "(auto-fixable)" in result.output
        assert "contracts.outputs.never_used" in result.output

    def test_lint_auto_fix_adds_missing_default_route(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-auto-fix-default"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    route:
      type: string
      default: null
agents:
  router:
    model: "gpt-4o"
graph:
  entry_point: router
  nodes:
    router:
      agent: router
    worker:
      agent: router
  edges:
    - from: router
      to:
        - condition: "state.route == 'worker'"
          target: worker
    - from: worker
      to: END
output:
  schema:
    route:
      type: string
      required: true
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint), "--auto-fix"])
        assert result.exit_code == 0
        assert "Applied 1 auto-fix(es)" in result.output
        assert "No lint findings" in result.output
        assert "default: END" in blueprint.read_text(encoding="utf-8")

    def test_fix_command_removes_unused_output_contract(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "fix-unused-contract"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
agents:
  assistant:
    model: "gpt-4o"
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
contracts:
  outputs:
    never_used:
      type: object
      properties:
        answer:
          type: string
""",
        )

        result = runner.invoke(app, ["fix", str(blueprint)])
        assert result.exit_code == 0
        assert "Applied 1 auto-fix(es)" in result.output
        assert "No lint findings" in result.output
        content = blueprint.read_text(encoding="utf-8")
        assert "never_used" not in content

    def test_invalid_state_invariant_surfaces_as_compilation_error(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-bad-invariant"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    count:
      type: integer
      default: 0
agents:
  assistant:
    model: "gpt-4o"
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
contracts:
  state:
    invariants:
      - "len(state.messages) > 0"
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 1
        output = result.output + str(result.exception or "")
        assert "Compilation error" in output or "contracts.state.invariants[0]" in output

    def test_valid_state_invariant_passes_lint(self, tmp_path):
        blueprint = _write_blueprint(
            tmp_path,
            """\
blueprint:
  name: "lint-good-invariant"
state:
  fields:
    messages:
      type: "list[message]"
      reducer: append
    count:
      type: integer
      default: 0
agents:
  assistant:
    model: "gpt-4o"
graph:
  entry_point: assistant
  nodes:
    assistant:
      agent: assistant
  edges:
    - from: assistant
      to: END
contracts:
  state:
    invariants:
      - "state.count >= 0"
""",
        )

        result = runner.invoke(app, ["lint", str(blueprint)])
        assert result.exit_code == 0
        assert "No lint findings" in result.output
