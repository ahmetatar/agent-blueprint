"""Tests for the harness scenario runner."""

import json
from pathlib import Path

from agent_blueprint.harness_runner import (
    extract_replay_fixtures,
    load_trace_manifest,
    merge_harness_fixtures,
    parse_runner_output,
    resolve_harness_trace_mode,
    run_harness_scenario,
)
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec
from agent_blueprint.models.harness import HarnessFixtures


def _build_ir(raw: dict):
    return compile_blueprint(BlueprintSpec.model_validate(raw))


class TestHarnessRunner:
    def test_resolve_harness_trace_mode(self):
        assert resolve_harness_trace_mode("mock", "stub") == "mock"
        assert resolve_harness_trace_mode("replay", "stub") == "stubbed"
        assert resolve_harness_trace_mode("replay", "live") == "live-tools"
        assert resolve_harness_trace_mode("replay", "replay") == "replay"
        assert resolve_harness_trace_mode("live", "live") == "live"

    def test_parse_runner_output_handles_structured_text(self):
        assert parse_runner_output('{"answer":"ok"}') == {"answer": "ok"}
        assert parse_runner_output("{'answer': 'ok'}") == {"answer": "ok"}
        assert parse_runner_output("plain output") == "plain output"

    def test_merge_harness_fixtures_applies_scenario_override(self):
        merged = merge_harness_fixtures(
            HarnessFixtures(
                llm_outputs={"assistant": [{"content": "default"}]},
                tool_outputs={"lookup_invoice": {"result": {"status": "paid"}}},
            ),
            HarnessFixtures(
                llm_outputs={"reviewer": [{"content": "override"}]},
                tool_outputs={"lookup_invoice": {"result": {"status": "overridden"}}},
            ),
        )
        assert sorted(merged["llm_outputs"]) == ["assistant", "reviewer"]
        assert merged["tool_outputs"]["lookup_invoice"]["result"]["status"] == "overridden"

    def test_extract_replay_fixtures_reads_replay_payload(self):
        fixtures = extract_replay_fixtures({
            "replay": {
                "llm_outputs": {"assistant": [{"content": "ok"}]},
                "tool_outputs": {"lookup_invoice": [{"result": {"status": "paid"}}]},
            }
        })
        assert fixtures["llm_outputs"]["assistant"][0]["content"] == "ok"
        assert fixtures["tool_outputs"]["lookup_invoice"][0]["result"]["status"] == "paid"

    def test_load_trace_manifest_reads_json_file(self, tmp_path):
        trace_file = tmp_path / "golden.json"
        trace_file.write_text('{"trace":[]}', encoding="utf-8")
        assert load_trace_manifest(str(trace_file)) == {"trace": []}

    def test_run_harness_scenario_checks_tools_and_outputs(self, monkeypatch):
        ir = _build_ir({
            "blueprint": {"name": "test"},
            "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
            "harness": {
                "defaults": {"llm_mode": "live", "tool_mode": "live"},
                "scenarios": [
                    {
                        "id": "happy_path",
                        "input": {"message": "hello"},
                        "expected": {
                            "tools_called": ["lookup_invoice"],
                            "outputs": {"answer": "ok"},
                        },
                    }
                ],
            },
        })

        from agent_blueprint.runners.local import LocalRunResult

        def fake_run_capture(self, user_input=None, **kwargs):
            assert user_input == "hello"
            return LocalRunResult(
                returncode=0,
                stdout='{"answer": "ok"}',
                stderr="",
                trace_file=None,
                trace_manifest={
                    "trace": [
                        {"event": "tool_called", "tool": "lookup_invoice"},
                    ]
                },
            )

        monkeypatch.setattr("agent_blueprint.runners.local.LocalRunner.run_capture", fake_run_capture)
        result = run_harness_scenario(ir, ir.harness.scenarios[0], install=False)  # type: ignore[union-attr]
        assert result.passed is True
        assert result.checks == ["tools_called", "outputs"]

    def test_run_harness_scenario_fails_unsupported_assertions(self, monkeypatch):
        ir = _build_ir({
            "blueprint": {"name": "test"},
            "graph": {"entry_point": "n", "nodes": {"n": {"type": "function"}}, "edges": []},
            "harness": {
                "defaults": {"llm_mode": "live", "tool_mode": "live"},
                "scenarios": [
                    {
                        "id": "route_case",
                        "input": {"message": "hello"},
                        "expected": {"route": "billing"},
                    }
                ],
            },
        })

        from agent_blueprint.runners.local import LocalRunResult

        def fake_run_capture(self, user_input=None, **kwargs):
            return LocalRunResult(
                returncode=0,
                stdout="ok",
                stderr="",
                trace_file=None,
                trace_manifest={"trace": []},
            )

        monkeypatch.setattr("agent_blueprint.runners.local.LocalRunner.run_capture", fake_run_capture)
        result = run_harness_scenario(ir, ir.harness.scenarios[0], install=False)  # type: ignore[union-attr]
        assert result.passed is False
        assert "Unsupported harness assertion(s)" in result.failures[0]

    def test_run_harness_scenario_passes_fixture_env_to_runner(self, monkeypatch):
        ir = _build_ir({
            "blueprint": {"name": "test"},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            "harness": {
                "defaults": {
                    "llm_mode": "mock",
                    "tool_mode": "stub",
                    "fixtures": {
                        "llm_outputs": {"assistant": [{"content": "hello from fixture"}]},
                        "tool_outputs": {"lookup_invoice": {"result": {"status": "paid"}}},
                    },
                },
                "scenarios": [
                    {
                        "id": "fixture_case",
                        "input": {"message": "hello"},
                        "expected": {},
                        "fixtures": {
                            "tool_outputs": {"issue_refund": {"result": {"approved": True}}},
                        },
                    }
                ],
            },
        })

        from agent_blueprint.runners.local import LocalRunResult

        captured_env: dict[str, str] = {}

        def fake_run_capture(self, user_input=None, **kwargs):
            nonlocal captured_env
            captured_env = kwargs["extra_env"]
            return LocalRunResult(
                returncode=0,
                stdout="ok",
                stderr="",
                trace_file=None,
                trace_manifest={"trace": []},
            )

        monkeypatch.setattr("agent_blueprint.runners.local.LocalRunner.run_capture", fake_run_capture)
        result = run_harness_scenario(ir, ir.harness.scenarios[0], install=False)  # type: ignore[union-attr]

        fixtures = json.loads(captured_env["ABP_HARNESS_FIXTURES"])
        assert result.passed is True
        assert result.warnings == []
        assert captured_env["ABP_LLM_MODE"] == "mock"
        assert captured_env["ABP_TOOL_MODE"] == "stub"
        assert fixtures["llm_outputs"]["assistant"][0]["content"] == "hello from fixture"
        assert fixtures["tool_outputs"]["lookup_invoice"]["result"]["status"] == "paid"
        assert fixtures["tool_outputs"]["issue_refund"]["result"]["approved"] is True

    def test_run_harness_scenario_loads_replay_trace_and_checks_diff(self, monkeypatch, tmp_path):
        golden_trace = {
            "schema_version": "1.0",
            "run": {
                "blueprint": "test",
                "blueprint_version": "1.0",
                "scenario_id": "replay_case",
                "mode": "live",
            },
            "trace": [
                {"sequence": 0, "event": "tool_called", "tool": "lookup_invoice"},
            ],
            "replay": {
                "llm_outputs": {"assistant": [{"content": "fixture"}]},
                "tool_outputs": {"lookup_invoice": [{"result": {"status": "paid"}}]},
            },
        }
        trace_path = tmp_path / "golden.json"
        trace_path.write_text(json.dumps(golden_trace), encoding="utf-8")

        ir = _build_ir({
            "blueprint": {"name": "test"},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            "harness": {
                "defaults": {"llm_mode": "replay", "tool_mode": "replay"},
                "scenarios": [
                    {
                        "id": "replay_case",
                        "input": {"message": "hello"},
                        "expected": {},
                        "replay_trace": str(trace_path),
                    }
                ],
            },
        })

        from agent_blueprint.runners.local import LocalRunResult

        captured_env: dict[str, str] = {}

        def fake_run_capture(self, user_input=None, **kwargs):
            nonlocal captured_env
            captured_env = kwargs["extra_env"]
            return LocalRunResult(
                returncode=0,
                stdout="ok",
                stderr="",
                trace_file=Path(trace_path),
                trace_manifest={
                    "schema_version": "1.0",
                    "run": {
                        "blueprint": "test",
                        "blueprint_version": "1.0",
                        "scenario_id": "replay_case",
                        "mode": "replay",
                    },
                    "trace": [
                        {"sequence": 0, "event": "tool_called", "tool": "lookup_invoice"},
                    ],
                    "replay": golden_trace["replay"],
                },
            )

        monkeypatch.setattr("agent_blueprint.runners.local.LocalRunner.run_capture", fake_run_capture)
        result = run_harness_scenario(ir, ir.harness.scenarios[0], install=False)  # type: ignore[union-attr]

        fixtures = json.loads(captured_env["ABP_HARNESS_FIXTURES"])
        assert result.passed is True
        assert "replay_trace" in result.checks
        assert fixtures["llm_outputs"]["assistant"][0]["content"] == "fixture"
        assert fixtures["tool_outputs"]["lookup_invoice"][0]["result"]["status"] == "paid"

    def test_run_harness_scenario_reports_replay_drift(self, monkeypatch, tmp_path):
        golden_trace = {
            "schema_version": "1.0",
            "run": {
                "blueprint": "test",
                "blueprint_version": "1.0",
                "scenario_id": "replay_case",
                "mode": "live",
            },
            "trace": [
                {"sequence": 0, "event": "tool_called", "tool": "lookup_invoice"},
            ],
            "replay": {},
        }
        trace_path = tmp_path / "golden.json"
        trace_path.write_text(json.dumps(golden_trace), encoding="utf-8")

        ir = _build_ir({
            "blueprint": {"name": "test"},
            "agents": {"assistant": {"model": "gpt-4o"}},
            "graph": {"entry_point": "assistant", "nodes": {"assistant": {"agent": "assistant"}}, "edges": []},
            "harness": {
                "defaults": {"llm_mode": "replay", "tool_mode": "replay"},
                "scenarios": [
                    {
                        "id": "replay_case",
                        "input": {"message": "hello"},
                        "expected": {},
                        "replay_trace": str(trace_path),
                    }
                ],
            },
        })

        from agent_blueprint.runners.local import LocalRunResult

        def fake_run_capture(self, user_input=None, **kwargs):
            return LocalRunResult(
                returncode=0,
                stdout="ok",
                stderr="",
                trace_file=None,
                trace_manifest={
                    "schema_version": "1.0",
                    "run": {
                        "blueprint": "test",
                        "blueprint_version": "1.0",
                        "scenario_id": "replay_case",
                        "mode": "replay",
                    },
                    "trace": [
                        {"sequence": 0, "event": "tool_called", "tool": "issue_refund"},
                    ],
                    "replay": {},
                },
            )

        monkeypatch.setattr("agent_blueprint.runners.local.LocalRunner.run_capture", fake_run_capture)
        result = run_harness_scenario(ir, ir.harness.scenarios[0], install=False)  # type: ignore[union-attr]
        assert result.passed is False
        assert "replay trace drift detected" in result.failures[0]
