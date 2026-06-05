"""Tests for the persisted trace store and eval-dataset export."""

import json
from pathlib import Path

from agent_blueprint.eval_runner import load_eval_dataset
from agent_blueprint.harness_runner import ScenarioResult
from agent_blueprint.trace_store import (
    TRACE_RECORD_SCHEMA_VERSION,
    build_case_from_record,
    build_trace_record,
    export_records_to_dataset,
    list_trace_records,
    sanitize_run_id,
    save_trace_record,
)


def _manifest(*, run_id: str = "case-1", blueprint: str = "test-agent") -> dict:
    return {
        "schema_version": "1.0",
        "run": {
            "run_id": run_id,
            "blueprint": blueprint,
            "blueprint_version": "1.0",
            "mode": "mock",
            "seed": 42,
        },
        "trace": [
            {"event": "node_started", "node": "router"},
            {"event": "tool_called", "tool": "lookup_invoice"},
            {"event": "node_finished", "node": "router"},
            {"event": "node_finished", "node": "billing"},
            {"event": "run_finished", "metadata": {"status": "success"}},
        ],
        "replay": {
            "llm_outputs": {"router": [{"content": "billing"}]},
            "tool_outputs": {"lookup_invoice": [{"result": {"status": "paid"}}]},
        },
        "final_state": {"route": "billing"},
    }


def _result(*, passed: bool, manifest: dict | None) -> ScenarioResult:
    return ScenarioResult(
        scenario_id="case-1",
        passed=passed,
        returncode=0,
        failures=[] if passed else ["route mismatch: expected 'billing', ended on 'support'"],
        trace_manifest=manifest,
    )


def _record(*, passed: bool = False, manifest: dict | None = None, saved_at: str = "2026-06-05T10:00:00Z") -> dict:
    return build_trace_record(
        scenario_id="case-1",
        input={"message": "refund invoice 123"},
        result=_result(passed=passed, manifest=manifest if manifest is not None else _manifest()),
        saved_at=saved_at,
    )


class TestTraceRecord:
    def test_sanitize_run_id_replaces_illegal_chars(self):
        assert sanitize_run_id("a:b/c d") == "a_b_c_d"
        assert sanitize_run_id("") == "run"

    def test_build_trace_record_failed(self):
        record = _record(passed=False)
        assert record["schema_version"] == TRACE_RECORD_SCHEMA_VERSION
        assert record["status"] == "failed"
        assert record["blueprint"] == "test-agent"
        assert record["seed"] == 42
        assert record["input"] == {"message": "refund invoice 123"}
        assert record["failures"]
        assert record["manifest"]["replay"]["llm_outputs"]

    def test_build_trace_record_passed(self):
        record = _record(passed=True)
        assert record["status"] == "passed"
        assert record["failures"] == []

    def test_build_trace_record_handles_none_manifest(self):
        result = ScenarioResult(
            scenario_id="case-1", passed=False, returncode=1,
            failures=["boom"], trace_manifest=None,
        )
        record = build_trace_record(
            scenario_id="case-1", input={"message": "hi"}, result=result,
        )
        assert record["status"] == "failed"
        assert record["blueprint"] == ""
        assert record["run_id"] == "case-1"
        assert record["manifest"] == {}

    def test_save_trace_record_writes_unique_filenames(self, tmp_path):
        record = _record()
        first = save_trace_record(record, store_dir=tmp_path)
        second = save_trace_record(record, store_dir=tmp_path)
        assert first != second
        assert json.loads(first.read_text(encoding="utf-8"))["scenario_id"] == "case-1"
        assert json.loads(second.read_text(encoding="utf-8"))["scenario_id"] == "case-1"


class TestListTraceRecords:
    def test_filters_status(self, tmp_path):
        save_trace_record(_record(passed=False), store_dir=tmp_path)
        save_trace_record(_record(passed=True), store_dir=tmp_path)
        failed = list_trace_records(store_dir=tmp_path, status="failed")
        assert len(failed) == 1
        assert failed[0]["status"] == "failed"
        assert len(list_trace_records(store_dir=tmp_path)) == 2

    def test_filters_blueprint(self, tmp_path):
        save_trace_record(_record(), store_dir=tmp_path)
        save_trace_record(
            _record(manifest=_manifest(blueprint="other-agent")), store_dir=tmp_path
        )
        records = list_trace_records(store_dir=tmp_path, blueprint="other-agent")
        assert len(records) == 1
        assert records[0]["blueprint"] == "other-agent"

    def test_skips_unparseable_and_unknown_schema(self, tmp_path):
        save_trace_record(_record(), store_dir=tmp_path)
        (tmp_path / "junk.json").write_text("{not json", encoding="utf-8")
        bad_schema = _record()
        bad_schema["schema_version"] = "99"
        (tmp_path / "future.json").write_text(json.dumps(bad_schema), encoding="utf-8")
        records = list_trace_records(store_dir=tmp_path)
        assert len(records) == 1

    def test_missing_dir_returns_empty(self, tmp_path):
        assert list_trace_records(store_dir=tmp_path / "nope") == []


class TestBuildCaseFromRecord:
    def test_failed_record_has_empty_expected_and_fixtures(self):
        case = build_case_from_record(_record(passed=False), golden=False)
        assert case["expected"] == {}
        assert case["llm_mode"] == "mock"
        assert case["tool_mode"] == "stub"
        assert case["seed"] == 42
        assert case["fixtures"]["llm_outputs"]["router"] == [{"content": "billing"}]
        assert case["metadata"]["source_run_id"] == "case-1"
        assert case["metadata"]["original_status"] == "failed"
        assert "error" in case["metadata"]
        assert case["id"].startswith("case-1__")

    def test_golden_fills_route_and_tools(self):
        case = build_case_from_record(_record(passed=True), golden=True)
        assert case["expected"]["route"] == "billing"
        assert case["expected"]["tools_called"] == ["lookup_invoice"]
        assert "note" not in case["metadata"]

    def test_golden_omits_empty_tools(self):
        manifest = _manifest()
        manifest["trace"] = [{"event": "node_finished", "node": "assistant"}]
        case = build_case_from_record(_record(passed=True, manifest=manifest), golden=True)
        assert case["expected"] == {"route": "assistant"}

    def test_case_ids_differ_per_saved_at(self):
        first = build_case_from_record(_record(saved_at="2026-06-05T10:00:00Z"), golden=False)
        second = build_case_from_record(_record(saved_at="2026-06-05T11:00:00Z"), golden=False)
        assert first["id"] != second["id"]


class TestExportRecordsToDataset:
    def test_export_writes_cases_yaml(self, tmp_path):
        output = tmp_path / "datasets" / "regressions.yaml"
        added, skipped = export_records_to_dataset([_record()], output=output, golden=False)
        assert (added, skipped) == (1, 0)
        assert output.exists()
        text = output.read_text(encoding="utf-8")
        assert "cases:" in text

    def test_export_merge_dedup_idempotent(self, tmp_path):
        output = tmp_path / "regressions.yaml"
        record = _record()
        assert export_records_to_dataset([record], output=output, golden=False) == (1, 0)
        assert export_records_to_dataset([record], output=output, golden=False) == (0, 1)

    def test_export_preserves_hand_edited_cases(self, tmp_path):
        output = tmp_path / "regressions.yaml"
        export_records_to_dataset([_record()], output=output, golden=False)
        data = yaml_load(output)
        data["cases"][0]["expected"] = {"route": "billing"}
        with output.open("w", encoding="utf-8") as handle:
            from agent_blueprint.utils.yaml_loader import yaml

            yaml.dump(data, handle)
        export_records_to_dataset([_record()], output=output, golden=False)
        assert yaml_load(output)["cases"][0]["expected"] == {"route": "billing"}

    def test_export_json_output(self, tmp_path):
        output = tmp_path / "regressions.json"
        export_records_to_dataset([_record()], output=output, golden=False)
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["cases"][0]["llm_mode"] == "mock"

    def test_export_roundtrips_through_load_eval_dataset(self, tmp_path):
        output = tmp_path / "regressions.yaml"
        export_records_to_dataset(
            [_record(passed=True)], output=output, golden=True
        )
        cases = load_eval_dataset(str(output), blueprint_dir=tmp_path)
        assert len(cases) == 1
        case = cases[0]
        assert case.input == {"message": "refund invoice 123"}
        assert case.expected.route == "billing"
        assert case.expected.tools_called == ["lookup_invoice"]
        assert case.fixtures.llm_outputs["router"] == [{"content": "billing"}]
        assert case.llm_mode == "mock"


def yaml_load(path: Path):
    from agent_blueprint.utils.yaml_loader import yaml

    return yaml.load(path.read_text(encoding="utf-8"))


class TestOriginTagging:
    def test_default_origin_is_harness(self):
        assert _record()["origin"] == "harness"

    def test_explicit_origin_is_recorded(self):
        record = build_trace_record(
            scenario_id="case-1",
            input={"message": "hi"},
            result=_result(passed=False, manifest=_manifest()),
            origin="eval",
        )
        assert record["origin"] == "eval"

    def test_list_filters_by_origin(self, tmp_path):
        save_trace_record(_record(), store_dir=tmp_path)
        eval_record = build_trace_record(
            scenario_id="case-1",
            input={"message": "hi"},
            result=_result(passed=False, manifest=_manifest()),
            origin="eval",
        )
        save_trace_record(eval_record, store_dir=tmp_path)
        assert len(list_trace_records(store_dir=tmp_path, origin="all")) == 2
        assert len(list_trace_records(store_dir=tmp_path, origin="eval")) == 1
        assert len(list_trace_records(store_dir=tmp_path, origin="harness")) == 1

    def test_legacy_record_without_origin_counts_as_harness(self, tmp_path):
        legacy = _record()
        del legacy["origin"]
        (tmp_path / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
        harness_records = list_trace_records(store_dir=tmp_path, origin="harness")
        assert len(harness_records) == 1
        assert list_trace_records(store_dir=tmp_path, origin="eval") == []
