"""Tests for deepened parallel/handoff generation."""

import ast

from agent_blueprint.generators.langgraph import LangGraphGenerator
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec


def generate(raw: dict) -> dict[str, str]:
    ir = compile_blueprint(BlueprintSpec.model_validate(raw))
    return LangGraphGenerator().generate(ir)


def _node_body(nodes_py: str, func: str) -> str:
    return nodes_py.split(f"def {func}(")[1].split("\ndef ")[0]


def _parallel_spec() -> dict:
    return {
        "blueprint": {"name": "par"},
        "state": {"fields": {
            "messages": {"type": "list[message]", "reducer": "append"},
            "findings": {"type": "list", "reducer": "append"},
        }},
        "agents": {
            "a1": {"model": "gpt-4o", "system_prompt": "x"},
            "a2": {"model": "gpt-4o", "system_prompt": "y"},
        },
        "graph": {
            "entry_point": "fan",
            "nodes": {
                "fan": {"type": "parallel", "branches": ["b1", "b2"], "join": "merge"},
                "b1": {"agent": "a1"},
                "b2": {"agent": "a2"},
                "merge": {"type": "function"},
            },
            "edges": [{"from": "merge", "to": "END"}],
        },
    }


def _handoff_spec(channel: str = "slack", **node_extra) -> dict:
    return {
        "blueprint": {"name": "ho"},
        "state": {"fields": {
            "messages": {"type": "list[message]", "reducer": "append"},
            "severity": {"type": "string"},
        }},
        "agents": {"a": {"model": "gpt-4o", "system_prompt": "x"}},
        "graph": {
            "entry_point": "triage",
            "nodes": {
                "triage": {"agent": "a"},
                "escalate": {
                    "type": "handoff",
                    "channel": channel,
                    "action": "page_oncall",
                    "message_template": "Severity {severity}: review needed",
                    **node_extra,
                },
            },
            "edges": [
                {"from": "triage", "to": [
                    {"condition": "state.severity == 'high'", "target": "escalate"},
                    {"default": "END"},
                ]},
                {"from": "escalate", "to": "END"},
            ],
        },
    }


class TestParallelGeneration:
    def test_parallel_finished_moved_to_join(self):
        nodes_py = generate(_parallel_spec())["nodes.py"]
        ast.parse(nodes_py)
        fan = _node_body(nodes_py, "node_fan")
        merge = _node_body(nodes_py, "node_merge")
        assert 'emit_trace_event(\n        "parallel_finished"' not in fan
        assert '"parallel_started"' in fan
        assert '"parallel_finished"' in merge

    def test_agent_join_also_emits_parallel_finished(self):
        raw = _parallel_spec()
        raw["agents"]["m"] = {"model": "gpt-4o", "system_prompt": "m"}
        raw["graph"]["nodes"]["merge"] = {"agent": "m"}
        nodes_py = generate(raw)["nodes.py"]
        merge = _node_body(nodes_py, "node_merge")
        assert '"parallel_finished"' in merge
        # parallel_finished is emitted before the join's own node_started
        assert merge.index("parallel_finished") < merge.index("node_started")

    def test_lint_flags_conflicting_branch_writes(self):
        raw = _parallel_spec()
        raw["state"]["fields"]["result"] = {"type": "string"}  # replace reducer
        raw["contracts"] = {"nodes": {
            "b1": {"produces": ["result"]},
            "b2": {"produces": ["result"]},
        }}
        spec = BlueprintSpec.model_validate(raw)
        ir = compile_blueprint(spec)
        findings = lint_blueprint(spec, ir)
        conflict = [f for f in findings if f.code == "parallel-branch-conflict"]
        assert len(conflict) == 1
        assert "result" in conflict[0].message

    def test_lint_allows_append_reducer_branch_writes(self):
        raw = _parallel_spec()
        raw["contracts"] = {"nodes": {
            "b1": {"produces": ["findings"]},
            "b2": {"produces": ["findings"]},
        }}
        spec = BlueprintSpec.model_validate(raw)
        ir = compile_blueprint(spec)
        findings = lint_blueprint(spec, ir)
        assert not [f for f in findings if f.code == "parallel-branch-conflict"]


class TestHandoffGeneration:
    def test_console_channel(self):
        files = generate(_handoff_spec("console"))
        nodes_py = files["nodes.py"]
        ast.parse(nodes_py)
        body = _node_body(nodes_py, "node_escalate")
        assert "_deliver_handoff_console" in body
        assert "_deliver_handoff_console" in nodes_py

    def test_slack_channel_and_env_example(self):
        files = generate(_handoff_spec("slack"))
        body = _node_body(files["nodes.py"], "node_escalate")
        assert "_deliver_handoff_slack" in body
        assert "ABP_HANDOFF_SLACK_WEBHOOK_URL=" in files[".env.example"]

    def test_webhook_channel_and_env_example(self):
        files = generate(_handoff_spec("webhook"))
        assert "_deliver_handoff_webhook" in files["nodes.py"]
        assert "ABP_HANDOFF_WEBHOOK_URL=" in files[".env.example"]

    def test_email_channel_and_env_example(self):
        files = generate(_handoff_spec("email"))
        assert "_deliver_handoff_email" in files["nodes.py"]
        env = files[".env.example"]
        for var in ("ABP_HANDOFF_SMTP_HOST=", "ABP_HANDOFF_EMAIL_FROM=", "ABP_HANDOFF_EMAIL_TO="):
            assert var in env

    def test_handoff_emits_trace_events(self):
        body = _node_body(generate(_handoff_spec())["nodes.py"], "node_escalate")
        assert '"node_started"' in body
        assert '"handoff_requested"' in body
        assert '"handoff_failed"' in body
        assert '"node_finished"' in body

    def test_delivery_guarded_by_tool_mode(self):
        body = _node_body(generate(_handoff_spec())["nodes.py"], "node_escalate")
        assert 'os.environ.get("ABP_TOOL_MODE", "live") == "live"' in body

    def test_message_template_rendered_from_state(self):
        body = _node_body(generate(_handoff_spec())["nodes.py"], "node_escalate")
        assert "_render_handoff_message" in body
        assert "Severity {severity}: review needed" in body

    def test_no_handoff_helpers_without_handoff_nodes(self):
        nodes_py = generate(_parallel_spec())["nodes.py"]
        assert "_deliver_handoff_" not in nodes_py
        assert "_render_handoff_message" not in nodes_py


class TestGeneratedHandoffRuntime:
    """Execute the generated helpers in-process (they are stdlib + httpx only)."""

    def _helpers_namespace(self, channel: str = "console") -> dict:
        nodes_py = generate(_handoff_spec(channel))["nodes.py"]
        # extract only the handoff helper block (module imports langchain etc.)
        start = nodes_py.index("class _HandoffStateDict")
        end = nodes_py.index("{% raw %}" if "{% raw %}" in nodes_py else "\ndef node_", start)
        snippet = "import os\nfrom typing import Any\n" + nodes_py[start:end]
        namespace: dict = {}
        exec(snippet, namespace)  # noqa: S102 — generated code under test
        return namespace

    def test_render_message_with_state(self):
        ns = self._helpers_namespace()
        out = ns["_render_handoff_message"](
            "Severity {severity}: review needed", {"severity": "high"}
        )
        assert out == "Severity high: review needed"

    def test_render_message_missing_field_keeps_placeholder(self):
        ns = self._helpers_namespace()
        out = ns["_render_handoff_message"]("Severity {severity}", {})
        assert out == "Severity {severity}"

    def test_console_delivery_prints(self, capsys):
        ns = self._helpers_namespace()
        ns["_deliver_handoff_console"]({"message": "ping"})
        assert "[HANDOFF] ping" in capsys.readouterr().out

    def test_missing_env_raises_clear_error(self, monkeypatch):
        import pytest

        ns = self._helpers_namespace()
        monkeypatch.delenv("ABP_HANDOFF_WEBHOOK_URL", raising=False)
        with pytest.raises(RuntimeError, match="ABP_HANDOFF_WEBHOOK_URL"):
            ns["_deliver_handoff_webhook"]({"message": "x"})
