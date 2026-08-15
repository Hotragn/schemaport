"""Structured-output analyzers, driven through synthetic profiles.

The rules here are built in the test rather than read from the bundled dataset,
so a change to shipped contract data does not silently change what these
assert. The dataset is covered separately by test_contract_data.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from schemaport.analyzers import structured_output
from tests.support import make_rule, run
from tests.support import openai_response_format as openai_request


class TestWalk:
    def test_root_is_depth_one(self) -> None:
        nodes = list(structured_output.walk({"type": "object"}, "$"))
        assert [(node.path, node.depth) for node in nodes] == [("$", 1)]

    def test_properties_add_a_level(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "object", "properties": {}}}}
        depths = {node.path: node.depth for node in structured_output.walk(schema, "$")}
        assert depths == {"$": 1, "$.properties.a": 2}

    def test_array_items_add_a_level(self) -> None:
        schema = {"type": "array", "items": {"type": "object"}}
        depths = {node.path: node.depth for node in structured_output.walk(schema, "$")}
        assert depths["$.items"] == 2

    def test_combinator_branches_stay_at_the_same_level(self) -> None:
        schema = {"anyOf": [{"type": "string"}, {"type": "number"}]}
        depths = {node.path: node.depth for node in structured_output.walk(schema, "$")}
        assert depths["$.anyOf[0]"] == 1
        assert depths["$.anyOf[1]"] == 1

    def test_non_identifier_keys_are_bracket_quoted(self) -> None:
        schema = {"properties": {"order id": {"type": "string"}}}
        paths = [node.path for node in structured_output.walk(schema, "$")]
        assert "$.properties['order id']" in paths

    def test_a_self_referential_document_terminates(self) -> None:
        schema: dict[str, Any] = {"type": "object", "properties": {}}
        schema["properties"]["self"] = schema
        nodes = list(structured_output.walk(schema, "$"))
        assert len(nodes) == 1


class TestRootType:
    def test_flags_a_non_object_root(self) -> None:
        rule = make_rule(
            "structured_output.root_type",
            params={"expected_type": "object"},
            applies_to_kinds=("response_format",),
        )
        findings = run(openai_request({"type": "array"}), "openai.chat_completions", rule)
        assert [f.path for f in findings] == ["$.response_format.json_schema.schema"]
        assert "type 'array'" in (findings[0].detail or "")

    def test_accepts_an_object_root(self) -> None:
        rule = make_rule(
            "structured_output.root_type",
            params={"expected_type": "object"},
            applies_to_kinds=("response_format",),
        )
        assert not run(openai_request({"type": "object"}), "openai.chat_completions", rule)

    def test_reports_a_missing_type_keyword_distinctly(self) -> None:
        rule = make_rule("structured_output.root_type", params={"expected_type": "object"})
        findings = run(openai_request({"properties": {}}), "openai.chat_completions", rule)
        assert "no 'type' keyword" in (findings[0].detail or "")


class TestStrictGating:
    """A strict-mode rule must not fire on a schema that never opted in."""

    def test_rule_is_skipped_when_strict_is_absent(self) -> None:
        rule = make_rule(
            "structured_output.root_type",
            params={"expected_type": "object"},
            requires_strict=True,
        )
        request = openai_request({"type": "array"}, strict=False)
        assert not run(request, "openai.chat_completions", rule)

    def test_rule_fires_when_strict_is_set(self) -> None:
        rule = make_rule(
            "structured_output.root_type",
            params={"expected_type": "object"},
            requires_strict=True,
        )
        request = openai_request({"type": "array"}, strict=True)
        assert len(run(request, "openai.chat_completions", rule)) == 1

    def test_strict_not_enabled_reports_the_flag_location(self) -> None:
        rule = make_rule("structured_output.strict_not_enabled")
        request = openai_request({"type": "object"}, strict=False)
        findings = run(request, "openai.chat_completions", rule)
        assert [f.path for f in findings] == ["$.response_format.json_schema.strict"]


class TestAdditionalProperties:
    def test_flags_nested_objects_not_only_the_root(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"inner": {"type": "object", "properties": {}}},
        }
        rule = make_rule("structured_output.additional_properties", params={"expected": False})
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert [f.path for f in findings] == [
            "$.response_format.json_schema.schema.properties.inner"
        ]

    def test_flags_additional_properties_set_to_true(self) -> None:
        schema = {"type": "object", "additionalProperties": True, "properties": {}}
        rule = make_rule("structured_output.additional_properties", params={"expected": False})
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert "sets additionalProperties to True" in (findings[0].detail or "")


class TestRequiredCompleteness:
    def test_lists_the_missing_property_names(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["a"],
        }
        rule = make_rule("structured_output.required_completeness")
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert len(findings) == 1
        assert "'b'" in (findings[0].detail or "")

    def test_a_missing_required_array_counts_every_property(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        rule = make_rule("structured_output.required_completeness")
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert "1 property not in 'required'" in (findings[0].detail or "")

    def test_complete_required_produces_nothing(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}
        rule = make_rule("structured_output.required_completeness")
        assert not run(openai_request(schema), "openai.chat_completions", rule)


class TestKeywordAndLimitRules:
    def test_unsupported_keyword_points_at_the_keyword_itself(self) -> None:
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string", "pattern": "^a$"}},
        }
        rule = make_rule("structured_output.unsupported_keywords", params={"keywords": ["pattern"]})
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert [f.path for f in findings] == [
            "$.response_format.json_schema.schema.properties.status.pattern"
        ]

    def test_max_depth_reports_the_deepest_node(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "object", "properties": {"b": {"type": "object", "properties": {}}}}
            },
        }
        rule = make_rule("structured_output.max_depth", params={"limit": 2})
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert findings[0].path.endswith("properties.a.properties.b")
        assert "nests 3 levels deep" in (findings[0].detail or "")

    def test_max_depth_within_limit_is_silent(self) -> None:
        rule = make_rule("structured_output.max_depth", params={"limit": 5})
        assert not run(openai_request({"type": "object"}), "openai.chat_completions", rule)

    def test_total_object_properties_sums_across_the_whole_schema(self) -> None:
        """The documented limit is a total, so no single object need look large."""
        schema = {
            "type": "object",
            "properties": {
                "a": {"type": "object", "properties": {"x": {"type": "string"}}},
                "b": {"type": "object", "properties": {"y": {"type": "string"}}},
            },
        }
        rule = make_rule("structured_output.total_object_properties", params={"limit": 3})
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert "declares 4 properties in total" in (findings[0].detail or "")

    def test_total_object_properties_within_limit_is_silent(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "string"}}}
        rule = make_rule("structured_output.total_object_properties", params={"limit": 3})
        assert not run(openai_request(schema), "openai.chat_completions", rule)

    def test_total_enum_values_sums_every_enum(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"enum": ["x", "y"]}, "b": {"enum": ["p", "q"]}},
        }
        rule = make_rule("structured_output.total_enum_values", params={"limit": 3})
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert "4 enum values" in (findings[0].detail or "")

    def test_total_string_length_counts_names_and_literals(self) -> None:
        schema = {"type": "object", "properties": {"abcde": {"enum": ["fghij"]}}}
        rule = make_rule("structured_output.total_string_length", params={"limit_chars": 9})
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert "uses 10 characters" in (findings[0].detail or "")

    def test_large_enum_string_length_ignores_small_enums(self) -> None:
        schema = {"type": "object", "properties": {"a": {"enum": ["xxxxxxxxxx", "yyyyyyyyyy"]}}}
        rule = make_rule(
            "structured_output.large_enum_string_length",
            params={"value_threshold": 5, "limit_chars": 5},
        )
        assert not run(openai_request(schema), "openai.chat_completions", rule)

    def test_large_enum_string_length_fires_past_the_threshold(self) -> None:
        schema = {"type": "object", "properties": {"a": {"enum": [f"value{i}" for i in range(6)]}}}
        rule = make_rule(
            "structured_output.large_enum_string_length",
            params={"value_threshold": 5, "limit_chars": 10},
        )
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert findings[0].path.endswith("properties.a.enum")

    def test_enum_value_types_flags_non_scalars(self) -> None:
        schema = {"type": "object", "properties": {"a": {"enum": ["x", {"nested": 1}]}}}
        rule = make_rule(
            "structured_output.enum_value_types",
            params={"allowed": ["string", "integer", "number", "boolean", "null"]},
        )
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert "object value(s)" in (findings[0].detail or "")

    def test_enum_value_types_accepts_scalars(self) -> None:
        schema = {"type": "object", "properties": {"a": {"enum": ["x", 1, True, None]}}}
        rule = make_rule(
            "structured_output.enum_value_types",
            params={"allowed": ["string", "integer", "number", "boolean", "null"]},
        )
        assert not run(openai_request(schema), "openai.chat_completions", rule)

    def test_keyword_allowed_values(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "array", "minItems": 3}}}
        rule = make_rule(
            "structured_output.keyword_allowed_values",
            params={"keyword": "minItems", "allowed": [0, 1]},
        )
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert findings[0].path.endswith("minItems")
        assert "minItems is 3" in (findings[0].detail or "")

    def test_keyword_allowed_values_accepts_a_permitted_value(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "array", "minItems": 1}}}
        rule = make_rule(
            "structured_output.keyword_allowed_values",
            params={"keyword": "minItems", "allowed": [0, 1]},
        )
        assert not run(openai_request(schema), "openai.chat_completions", rule)

    def test_root_forbidden_keywords(self) -> None:
        schema = {"type": "object", "anyOf": [{"type": "object"}]}
        rule = make_rule(
            "structured_output.root_forbidden_keywords", params={"keywords": ["anyOf"]}
        )
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert findings[0].path == "$.response_format.json_schema.schema.anyOf"

    def test_root_forbidden_keywords_allows_the_keyword_when_nested(self) -> None:
        schema = {"type": "object", "properties": {"a": {"anyOf": [{"type": "string"}]}}}
        rule = make_rule(
            "structured_output.root_forbidden_keywords", params={"keywords": ["anyOf"]}
        )
        assert not run(openai_request(schema), "openai.chat_completions", rule)

    def test_external_ref(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "a": {"$ref": "https://example.test/s.json"},
                "b": {"$ref": "#/$defs/local"},
            },
            "$defs": {"local": {"type": "string"}},
        }
        rule = make_rule("structured_output.external_ref")
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        # `$ref` is not a dot-safe key, so the path builder quotes it.
        assert [f.path for f in findings] == [
            "$.response_format.json_schema.schema.properties.a['$ref']"
        ]

    def test_recursive_schema_detects_a_ref_to_an_ancestor(self) -> None:
        schema = {
            "type": "object",
            "properties": {"child": {"$ref": "#/properties/child"}},
        }
        rule = make_rule("structured_output.recursive_schema")
        findings = run(openai_request(schema), "openai.chat_completions", rule)
        assert findings
        assert "ancestor" in (findings[0].detail or "")

    def test_recursive_schema_detects_a_self_containing_document(self) -> None:
        schema: dict[str, Any] = {"type": "object", "properties": {}}
        schema["properties"]["self"] = schema
        request = openai_request(schema)
        rule = make_rule("structured_output.recursive_schema")
        findings = run(request, "openai.chat_completions", rule)
        assert findings
        assert "contains itself" in (findings[0].detail or "")

    def test_recursive_schema_leaves_a_plain_ref_alone(self) -> None:
        schema = {
            "type": "object",
            "properties": {"a": {"$ref": "#/$defs/node"}},
            "$defs": {"node": {"type": "string"}},
        }
        rule = make_rule("structured_output.recursive_schema")
        assert not run(openai_request(schema), "openai.chat_completions", rule)

    @pytest.mark.parametrize(
        "params",
        [{}, {"limit": "5"}, {"limit": True}],
        ids=["absent", "string", "bool"],
    )
    def test_a_limit_that_is_not_an_integer_disables_the_rule(self, params: dict[str, Any]) -> None:
        rule = make_rule("structured_output.max_depth", params=params)
        deep = {"type": "object", "properties": {"a": {"type": "object", "properties": {}}}}
        assert not run(openai_request(deep), "openai.chat_completions", rule)


class TestToolNames:
    def test_name_failing_the_pattern_is_reported_at_the_name(self, anthropic_request) -> None:
        anthropic_request["tools"][0]["name"] = "search orders"
        rule = make_rule("tool.name_pattern", params={"pattern": "^[a-zA-Z0-9_-]{1,64}$"})
        findings = run(anthropic_request, "anthropic.messages", rule)
        assert [f.path for f in findings] == ["$.tools[0].name"]

    def test_valid_name_produces_nothing(self, anthropic_request) -> None:
        rule = make_rule("tool.name_pattern", params={"pattern": "^[a-zA-Z0-9_-]{1,64}$"})
        assert not run(anthropic_request, "anthropic.messages", rule)

    def test_a_tool_without_a_name_is_reported(self, anthropic_request) -> None:
        del anthropic_request["tools"][0]["name"]
        rule = make_rule("tool.name_pattern", params={"pattern": "^.+$"})
        findings = run(anthropic_request, "anthropic.messages", rule)
        assert "no string 'name'" in (findings[0].detail or "")

    def test_length_is_checked_separately_from_the_pattern(self, anthropic_request) -> None:
        anthropic_request["tools"][0]["name"] = "a" * 80
        rule = make_rule("tool.name_pattern", params={"pattern": "^[a-z]+$", "max_length": 64})
        findings = run(anthropic_request, "anthropic.messages", rule)
        assert "is 80 characters" in (findings[0].detail or "")


class TestAppliesToKinds:
    def test_a_rule_only_sees_the_kinds_it_names(self, anthropic_request) -> None:
        rule = make_rule(
            "structured_output.root_type",
            params={"expected_type": "object"},
            applies_to_kinds=("response_format",),
        )
        anthropic_request["tools"][0]["input_schema"] = {"type": "string"}
        assert not run(anthropic_request, "anthropic.messages", rule)

    def test_an_empty_kind_list_means_every_kind(self, anthropic_request) -> None:
        rule = make_rule("structured_output.root_type", params={"expected_type": "object"})
        anthropic_request["tools"][0]["input_schema"] = {"type": "string"}
        assert len(run(anthropic_request, "anthropic.messages", rule)) == 1
