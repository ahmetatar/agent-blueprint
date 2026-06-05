"""Persisted trace records and the trace -> eval-dataset flywheel.

`abp test` / `abp gate` persist failing harness runs as small JSON records
under `.abp/traces/`. `abp traces export` converts stored records into eval
dataset cases (`{"cases": [...]}`) consumable by `abp eval` and `abp gate`,
so real failures become regression tests.

The record wraps the trace manifest because the original scenario input is
not part of the manifest itself.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_blueprint.harness_runner import ScenarioResult, extract_replay_fixtures
from agent_blueprint.utils.yaml_loader import yaml

TRACE_RECORD_SCHEMA_VERSION = "1"

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_run_id(run_id: str) -> str:
    """Make a run id safe for use in a filename (Windows included)."""
    return _SAFE_ID_RE.sub("_", run_id) or "run"


def build_trace_record(
    *,
    scenario_id: str,
    input: dict[str, Any],
    result: ScenarioResult,
    saved_at: str | None = None,
) -> dict[str, Any]:
    """Wrap a scenario result + its trace manifest into a persistable record.

    `status` comes from ScenarioResult.passed (assertion outcome), NOT the
    manifest's run_finished status (which only reflects internal completion).
    Tolerates a missing manifest (subprocess crashed before writing a trace).
    """
    manifest = result.trace_manifest or {}
    run_meta = manifest.get("run", {}) if isinstance(manifest, dict) else {}
    return {
        "schema_version": TRACE_RECORD_SCHEMA_VERSION,
        "run_id": str(run_meta.get("run_id") or scenario_id),
        "blueprint": str(run_meta.get("blueprint") or ""),
        "blueprint_version": str(run_meta.get("blueprint_version") or ""),
        "scenario_id": scenario_id,
        "status": "passed" if result.passed else "failed",
        "saved_at": saved_at or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "input": input,
        "failures": list(result.failures),
        "seed": run_meta.get("seed"),
        "manifest": manifest,
    }


def save_trace_record(record: dict[str, Any], *, store_dir: Path) -> Path:
    """Write a record to the store with a unique, filesystem-safe filename."""
    store_dir.mkdir(parents=True, exist_ok=True)
    stamp = str(record.get("saved_at", "")).replace("-", "").replace(":", "")
    stamp = _SAFE_ID_RE.sub("", stamp) or "unknown"
    name = f"{sanitize_run_id(str(record.get('run_id', 'run')))}-{stamp}-{uuid.uuid4().hex[:6]}.json"
    path = store_dir / name
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def list_trace_records(
    *,
    store_dir: Path,
    status: str = "all",
    blueprint: str | None = None,
) -> list[dict[str, Any]]:
    """Load records from the store, oldest first.

    Unparseable files and unknown schema versions are skipped, never fatal.
    """
    if not store_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(store_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get("schema_version") != TRACE_RECORD_SCHEMA_VERSION:
            continue
        if status != "all" and data.get("status") != status:
            continue
        if blueprint is not None and data.get("blueprint") != blueprint:
            continue
        records.append(data)
    records.sort(key=lambda record: str(record.get("saved_at", "")))
    return records


def build_case_from_record(record: dict[str, Any], *, golden: bool) -> dict[str, Any]:
    """Convert a stored record into an EvalCaseDef-shaped dict.

    Failed records get an EMPTY `expected` — the exported case fails until the
    blueprint is fixed (the TDD flywheel). With `golden=True` the expected
    block is filled from the trace (route + tools_called) to lock in current
    behavior as a regression case.
    """
    manifest = record.get("manifest", {}) or {}
    scenario_id = str(record.get("scenario_id", "case"))
    digest_source = f"{record.get('run_id', '')}{record.get('saved_at', '')}"
    suffix = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:8]

    expected: dict[str, Any] = {}
    if golden:
        route = _derive_route(manifest)
        if route is not None:
            expected["route"] = route
        tools_called = _derive_tools_called(manifest)
        if tools_called:
            expected["tools_called"] = tools_called

    failures = record.get("failures", [])
    metadata: dict[str, Any] = {
        "source_run_id": record.get("run_id"),
        "exported_from": "abp traces export",
        "original_status": record.get("status"),
    }
    if failures:
        metadata["error"] = "; ".join(str(item) for item in failures)[:500]
    if not golden:
        metadata["note"] = "expected intentionally empty; fill it or fix the blueprint until green"

    case: dict[str, Any] = {
        "id": f"{scenario_id}__{suffix}",
        "input": record.get("input", {}),
        "expected": expected,
        "llm_mode": "mock",
        "tool_mode": "stub",
        "fixtures": extract_replay_fixtures(manifest),
        "metadata": metadata,
    }
    if record.get("seed") is not None:
        case["seed"] = record["seed"]
    return case


def export_records_to_dataset(
    records: list[dict[str, Any]],
    *,
    output: Path,
    golden: bool,
) -> tuple[int, int]:
    """Merge records into a dataset file, deduplicating by case id.

    Existing cases are never modified (hand-edited `expected` blocks survive
    re-exports). Returns (added, skipped).
    """
    existing = _load_existing_cases(output)
    known_ids = {str(case.get("id")) for case in existing}

    added = 0
    skipped = 0
    for record in records:
        case = build_case_from_record(record, golden=golden)
        if case["id"] in known_ids:
            skipped += 1
            continue
        existing.append(case)
        known_ids.add(case["id"])
        added += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cases": existing}
    if output.suffix in {".json", ".jsonl"}:
        output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        with output.open("w", encoding="utf-8") as handle:
            yaml.dump(payload, handle)
    return added, skipped


def _load_existing_cases(output: Path) -> list[dict[str, Any]]:
    if not output.exists():
        return []
    text = output.read_text(encoding="utf-8")
    if not text.strip():
        return []
    data: Any
    if output.suffix in {".json", ".jsonl"}:
        data = json.loads(text)
    else:
        data = yaml.load(text)
    if isinstance(data, dict):
        cases = data.get("cases", data.get("eval_cases", []))
    else:
        cases = data
    if not isinstance(cases, list):
        return []
    return [dict(case) for case in cases if isinstance(case, dict)]


def _derive_route(manifest: dict[str, Any]) -> str | None:
    """Last node_finished node — mirrors the harness route assertion."""
    events = manifest.get("trace", []) if isinstance(manifest, dict) else []
    finished = [event for event in events if event.get("event") == "node_finished"]
    if not finished:
        return None
    node = finished[-1].get("node")
    return str(node) if node is not None else None


def _derive_tools_called(manifest: dict[str, Any]) -> list[str]:
    """Ordered tool_called tool names — mirrors the harness assertion."""
    events = manifest.get("trace", []) if isinstance(manifest, dict) else []
    return [
        str(event.get("tool"))
        for event in events
        if event.get("event") == "tool_called" and event.get("tool") is not None
    ]
