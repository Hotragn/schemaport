"""Structured-output conformance analyzers.

Each one walks the JSON Schemas the shape adapter located and compares them
against a threshold or keyword list supplied by the rule. None of them know
which provider they are checking.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from schemaport import paths
from schemaport.analyzers import AnalysisContext, register
from schemaport.contracts import Rule
from schemaport.model import Finding
from schemaport.shapes import KIND_RESPONSE_FORMAT, SchemaLocation

# Keywords whose values are themselves schemas. Walking these is what makes
# depth, keyword, and size checks see the whole document rather than its root.
_SUBSCHEMA_MAPS = ("properties", "patternProperties", "$defs", "definitions")
_SUBSCHEMA_LISTS = ("anyOf", "oneOf", "allOf", "prefixItems")
_SUBSCHEMA_VALUES = ("items", "additionalItems", "contains", "not", "additionalProperties")


@dataclass(frozen=True)
class SubSchema:
    """One schema node reached while walking a located schema."""

    path: str
    schema: Mapping[str, Any]
    depth: int
    recursive: bool = False


def walk(
    schema: Mapping[str, Any],
    base: str,
    depth: int = 1,
    *,
    include_repeats: bool = False,
) -> Iterator[SubSchema]:
    """Yield every subschema under `schema`, depth-first and in key order.

    Depth counts data nesting: the root is 1, a property's schema is 2. The
    boolean combinators do not add a level, because `anyOf` branches describe
    alternatives at the same position rather than a nested value.

    A document that contains itself is walked once. `include_repeats` yields the
    node where the cycle closes, marked `recursive`, for the rule that reports it.
    """
    yield from _walk(schema, base, depth, set(), include_repeats)


def _walk(
    schema: Mapping[str, Any],
    base: str,
    depth: int,
    seen: set[int],
    include_repeats: bool,
) -> Iterator[SubSchema]:
    identity = id(schema)
    if identity in seen:
        # Already on the stack: the document refers back into itself. Report the
        # closing node if asked, then stop rather than looping forever.
        if include_repeats:
            yield SubSchema(path=base, schema=schema, depth=depth, recursive=True)
        return
    seen.add(identity)
    yield SubSchema(path=base, schema=schema, depth=depth)

    for keyword in _SUBSCHEMA_MAPS:
        block = schema.get(keyword)
        if isinstance(block, Mapping):
            keyword_path = paths.child(base, keyword)
            for name, child in block.items():
                if isinstance(child, Mapping):
                    child_path = paths.child(keyword_path, name)
                    yield from _walk(child, child_path, depth + 1, seen, include_repeats)

    for keyword in _SUBSCHEMA_LISTS:
        block = schema.get(keyword)
        if isinstance(block, Sequence) and not isinstance(block, (str, bytes)):
            keyword_path = paths.child(base, keyword)
            for i, child in enumerate(block):
                if isinstance(child, Mapping):
                    child_path = paths.index(keyword_path, i)
                    yield from _walk(child, child_path, depth, seen, include_repeats)

    for keyword in _SUBSCHEMA_VALUES:
        child = schema.get(keyword)
        if isinstance(child, Mapping):
            # `items` and `contains` step into array elements, so they nest;
            # `not` and `additionalProperties` constrain the same position.
            step = 1 if keyword in ("items", "contains", "additionalItems") else 0
            child_path = paths.child(base, keyword)
            yield from _walk(child, child_path, depth + step, seen, include_repeats)

    seen.discard(identity)


def _locations(context: AnalysisContext, rule: Rule) -> Iterator[SchemaLocation]:
    """Schema locations this rule applies to."""
    for location in context.view.schemas:
        if not rule.targets_kind(location.kind):
            continue
        if rule.requires_strict and not location.strict:
            continue
        yield location


def _is_object_schema(schema: Mapping[str, Any]) -> bool:
    if isinstance(schema.get("properties"), Mapping):
        return True
    declared = schema.get("type")
    if declared == "object":
        return True
    if isinstance(declared, Sequence) and not isinstance(declared, (str, bytes)):
        return "object" in declared
    return False


@register("structured_output.root_type")
def root_type(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """The root of a located schema must declare a particular type."""
    expected = rule.params.get("expected_type", "object")
    for location in _locations(context, rule):
        declared = location.schema.get("type")
        if declared == expected:
            continue
        found = "no 'type' keyword" if declared is None else f"type {declared!r}"
        yield context.finding(
            rule,
            location.path,
            f"schema {location.label!r} declares {found}; expected {expected!r}",
        )


@register("structured_output.root_forbidden_keywords")
def root_forbidden_keywords(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag keywords the profile does not accept at the root of a schema.

    Distinct from the general keyword rule: a construct can be fine nested and
    still be rejected at the top level.
    """
    keywords = rule.params.get("keywords", [])
    if not isinstance(keywords, Sequence) or isinstance(keywords, (str, bytes)):
        return
    for location in _locations(context, rule):
        for keyword in (str(k) for k in keywords):
            if keyword in location.schema:
                yield context.finding(
                    rule,
                    paths.child(location.path, keyword),
                    f"{keyword!r} appears at the root of schema {location.label!r}",
                )


@register("structured_output.additional_properties")
def additional_properties(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Every object schema must pin `additionalProperties` to the expected value."""
    expected = rule.params.get("expected", False)
    for location in _locations(context, rule):
        for node in walk(location.schema, location.path):
            if not _is_object_schema(node.schema):
                continue
            if node.schema.get("additionalProperties") == expected:
                continue
            present = "additionalProperties" in node.schema
            detail = (
                f"object schema sets additionalProperties to "
                f"{node.schema['additionalProperties']!r}"
                if present
                else "object schema does not set additionalProperties"
            )
            yield context.finding(rule, node.path, detail)


@register("structured_output.required_completeness")
def required_completeness(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Every declared property must be listed in `required`."""
    for location in _locations(context, rule):
        for node in walk(location.schema, location.path):
            properties = node.schema.get("properties")
            if not isinstance(properties, Mapping) or not properties:
                continue
            declared = node.schema.get("required")
            required = (
                {item for item in declared if isinstance(item, str)}
                if isinstance(declared, Sequence) and not isinstance(declared, (str, bytes))
                else set()
            )
            missing = [name for name in properties if name not in required]
            if not missing:
                continue
            listed = ", ".join(repr(name) for name in missing)
            yield context.finding(
                rule,
                node.path,
                f"{len(missing)} propert{'y' if len(missing) == 1 else 'ies'} not in "
                f"'required': {listed}",
            )


@register("structured_output.unsupported_keywords")
def unsupported_keywords(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag schema keywords the profile marks as outside the supported subset."""
    keywords = rule.params.get("keywords", [])
    if not isinstance(keywords, Sequence) or isinstance(keywords, (str, bytes)):
        return
    watched = [str(keyword) for keyword in keywords]
    for location in _locations(context, rule):
        for node in walk(location.schema, location.path):
            for keyword in watched:
                if keyword in node.schema:
                    yield context.finding(
                        rule,
                        paths.child(node.path, keyword),
                        f"keyword {keyword!r} is present in schema {location.label!r}",
                    )


@register("structured_output.max_depth")
def max_depth(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag schemas nested deeper than the profile's limit."""
    limit = _int_param(rule, "limit")
    if limit is None:
        return
    for location in _locations(context, rule):
        deepest: SubSchema | None = None
        for node in walk(location.schema, location.path):
            if deepest is None or node.depth > deepest.depth:
                deepest = node
        if deepest is None or deepest.depth <= limit:
            continue
        yield context.finding(
            rule,
            deepest.path,
            f"schema {location.label!r} nests {deepest.depth} levels deep; limit is {limit}",
        )


@register("structured_output.total_object_properties")
def total_object_properties(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag a schema declaring more properties in total than the profile's limit.

    The limit this implements is a whole-schema total, not a per-object cap, so
    a schema can breach it without any single object looking unusual.
    """
    limit = _int_param(rule, "limit")
    if limit is None:
        return
    for location in _locations(context, rule):
        total = sum(
            len(node.schema["properties"])
            for node in walk(location.schema, location.path)
            if isinstance(node.schema.get("properties"), Mapping)
        )
        if total <= limit:
            continue
        yield context.finding(
            rule,
            location.path,
            f"schema {location.label!r} declares {total} properties in total; limit is {limit}",
        )


@register("structured_output.total_enum_values")
def total_enum_values(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag more enum values across the whole schema than the profile's limit."""
    limit = _int_param(rule, "limit")
    if limit is None:
        return
    for location in _locations(context, rule):
        total = sum(len(values) for _, values in _enums(location))
        if total <= limit:
            continue
        yield context.finding(
            rule,
            location.path,
            f"schema {location.label!r} declares {total} enum values across all enum "
            f"properties; limit is {limit}",
        )


@register("structured_output.total_string_length")
def total_string_length(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag the combined length of the schema's identifier and literal strings.

    Counts what the profile says the provider counts — property names,
    definition names, enum values, and const values — rather than the size of
    the serialised document, which would include punctuation the limit ignores.
    """
    limit = _int_param(rule, "limit_chars")
    if limit is None:
        return
    for location in _locations(context, rule):
        total = 0
        for node in walk(location.schema, location.path):
            for keyword in ("properties", "$defs", "definitions"):
                block = node.schema.get(keyword)
                if isinstance(block, Mapping):
                    total += sum(len(str(name)) for name in block)
            values = node.schema.get("enum")
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                total += sum(len(value) for value in values if isinstance(value, str))
            constant = node.schema.get("const")
            if isinstance(constant, str):
                total += len(constant)
        if total <= limit:
            continue
        yield context.finding(
            rule,
            location.path,
            f"schema {location.label!r} uses {total} characters across property names, "
            f"definition names, enum values, and const values; limit is {limit}",
        )


@register("structured_output.large_enum_string_length")
def large_enum_string_length(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag a single large enum whose values are too long in total.

    Applies only past a value-count threshold, because that is how the
    constraint is written: small enums are not subject to it.
    """
    threshold = _int_param(rule, "value_threshold")
    limit = _int_param(rule, "limit_chars")
    if threshold is None or limit is None:
        return
    for location in _locations(context, rule):
        for path, values in _enums(location):
            if len(values) <= threshold:
                continue
            length = sum(len(value) for value in values if isinstance(value, str))
            if length <= limit:
                continue
            yield context.finding(
                rule,
                path,
                f"enum of {len(values)} values totals {length} characters; a single enum "
                f"property with more than {threshold} values is limited to {limit}",
            )


@register("structured_output.enum_value_types")
def enum_value_types(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag enum entries whose type the profile does not accept."""
    allowed = rule.params.get("allowed", [])
    if not isinstance(allowed, Sequence) or isinstance(allowed, (str, bytes)):
        return
    permitted = {str(name) for name in allowed}
    for location in _locations(context, rule):
        for path, values in _enums(location):
            offenders = sorted({_json_type(value) for value in values} - permitted)
            if not offenders:
                continue
            yield context.finding(
                rule,
                path,
                f"enum contains {', '.join(offenders)} value(s); this profile accepts "
                f"{', '.join(sorted(permitted))}",
            )


@register("structured_output.keyword_allowed_values")
def keyword_allowed_values(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag a keyword carrying a value outside the set the profile accepts."""
    keyword = rule.params.get("keyword")
    allowed = rule.params.get("allowed")
    if not isinstance(keyword, str) or not isinstance(allowed, Sequence):
        return
    if isinstance(allowed, (str, bytes)):
        return
    permitted = list(allowed)
    for location in _locations(context, rule):
        for node in walk(location.schema, location.path):
            if keyword not in node.schema:
                continue
            value = node.schema[keyword]
            # Compare types too, so `True` does not satisfy an allowed `1`.
            if any(
                value == candidate and type(value) is type(candidate) for candidate in permitted
            ):
                continue
            yield context.finding(
                rule,
                paths.child(node.path, keyword),
                f"{keyword} is {value!r}; this profile accepts "
                f"{', '.join(repr(v) for v in permitted)}",
            )


@register("structured_output.external_ref")
def external_ref(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag `$ref` values that point outside the document."""
    for location in _locations(context, rule):
        for node in walk(location.schema, location.path):
            reference = node.schema.get("$ref")
            if not isinstance(reference, str) or reference.startswith("#"):
                continue
            yield context.finding(
                rule,
                paths.child(node.path, "$ref"),
                f"$ref points outside the document: {reference!r}",
            )


@register("structured_output.recursive_schema")
def recursive_schema(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag a schema that refers back into itself.

    Catches both shapes it can take in a rendered request: a `$ref` whose
    target is an ancestor of the node holding it, and a Python object graph
    that literally contains itself after being assembled in memory.
    """
    for location in _locations(context, rule):
        for node in walk(location.schema, location.path, include_repeats=True):
            if node.recursive:
                yield context.finding(rule, node.path, "the schema at this path contains itself")
                continue
            reference = node.schema.get("$ref")
            if not isinstance(reference, str) or not reference.startswith("#/"):
                continue
            target = _pointer_to_path(location.path, reference)
            if target is not None and node.path.startswith(target):
                yield context.finding(
                    rule,
                    paths.child(node.path, "$ref"),
                    f"$ref {reference!r} resolves to an ancestor of this node",
                )


def _enums(location: SchemaLocation) -> Iterator[tuple[str, Sequence[Any]]]:
    """Every `enum` array under a located schema, with its path."""
    for node in walk(location.schema, location.path):
        values = node.schema.get("enum")
        if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
            yield paths.child(node.path, "enum"), values


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence):
        return "array"
    return "unknown"


def _pointer_to_path(base: str, reference: str) -> str | None:
    """Translate a local JSON Pointer into the path syntax findings use."""
    path = base
    for token in reference[2:].split("/"):
        if not token:
            return None
        path = paths.child(path, token.replace("~1", "/").replace("~0", "~"))
    return path


@register("structured_output.strict_not_enabled")
def strict_not_enabled(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Flag a JSON-schema response format that did not opt into strict mode."""
    for location in context.view.schemas:
        if location.kind != KIND_RESPONSE_FORMAT or location.strict:
            continue
        yield context.finding(
            rule,
            paths.child(location.container_path, "strict"),
            f"response format {location.label!r} does not set strict to true",
        )


@register("tool.name_pattern")
def tool_name_pattern(context: AnalysisContext, rule: Rule) -> Iterator[Finding]:
    """Check tool names against the pattern and length the profile records."""
    pattern = rule.params.get("pattern")
    max_length = _int_param(rule, "max_length")
    compiled = re.compile(str(pattern)) if isinstance(pattern, str) else None

    for tool in context.view.tools:
        if tool.name is None:
            yield context.finding(rule, tool.name_path, "tool definition has no string 'name'")
            continue
        if compiled is not None and not compiled.fullmatch(tool.name):
            yield context.finding(
                rule,
                tool.name_path,
                f"tool name {tool.name!r} does not match {pattern!r}",
            )
            continue
        if max_length is not None and len(tool.name) > max_length:
            yield context.finding(
                rule,
                tool.name_path,
                f"tool name is {len(tool.name)} characters; limit is {max_length}",
            )


def _int_param(rule: Rule, key: str) -> int | None:
    value = rule.params.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
