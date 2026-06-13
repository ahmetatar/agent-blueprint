"""Tests that ruamel YAML parse errors surface as clean BlueprintValidationErrors."""

from pathlib import Path

import pytest

from agent_blueprint.exceptions import BlueprintValidationError
from agent_blueprint.utils.yaml_loader import load_blueprint_yaml


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "agent.yml"
    p.write_text(content, encoding="utf-8")
    return p


class TestYamlParseErrors:
    def test_bad_indentation_raises_blueprint_validation_error_with_location(self, tmp_path):
        # 'description' is mis-indented relative to 'name' — a block-end parse error.
        bad = (
            "blueprint:\n"
            "  name: growops\n"
            "    description: oops\n"
        )
        bp = _write(tmp_path, bad)
        with pytest.raises(BlueprintValidationError) as exc:
            load_blueprint_yaml(bp)
        msg = str(exc.value)
        assert "YAML syntax error" in msg
        assert "line" in msg and "column" in msg

    def test_unterminated_flow_mapping_is_wrapped(self, tmp_path):
        bp = _write(tmp_path, "blueprint: { name: growops\n")
        with pytest.raises(BlueprintValidationError) as exc:
            load_blueprint_yaml(bp)
        assert "YAML syntax error" in str(exc.value)

    def test_valid_yaml_still_loads(self, tmp_path):
        good = (
            "blueprint:\n"
            "  name: growops\n"
            "  version: '1.0'\n"
        )
        bp = _write(tmp_path, good)
        raw = load_blueprint_yaml(bp)
        assert raw["blueprint"]["name"] == "growops"
