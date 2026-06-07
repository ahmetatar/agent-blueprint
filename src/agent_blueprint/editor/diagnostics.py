"""Lint findings with YAML source positions for the editor (phase E1).

Wraps the existing `linting` module and maps each finding's location path
(``graph.nodes.researcher``, ``graph.edges[2].to[1]``) to a 1-based
line/column in the blueprint source via ruamel's round-trip position data,
so the UI can render Monaco markers and node badges. Position mapping is
best-effort: an unmappable location simply yields ``line: null``.

Compile errors surface as a synthetic finding instead of raising — same
ordering the lint CLI gives users (compile problems before lint findings).
"""

import re
from typing import Any

from ruamel.yaml import YAML

from agent_blueprint.exceptions import BlueprintError
from agent_blueprint.ir.compiler import compile_blueprint
from agent_blueprint.linting import lint_blueprint
from agent_blueprint.models.blueprint import BlueprintSpec

_SEGMENT = re.compile(r"^([^\[\]]+)((?:\[\d+\])*)$")
_INDEX = re.compile(r"\[(\d+)\]")

_round_trip = YAML()  # round-trip mode keeps .lc position data


def lint_with_positions(spec: BlueprintSpec, raw_text: str) -> list[dict[str, Any]]:
    """Run compile + lint; findings carry line/col mapped from the raw source."""
    try:
        ir = compile_blueprint(spec)
    except BlueprintError as e:
        return [
            {
                "severity": "error",
                "code": "compile-error",
                "location": "",
                "message": str(e),
                "line": None,
                "col": None,
            }
        ]
    try:
        doc = _round_trip.load(raw_text)
    except Exception:  # position data is optional — never fail the endpoint
        doc = None
    findings = []
    for finding in lint_blueprint(spec, ir):
        line, col = locate_in_yaml(doc, finding.location)
        findings.append(
            {
                "severity": finding.severity.value,
                "code": finding.code,
                "location": finding.location,
                "message": finding.message,
                "line": line,
                "col": col,
            }
        )
    return findings


def locate_in_yaml(doc: Any, location: str) -> tuple[int | None, int | None]:
    """Best-effort (line, col), 1-based, of a dotted location path in a ruamel doc."""
    if doc is None or not location:
        return None, None
    pos: tuple[int, int] | None = None
    current = doc
    try:
        for segment in location.split("."):
            match = _SEGMENT.match(segment)
            if match is None:
                return None, None
            key = match.group(1)
            if not isinstance(current, dict) or key not in current or not hasattr(current, "lc"):
                return None, None
            pos = tuple(current.lc.key(key))
            current = current[key]
            for index_str in _INDEX.findall(match.group(2)):
                index = int(index_str)
                if (
                    not isinstance(current, list)
                    or index >= len(current)
                    or not hasattr(current, "lc")
                ):
                    return None, None
                pos = tuple(current.lc.item(index))
                current = current[index]
    except (KeyError, IndexError, TypeError, AttributeError):
        return None, None
    if pos is None:
        return None, None
    return pos[0] + 1, pos[1] + 1  # ruamel is 0-based; Monaco is 1-based
