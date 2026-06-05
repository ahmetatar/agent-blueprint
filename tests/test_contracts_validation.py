"""Tests for package-level output-contract validation."""

from agent_blueprint.contracts_validation import validate_output_contract
from agent_blueprint.models.contracts import OutputContractDef


def _contract(**kwargs) -> OutputContractDef:
    return OutputContractDef.model_validate(kwargs)


class TestValidateOutputContract:
    def test_valid_object_passes(self):
        contract = _contract(
            type="object",
            required=["route", "confidence"],
            properties={"route": {"type": "string"}, "confidence": {"type": "number"}},
        )
        violations = validate_output_contract({"route": "billing", "confidence": 0.9}, contract)
        assert violations == []

    def test_missing_required_reported(self):
        contract = _contract(
            type="object",
            required=["route", "confidence"],
            properties={"route": {"type": "string"}, "confidence": {"type": "number"}},
        )
        violations = validate_output_contract({"route": "billing"}, contract)
        assert violations == ["output.confidence is required"]

    def test_type_mismatch_reported(self):
        contract = _contract(
            type="object",
            properties={"confidence": {"type": "number"}},
        )
        violations = validate_output_contract({"confidence": "high"}, contract)
        assert violations == ["output.confidence must be of type 'number'"]

    def test_non_dict_against_object_contract(self):
        contract = _contract(type="object", required=["route"])
        violations = validate_output_contract("plain text", contract)
        assert violations == ["output must be of type 'object'"]

    def test_enum_violation_reported(self):
        contract = _contract(
            type="object",
            properties={"route": {"type": "string", "enum": ["billing", "support"]}},
        )
        violations = validate_output_contract({"route": "sales"}, contract)
        assert violations == ["output.route must be one of ['billing', 'support']"]

    def test_nested_properties_and_items(self):
        contract = _contract(
            type="object",
            required=["items"],
            properties={
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["name"],
                        "properties": {"name": {"type": "string"}},
                    },
                }
            },
        )
        violations = validate_output_contract(
            {"items": [{"name": "a"}, {"label": "b"}]}, contract
        )
        assert violations == ["output.items[1].name is required"]

    def test_additional_properties_false_rejects_extra(self):
        contract = _contract(
            type="object",
            properties={"route": {"type": "string"}},
            additionalProperties=False,
        )
        violations = validate_output_contract({"route": "billing", "debug": True}, contract)
        assert violations == ["output has unknown field(s): debug"]

    def test_nullable_allows_none(self):
        contract = _contract(
            type="object",
            properties={"note": {"type": "string", "nullable": True}},
        )
        assert validate_output_contract({"note": None}, contract) == []
        bare = _contract(type="object", properties={"note": {"type": "string"}})
        assert validate_output_contract({"note": None}, bare) == ["output.note cannot be null"]

    def test_multiple_violations_collected(self):
        contract = _contract(
            type="object",
            required=["route", "confidence"],
            properties={"route": {"type": "string"}, "confidence": {"type": "number"}},
            additionalProperties=False,
        )
        violations = validate_output_contract({"confidence": "high", "debug": 1}, contract)
        assert "output.route is required" in violations
        assert "output.confidence must be of type 'number'" in violations
        assert "output has unknown field(s): debug" in violations
